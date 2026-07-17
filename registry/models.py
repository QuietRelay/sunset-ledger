import unicodedata

from django.core.exceptions import ValidationError
from django.db import models
from django.utils.text import slugify

from registry.services import system_fields

US_STATE_CHOICES = [
    ("AL", "Alabama"), ("AK", "Alaska"), ("AZ", "Arizona"), ("AR", "Arkansas"),
    ("CA", "California"), ("CO", "Colorado"), ("CT", "Connecticut"), ("DE", "Delaware"),
    ("DC", "District of Columbia"), ("FL", "Florida"), ("GA", "Georgia"), ("HI", "Hawaii"),
    ("ID", "Idaho"), ("IL", "Illinois"), ("IN", "Indiana"), ("IA", "Iowa"),
    ("KS", "Kansas"), ("KY", "Kentucky"), ("LA", "Louisiana"), ("ME", "Maine"),
    ("MD", "Maryland"), ("MA", "Massachusetts"), ("MI", "Michigan"), ("MN", "Minnesota"),
    ("MS", "Mississippi"), ("MO", "Missouri"), ("MT", "Montana"), ("NE", "Nebraska"),
    ("NV", "Nevada"), ("NH", "New Hampshire"), ("NJ", "New Jersey"), ("NM", "New Mexico"),
    ("NY", "New York"), ("NC", "North Carolina"), ("ND", "North Dakota"), ("OH", "Ohio"),
    ("OK", "Oklahoma"), ("OR", "Oregon"), ("PA", "Pennsylvania"), ("RI", "Rhode Island"),
    ("SC", "South Carolina"), ("SD", "South Dakota"), ("TN", "Tennessee"), ("TX", "Texas"),
    ("UT", "Utah"), ("VT", "Vermont"), ("VA", "Virginia"), ("WA", "Washington"),
    ("WV", "West Virginia"), ("WI", "Wisconsin"), ("WY", "Wyoming"),
]


class Jurisdiction(models.Model):
    """A governing body a Sunset Ledger agreement can belong to. Reference
    metadata (name, population, meeting schedule) is sourced loosely --
    unlike agreement/document/fact, it is not held to field-level
    citation rigor. See docs/schema-spec.md.

    Identity note: the (name, state, type) unique constraint below is a
    v1 *duplicate-prevention* heuristic for the data-entry/moderation
    workflow -- it catches "did someone already add this city" -- not a
    claim that those three fields are a universally correct canonical
    identity for a governing body. `fips_code` is the external identifier
    (Census/GNIS) for jurisdictions that have one; `slug` is this
    project's own stable internal identity (used in URLs and any future
    cross-reference), assigned once at creation and never recomputed from
    a later name change, so a renamed jurisdiction doesn't silently break
    inbound links or references from other records.
    """

    class JurisdictionType(models.TextChoices):
        CITY = "city", "City"
        COUNTY = "county", "County"
        HOA = "hoa", "HOA"
        UNIVERSITY = "university", "University"
        TRANSIT = "transit", "Transit authority"
        OTHER = "other", "Other"

    class AgendaPlatform(models.TextChoices):
        LEGISTAR = "legistar", "Legistar"
        GRANICUS = "granicus", "Granicus"
        CIVICPLUS = "civicplus", "CivicPlus"
        NOVUSAGENDA = "novusagenda", "NovusAGENDA"
        PRIMEGOV = "primegov", "PrimeGov"
        OTHER = "other", "Other"
        UNKNOWN = "unknown", "Unknown"

    name = models.CharField(max_length=200)
    slug = models.SlugField(max_length=220, unique=True, editable=False)
    type = models.CharField(max_length=16, choices=JurisdictionType.choices)
    state = models.CharField(max_length=2, choices=US_STATE_CHOICES)
    population = models.PositiveIntegerField(null=True, blank=True)
    governing_body_name = models.CharField(max_length=200, blank=True)
    meeting_schedule_text = models.TextField(blank=True)
    agenda_url = models.URLField(blank=True)
    records_request_contact = models.CharField(
        max_length=255, blank=True,
        help_text="Public-records request URL or email -- free text since either form is valid.",
    )
    fips_code = models.CharField(max_length=10, unique=True, null=True, blank=True)
    # Not read by anything in v1 -- reserved for the v2 agenda scraper.
    agenda_platform = models.CharField(
        max_length=16, choices=AgendaPlatform.choices, null=True, blank=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["name", "state", "type"], name="unique_jurisdiction_name_state_type",
            ),
        ]
        ordering = ["state", "name"]

    def __str__(self):
        return f"{self.name}, {self.state} ({self.get_type_display()})"

    def save(self, *args, **kwargs):
        if not self.slug:
            base = slugify(f"{self.name}-{self.state}") or "jurisdiction"
            candidate = base
            suffix = 2
            while Jurisdiction.objects.filter(slug=candidate).exclude(pk=self.pk).exists():
                candidate = f"{base}-{suffix}"
                suffix += 1
            self.slug = candidate
        super().save(*args, **kwargs)


class Vendor(models.Model):
    """Vendors are open data, not a closed enum -- rebrands and
    acquisitions (e.g. Motorola/Vigilant) are inserts, never migrations."""

    canonical_name = models.CharField(max_length=200, unique=True)
    website = models.URLField(blank=True)
    parent_company = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.SET_NULL, related_name="subsidiaries",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["canonical_name"]

    def __str__(self):
        return self.canonical_name


class VendorAlias(models.Model):
    """Alternate names a source document might use for a vendor (e.g.
    'Vigilant Solutions' for what is now Motorola Solutions), so
    normalization doesn't require lossy hardcoding.

    Uniqueness is enforced against `normalized_alias`, computed in Python
    at save() time -- not a database Lower() function. SQLite's built-in
    LOWER() is ASCII-only and Postgres's lower() does not perform full
    Unicode case folding, so relying on either for true Unicode-aware
    uniqueness would silently under-normalize (e.g. it would not fold a
    German 'ß' the same as 'ss', and would treat differently-composed
    but visually identical strings as distinct). Normalization is:
    Unicode NFKC normalization, then whitespace trimming, then casefold()
    (Unicode-aware, stronger than lower()).
    """

    vendor = models.ForeignKey(Vendor, on_delete=models.CASCADE, related_name="aliases")
    alias_text = models.CharField(max_length=200)
    normalized_alias = models.CharField(max_length=200, unique=True, editable=False)

    class Meta:
        ordering = ["alias_text"]

    def __str__(self):
        return f"{self.alias_text} -> {self.vendor.canonical_name}"

    @staticmethod
    def normalize(text: str) -> str:
        return unicodedata.normalize("NFKC", text).strip().casefold()

    def save(self, *args, **kwargs):
        self.normalized_alias = self.normalize(self.alias_text)
        super().save(*args, **kwargs)


class Reviewer(models.Model):
    """Stable internal identity for attributing verification/moderation/
    dispute-resolution actions. Not a public account -- no profile page,
    no login for the public, contact_email never exported. Provisioned
    manually by a maintainer."""

    class Role(models.TextChoices):
        MAINTAINER = "maintainer", "Maintainer"
        TRUSTED_REVIEWER = "trusted_reviewer", "Trusted reviewer"

    display_name = models.CharField(max_length=100)
    contact_email = models.EmailField(unique=True)
    role = models.CharField(max_length=20, choices=Role.choices)
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["display_name"]

    def __str__(self):
        return self.display_name


class FactFieldQuerySet(models.QuerySet):
    """Closes the bulk-operation gap that Model.save()/.delete() guards
    cannot cover: QuerySet.update(), QuerySet.delete(), bulk_create(), and
    bulk_update() all write directly via SQL and never call an instance's
    save()/delete(), so they bypass FactField's per-instance guard
    entirely unless intercepted here too.

    On Postgres this is defense in depth -- the RLS policies in the 0002
    migration already block app_runtime from writing machine rows via
    *any* SQL statement, bulk or not, regardless of what Django code
    generated it. On SQLite there is no equivalent database-level
    backstop at all, so this QuerySet is the *only* protection bulk
    operations have there -- it is load-bearing, not merely cosmetic, on
    that backend.
    """

    def _existing_machine_rows(self):
        return self.filter(category=FactField.Category.MACHINE).exists()

    def update(self, **kwargs):
        if not system_fields.mutation_allowed():
            escapes_into_machine = kwargs.get("category") == FactField.Category.MACHINE
            if self._existing_machine_rows() or escapes_into_machine:
                raise system_fields.SystemFieldMutationNotAllowed(
                    "QuerySet.update() cannot create or modify machine fact fields "
                    "outside a migration context."
                )
        return super().update(**kwargs)

    def delete(self):
        if not system_fields.mutation_allowed():
            if self._existing_machine_rows():
                raise system_fields.SystemFieldMutationNotAllowed(
                    "QuerySet.delete() cannot remove machine fact fields outside a "
                    "migration context."
                )
        return super().delete()

    def bulk_create(self, objs, **kwargs):
        objs = list(objs)
        if not system_fields.mutation_allowed():
            if any(o.category == FactField.Category.MACHINE for o in objs):
                raise system_fields.SystemFieldMutationNotAllowed(
                    "bulk_create() cannot create machine fact fields outside a migration "
                    "context."
                )
        return super().bulk_create(objs, **kwargs)

    def bulk_update(self, objs, fields, **kwargs):
        objs = list(objs)
        if not system_fields.mutation_allowed():
            in_memory_machine = (
                any(getattr(o, "category", None) == FactField.Category.MACHINE for o in objs)
                or "category" in fields
            )
            pks = [o.pk for o in objs if o.pk is not None]
            db_machine = bool(pks) and self.model.objects.filter(
                pk__in=pks, category=FactField.Category.MACHINE
            ).exists()
            if in_memory_machine or db_machine:
                raise system_fields.SystemFieldMutationNotAllowed(
                    "bulk_update() cannot modify machine fact fields outside a migration "
                    "context."
                )
        return super().bulk_update(objs, fields, **kwargs)


class FactField(models.Model):
    """The controlled vocabulary `fact` rows point at. `category=machine`
    rows are the frozen, migration-managed set that the deadline/tier
    engine reads by hardcoded machine_key -- see MACHINE_KEYS below and
    docs/schema-spec.md. `category=descriptive` rows are ordinary
    editorial reference data and are structurally barred from ever
    participating in deadline/tier computation.

    On Postgres, category='machine' rows are additionally protected by a
    row-level-security policy restricting the application's runtime role
    (see the 0002 migration). The guard here
    (registry.services.system_fields), together with FactFieldQuerySet
    above, is what gives SQLite the same protection and is defense in
    depth on Postgres.
    """

    class Category(models.TextChoices):
        MACHINE = "machine", "Machine"
        DESCRIPTIVE = "descriptive", "Descriptive"

    class ValueType(models.TextChoices):
        TEXT = "text", "Text"
        DATE = "date", "Date"
        NUMBER = "number", "Number"
        BOOL = "bool", "Boolean"

    # Frozen per docs/schema-spec.md. Extending this set is a schema
    # migration, deliberately, not a data change.
    MACHINE_KEYS = [
        "end_date",
        "start_date",
        "renewal_mechanism",
        "non_renewal_notice_days",
        "termination_for_convenience_notice_days",
        "renewal_term_length_days",
        "scheduled_vote_date",
        "cancellation_effective_date",
        "termination_effective_date",
    ]

    code = models.SlugField(max_length=64, unique=True)
    machine_key = models.CharField(max_length=64, unique=True, null=True, blank=True)
    category = models.CharField(max_length=16, choices=Category.choices)
    value_type = models.CharField(max_length=8, choices=ValueType.choices)
    is_period = models.BooleanField(
        default=False,
        help_text="If true, fact rows for this field carry a period_unit "
                   "(calendar_days/business_days/months/years).",
    )
    allows_multiple_concurrent = models.BooleanField(default=False)
    description = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    objects = FactFieldQuerySet.as_manager()

    class Meta:
        ordering = ["category", "code"]

    def __str__(self):
        return self.code

    def clean(self):
        if self.category == self.Category.MACHINE:
            if not self.machine_key:
                raise ValidationError("Machine fact fields must declare a machine_key.")
            if self.machine_key not in self.MACHINE_KEYS:
                raise ValidationError(
                    f"'{self.machine_key}' is not one of the frozen machine keys: "
                    f"{self.MACHINE_KEYS}. Adding a new one requires a schema migration."
                )
        elif self.machine_key:
            raise ValidationError("Descriptive fact fields must not set machine_key.")

    def _previous_category(self):
        if not self.pk:
            return None
        try:
            return type(self).objects.only("category").get(pk=self.pk).category
        except type(self).DoesNotExist:
            return None

    def _guard(self):
        previous_category = self._previous_category()
        if self.category == self.Category.MACHINE or previous_category == self.Category.MACHINE:
            if not system_fields.mutation_allowed():
                raise system_fields.SystemFieldMutationNotAllowed(
                    f"'{self.code}' is a migration-managed machine fact field. It cannot be "
                    "created, modified, or deleted outside a schema migration -- including "
                    "changing its category away from 'machine'."
                )

    def save(self, *args, **kwargs):
        self._guard()
        self.full_clean()
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        self._guard()
        super().delete(*args, **kwargs)
