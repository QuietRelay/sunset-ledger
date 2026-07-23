import unicodedata
from datetime import date

from django.core.exceptions import ValidationError
from django.core.validators import RegexValidator
from django.db import models
from django.utils.text import slugify

from registry.services import integrity, system_fields

SHA256_HEX_VALIDATOR = RegexValidator(
    regex=r"^[a-f0-9]{64}$",
    message="Must be a 64-character lowercase hexadecimal SHA-256 digest.",
)

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


class AgreementQuerySet(models.QuerySet):
    """See registry.services.integrity -- agreements are never
    hard-deleted in v1, on either the instance or the bulk path."""

    def delete(self):
        raise integrity.RecordDeletionNotAllowed(
            "Agreements are never hard-deleted in v1 -- see docs/schema-spec.md."
        )


class Agreement(models.Model):
    """One distinct contractual/procurement instrument -- not "this
    jurisdiction's relationship with this vendor". A jurisdiction can have
    multiple simultaneous or sequential agreements with the same vendor;
    each is its own row.

    Deliberately holds no mutable contract terms (dates, prices, camera
    counts, renewal clauses) -- those are facts, not built yet. Also
    deliberately holds no `status`: status is derived from facts
    (cancellation_effective_date, termination_effective_date, end_date,
    start_date) that don't exist until the fact slice, so there is
    nothing yet to derive it from. See docs/schema-spec.md.
    """

    class AgreementType(models.TextChoices):
        MASTER_AGREEMENT = "master_agreement", "Master agreement"
        PARTICIPATING_AGREEMENT = "participating_agreement", "Participating agreement"
        STANDALONE_AGREEMENT = "standalone_agreement", "Standalone agreement"
        TASK_ORDER = "task_order", "Task order"

    jurisdiction = models.ForeignKey(Jurisdiction, on_delete=models.PROTECT, related_name="agreements")
    vendor = models.ForeignKey(Vendor, on_delete=models.PROTECT, related_name="agreements")
    agreement_type = models.CharField(max_length=24, choices=AgreementType.choices)
    parent_agreement = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.PROTECT, related_name="participating_agreements",
    )
    scope_note = models.TextField(
        blank=True,
        help_text="Short factual description of what this instrument covers, "
                   "e.g. 'ALPR cameras, Police Dept'.",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = AgreementQuerySet.as_manager()

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=models.Q(parent_agreement__isnull=True) | ~models.Q(parent_agreement=models.F("id")),
                name="agreement_parent_not_self",
            ),
        ]
        indexes = [
            models.Index(fields=["jurisdiction", "vendor"], name="agreement_juris_vendor_idx"),
        ]
        ordering = ["jurisdiction", "vendor", "id"]

    def __str__(self):
        return f"{self.jurisdiction} / {self.vendor} ({self.get_agreement_type_display()})"

    def clean(self):
        # Parent policy by type: master and standalone agreements are
        # never themselves a child; a participating agreement always
        # participates *in* a master; a task order may optionally sit
        # under anything (master, standalone, or another task order).
        if self.agreement_type in (self.AgreementType.MASTER_AGREEMENT, self.AgreementType.STANDALONE_AGREEMENT):
            if self.parent_agreement_id:
                raise ValidationError(
                    f"A {self.agreement_type} cannot itself have a parent_agreement."
                )
        elif self.agreement_type == self.AgreementType.PARTICIPATING_AGREEMENT:
            if not self.parent_agreement_id:
                raise ValidationError("A participating_agreement must have a parent_agreement.")
            if self.parent_agreement.agreement_type != self.AgreementType.MASTER_AGREEMENT:
                raise ValidationError(
                    "A participating_agreement's parent_agreement must be a master_agreement."
                )

        if self.parent_agreement_id:
            self._check_no_hierarchy_cycle()

    def _check_no_hierarchy_cycle(self):
        # CheckConstraint('agreement_parent_not_self') only catches
        # direct self-parenting -- a cycle several links deep (A -> B ->
        # C -> A) needs an actual traversal, since neither SQLite nor
        # Postgres can express "no cycle in this self-referential FK" as
        # a portable row-local CHECK constraint.
        seen = {self.pk} if self.pk else set()
        node = self.parent_agreement
        depth = 0
        while node is not None:
            depth += 1
            if node.pk in seen or depth > 100:
                raise ValidationError("This parent_agreement chain forms a cycle.")
            seen.add(node.pk)
            node = node.parent_agreement

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise integrity.RecordDeletionNotAllowed(
            "Agreements are never hard-deleted in v1 -- see docs/schema-spec.md."
        )


class AgreementRelationship(models.Model):
    """Non-hierarchical relationships between agreements. Hierarchical
    master/participating relationships use Agreement.parent_agreement
    instead -- this table is for everything else."""

    class RelationshipType(models.TextChoices):
        REPLACES = "replaces", "Replaces"
        RENEWED_AS_NEW_PROCUREMENT = "renewed_as_new_procurement", "Renewed as new procurement"
        RELATED_DEPARTMENT_ARRANGEMENT = "related_department_arrangement", "Related department arrangement"
        CONSOLIDATED_FROM = "consolidated_from", "Consolidated from"

    from_agreement = models.ForeignKey(
        Agreement, on_delete=models.PROTECT, related_name="outgoing_relationships",
    )
    to_agreement = models.ForeignKey(
        Agreement, on_delete=models.PROTECT, related_name="incoming_relationships",
    )
    relationship_type = models.CharField(max_length=32, choices=RelationshipType.choices)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=~models.Q(from_agreement=models.F("to_agreement")),
                name="agree_rel_no_self_link",
            ),
            models.UniqueConstraint(
                fields=["from_agreement", "to_agreement", "relationship_type"],
                name="uniq_agree_rel_directional",
            ),
        ]

    def __str__(self):
        return f"{self.from_agreement} -{self.get_relationship_type_display()}-> {self.to_agreement}"

    def clean(self):
        if self.from_agreement_id and self.from_agreement_id == self.to_agreement_id:
            raise ValidationError("An agreement cannot have a relationship to itself.")

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)


class DocumentQuerySet(models.QuerySet):
    """See registry.services.integrity -- documents are never
    hard-deleted in v1, on either the instance or the bulk path."""

    def delete(self):
        raise integrity.RecordDeletionNotAllowed(
            "Documents are never hard-deleted in v1 -- see docs/schema-spec.md."
        )


class Document(models.Model):
    """An archived artifact (contract, amendment, agenda packet, etc.).

    Before any fact cites this document, its core identity fields are
    ordinarily editable -- admin-based data entry benefits from that
    while a record is still being assembled. Once a fact cites it (as
    `primary_document` or via `FactCorroboration`), `archived_storage_key`,
    `content_sha256`, `file_size_bytes`, and `mime_type` become locked
    (see `_LOCKED_ONCE_CITED_FIELDS` and `clean()` below): a document a
    published fact points to must keep meaning the same archived bytes.
    Corrections after that point create a new Document linked via
    DocumentRelationship(corrected_version_of) instead of editing this row
    in place. `wayback_*` fields are exempt -- they're expected to change
    repeatedly via the future archive_sources retry command regardless of
    citation state.

    `content_sha256` is intentionally NOT unique -- identical hashes are
    permitted and resolved editorially via DocumentRelationship(duplicate_of),
    never rejected at insert. See docs/schema-spec.md.
    """

    _LOCKED_ONCE_CITED_FIELDS = ("archived_storage_key", "content_sha256", "file_size_bytes", "mime_type")

    class DocumentType(models.TextChoices):
        ORIGINAL_AGREEMENT = "original_agreement", "Original agreement"
        AMENDMENT = "amendment", "Amendment"
        EXTENSION = "extension", "Extension"
        PURCHASE_ORDER = "purchase_order", "Purchase order"
        COUNCIL_APPROVAL = "council_approval", "Council approval"
        AGENDA_PACKET = "agenda_packet", "Agenda packet"
        MINUTES = "minutes", "Minutes"
        INVOICE = "invoice", "Invoice"
        RECORDS_RESPONSE = "records_response", "Records response"
        NEWS_ARTICLE = "news_article", "News article"

    class AcquisitionMethod(models.TextChoices):
        PUBLIC_RECORDS_REQUEST = "public_records_request", "Public records request"
        AGENDA_PACKET = "agenda_packet", "Agenda packet"
        NEWS_COVERAGE = "news_coverage", "News coverage"
        VENDOR_PRESS_RELEASE = "vendor_press_release", "Vendor press release"
        DIRECT_SUBMISSION = "direct_submission", "Direct submission"
        OTHER = "other", "Other"

    class RedactionPerformedBy(models.TextChoices):
        SOURCE_AGENCY = "source_agency", "Source agency"
        PROJECT = "project", "Project"
        UNKNOWN = "unknown", "Unknown"

    class UnredactedCopyStatus(models.TextChoices):
        NOT_APPLICABLE = "not_applicable", "Not applicable"
        RETAINED_RESTRICTED = "retained_restricted", "Retained (restricted)"
        DELETED = "deleted", "Deleted"

    class WaybackStatus(models.TextChoices):
        PENDING = "pending", "Pending"
        SUCCEEDED = "succeeded", "Succeeded"
        FAILED = "failed", "Failed"

    # --- core identity / archival ---
    document_type = models.CharField(max_length=24, choices=DocumentType.choices)
    original_url = models.URLField(blank=True)
    archived_storage_key = models.CharField(max_length=500, unique=True)
    content_sha256 = models.CharField(max_length=64, validators=[SHA256_HEX_VALIDATOR])
    file_size_bytes = models.PositiveBigIntegerField()
    mime_type = models.CharField(max_length=100)
    document_date = models.DateField(null=True, blank=True)
    date_obtained = models.DateField()
    acquisition_method = models.CharField(max_length=24, choices=AcquisitionMethod.choices)

    # --- distinct dates for retroactive-effect handling (see fact.effective_date_basis, future slice) ---
    vote_or_approval_date = models.DateField(null=True, blank=True)
    execution_date = models.DateField(null=True, blank=True)
    stated_effective_date = models.DateField(null=True, blank=True)

    # --- privacy / redaction (frozen retention rule) ---
    contains_personal_info = models.BooleanField(default=False)
    redaction_performed_by = models.CharField(
        max_length=16, choices=RedactionPerformedBy.choices, null=True, blank=True,
    )
    redaction_note = models.TextField(blank=True)
    unredacted_copy_status = models.CharField(
        max_length=24, choices=UnredactedCopyStatus.choices, default=UnredactedCopyStatus.NOT_APPLICABLE,
    )
    unredacted_copy_sha256 = models.CharField(
        max_length=64, null=True, blank=True, validators=[SHA256_HEX_VALIDATOR],
    )
    unredacted_copy_deleted_at = models.DateTimeField(null=True, blank=True)
    unredacted_retention_reason = models.TextField(blank=True)
    unredacted_access_policy = models.CharField(max_length=255, blank=True)

    # --- archival retry metadata ---
    wayback_status = models.CharField(max_length=16, choices=WaybackStatus.choices, default=WaybackStatus.PENDING)
    wayback_attempts = models.PositiveIntegerField(default=0)
    wayback_last_attempt_at = models.DateTimeField(null=True, blank=True)
    wayback_last_error = models.TextField(blank=True)
    wayback_url = models.URLField(blank=True)
    wayback_saved_at = models.DateTimeField(null=True, blank=True)
    next_archive_attempt_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = DocumentQuerySet.as_manager()

    class Meta:
        indexes = [
            models.Index(fields=["content_sha256"], name="document_sha256_idx"),
            models.Index(fields=["wayback_status", "next_archive_attempt_at"], name="document_archive_retry_idx"),
            models.Index(fields=["document_date"], name="document_document_date_idx"),
            models.Index(fields=["date_obtained"], name="document_date_obtained_idx"),
        ]
        ordering = ["-date_obtained", "id"]

    def __str__(self):
        return f"{self.get_document_type_display()} ({self.content_sha256[:12]}...)"

    def clean(self):
        if self.redaction_performed_by == self.RedactionPerformedBy.PROJECT and not self.redaction_note:
            raise ValidationError(
                "redaction_note is required when redaction_performed_by='project'."
            )
        if (
            self.unredacted_copy_status == self.UnredactedCopyStatus.RETAINED_RESTRICTED
            and not self.unredacted_retention_reason
        ):
            raise ValidationError(
                "unredacted_retention_reason is required when "
                "unredacted_copy_status='retained_restricted'."
            )
        self._check_locked_fields_unchanged_if_cited()

    def _is_cited_by_a_fact(self):
        if not self.pk:
            return False
        return self.primary_facts.exists() or self.corroborated_facts.exists()

    def _check_locked_fields_unchanged_if_cited(self):
        if not self._is_cited_by_a_fact():
            return
        previous = type(self).objects.get(pk=self.pk)
        for field_name in self._LOCKED_ONCE_CITED_FIELDS:
            if getattr(self, field_name) != getattr(previous, field_name):
                raise ValidationError(
                    f"'{field_name}' cannot change: this document is cited by a fact. "
                    "Create a new Document and link it via "
                    "DocumentRelationship(corrected_version_of) instead."
                )

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise integrity.RecordDeletionNotAllowed(
            "Documents are never hard-deleted in v1 -- see docs/schema-spec.md."
        )


class DocumentRelationship(models.Model):
    """Relationships between documents. `from` reads as the subject,
    `to` as the object -- see each type's docstring line for exact
    directionality:

    - redacted_version_of:  from = the redacted copy, to = the original
    - corrected_version_of: from = the corrected copy, to = the earlier version
    - ocr_derived_from:     from = the OCR text/output, to = the source scan
    - duplicate_of:         from = the duplicate, to = the canonical copy
    - replacement_for:      from = the new replacement, to = the old copy
    - attachment_to:        from = the attachment, to = the main document
    """

    class RelationshipType(models.TextChoices):
        REDACTED_VERSION_OF = "redacted_version_of", "Redacted version of"
        CORRECTED_VERSION_OF = "corrected_version_of", "Corrected version of"
        OCR_DERIVED_FROM = "ocr_derived_from", "OCR derived from"
        DUPLICATE_OF = "duplicate_of", "Duplicate of"
        REPLACEMENT_FOR = "replacement_for", "Replacement for"
        ATTACHMENT_TO = "attachment_to", "Attachment to"

    from_document = models.ForeignKey(
        Document, on_delete=models.PROTECT, related_name="outgoing_relationships",
    )
    to_document = models.ForeignKey(
        Document, on_delete=models.PROTECT, related_name="incoming_relationships",
    )
    relationship_type = models.CharField(max_length=24, choices=RelationshipType.choices)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=~models.Q(from_document=models.F("to_document")),
                name="doc_rel_no_self_link",
            ),
            models.UniqueConstraint(
                fields=["from_document", "to_document", "relationship_type"],
                name="uniq_doc_rel_directional",
            ),
        ]

    def __str__(self):
        return f"{self.from_document_id} -{self.get_relationship_type_display()}-> {self.to_document_id}"

    def clean(self):
        if self.from_document_id and self.from_document_id == self.to_document_id:
            raise ValidationError("A document cannot have a relationship to itself.")

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)


class DocumentAgreement(models.Model):
    """Many-to-many link between documents and agreements. One document
    may support multiple agreements (e.g. a countywide master agreement
    covering several participating agreements' own documentation trail).

    This is NOT field-level provenance -- it only says "this document is
    associated with this agreement," nothing about which specific claims
    it supports. Field-level citation is fact.primary_document_id.

    Known v1 limitation, accepted rather than expanded now: exactly one
    row per (document, agreement) pair, so a document playing genuinely
    multiple roles for the same agreement (e.g. both the governing
    instrument and the pricing schedule) has to pick one role, not
    record both. Revisit if that turns out to matter in practice.
    """

    class RelationshipRole(models.TextChoices):
        GOVERNING_INSTRUMENT = "governing_instrument", "Governing instrument"
        AMENDMENT = "amendment", "Amendment"
        APPROVAL_RECORD = "approval_record", "Approval record"
        PRICING_SCHEDULE = "pricing_schedule", "Pricing schedule"
        SUPPORTING_RECORD = "supporting_record", "Supporting record"

    document = models.ForeignKey(Document, on_delete=models.PROTECT, related_name="agreement_links")
    agreement = models.ForeignKey(Agreement, on_delete=models.PROTECT, related_name="document_links")
    relationship_role = models.CharField(
        max_length=24, choices=RelationshipRole.choices, null=True, blank=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["document", "agreement"], name="unique_document_agreement_pair"),
        ]

    def __str__(self):
        return f"{self.document} <-> {self.agreement}"

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)


class FactFieldQualifier(models.Model):
    """A controlled qualifier scoped to one specific FactField -- e.g.
    'year_2' or 'option_b' only make sense for particular fields, not as
    a global vocabulary. Ordinary admin-manageable reference data in v1,
    same as descriptive FactField rows; not given the machine-field
    RLS/guard treatment even when the parent field is category=machine,
    since nothing yet reads a specific qualifier_key by hardcoded name
    the way the deadline engine will read machine_key. Revisit if/when
    it does.
    """

    field = models.ForeignKey(FactField, on_delete=models.PROTECT, related_name="qualifiers")
    qualifier_key = models.SlugField(max_length=64)
    qualifier_description = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["field", "qualifier_key"], name="uniq_qualifier_per_field"),
        ]
        ordering = ["field", "qualifier_key"]

    def __str__(self):
        return f"{self.field.code}:{self.qualifier_key}"

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)


class FactQuerySet(models.QuerySet):
    """See registry.services.integrity -- facts are never hard-deleted;
    retract them instead."""

    def delete(self):
        raise integrity.RecordDeletionNotAllowed(
            "Facts are never hard-deleted in v1 -- retract them instead. See docs/schema-spec.md."
        )

    def operative_at(self, agreement, field, as_of, qualifier=None):
        """The fact whose validity interval covers `as_of` for
        (agreement, field[, qualifier]) -- resolves "current value" by
        valid_from/valid_until, not by status. Excludes disputed/
        retracted/proposed_option facts. A query helper for temporal
        resolution, not deadline arithmetic -- callers needing an actual
        notice-deadline computation are the future deadlines engine, not
        this method.
        """
        qs = self.filter(
            agreement=agreement, field=field, qualifier=qualifier,
            status=Fact.Status.ACTIVE, valid_from__lte=as_of,
        ).filter(models.Q(valid_until__isnull=True) | models.Q(valid_until__gt=as_of))
        return qs.order_by("-valid_from").first()


class Fact(integrity.UndeletableModelMixin, models.Model):
    """The atomic material term. Never overwritten -- a change creates a
    new row with `supersedes` pointing at the one it replaces; the old
    row's `valid_until` is set to the new row's `valid_from`, and it
    keeps status='active' (supersession is a temporal concern, resolved
    by valid_from/valid_until, not a status value -- a superseded fact is
    still a true historical assertion, just not currently operative).

    Exactly one of value_text/value_number/value_date/value_bool is
    populated, matching field.value_type -- enforced both by a portable
    CHECK constraint (the "exactly one" part) and by clean() (the
    "matches field.value_type" part, which needs a join CHECK can't do
    portably).
    """

    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        DISPUTED = "disputed", "Disputed"
        RETRACTED = "retracted", "Retracted"
        PROPOSED_OPTION = "proposed_option", "Proposed option"

    class PeriodUnit(models.TextChoices):
        CALENDAR_DAYS = "calendar_days", "Calendar days"
        BUSINESS_DAYS = "business_days", "Business days"
        MONTHS = "months", "Months"
        YEARS = "years", "Years"

    class EffectiveDateBasis(models.TextChoices):
        STATED_IN_DOCUMENT = "stated_in_document", "Stated in document"
        EXECUTION_DATE = "execution_date", "Execution date"
        APPROVAL_DATE = "approval_date", "Approval date"
        UNSPECIFIED_DEFAULTED = "unspecified_defaulted", "Unspecified (defaulted)"

    class PricingBasis(models.TextChoices):
        TOTAL_CONTRACT_VALUE = "total_contract_value", "Total contract value"
        ANNUAL_RECURRING = "annual_recurring", "Annual recurring"
        MONTHLY_RECURRING = "monthly_recurring", "Monthly recurring"
        ONE_TIME = "one_time", "One time"

    agreement = models.ForeignKey(Agreement, on_delete=models.PROTECT, related_name="facts")
    field = models.ForeignKey(FactField, on_delete=models.PROTECT, related_name="facts")
    qualifier = models.ForeignKey(
        FactFieldQualifier, on_delete=models.PROTECT, null=True, blank=True, related_name="facts",
    )
    scope_period_start = models.DateField(null=True, blank=True)
    scope_period_end = models.DateField(null=True, blank=True)

    value_text = models.TextField(null=True, blank=True)
    value_number = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    value_date = models.DateField(null=True, blank=True)
    value_bool = models.BooleanField(null=True, blank=True)

    period_unit = models.CharField(max_length=16, choices=PeriodUnit.choices, null=True, blank=True)
    currency = models.CharField(max_length=3, null=True, blank=True, help_text="ISO 4217, e.g. USD.")
    pricing_basis = models.CharField(max_length=24, choices=PricingBasis.choices, null=True, blank=True)

    valid_from = models.DateField()
    valid_until = models.DateField(null=True, blank=True)
    recorded_at = models.DateTimeField(auto_now_add=True)

    effective_date_basis = models.CharField(max_length=24, choices=EffectiveDateBasis.choices)

    status = models.CharField(max_length=16, choices=Status.choices, default=Status.ACTIVE)
    supersedes = models.ForeignKey(
        "self", on_delete=models.PROTECT, null=True, blank=True, related_name="superseded_by",
    )
    retraction_reason = models.TextField(blank=True)
    retracted_by = models.ForeignKey(
        Reviewer, on_delete=models.PROTECT, null=True, blank=True, related_name="facts_retracted",
    )
    retracted_at = models.DateTimeField(null=True, blank=True)

    primary_document = models.ForeignKey(Document, on_delete=models.PROTECT, related_name="primary_facts")
    page_or_section_reference = models.CharField(max_length=255, blank=True)
    excerpt = models.TextField(blank=True)

    objects = FactQuerySet.as_manager()

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=(
                    (models.Q(value_text__isnull=False) & models.Q(value_number__isnull=True)
                     & models.Q(value_date__isnull=True) & models.Q(value_bool__isnull=True))
                    | (models.Q(value_text__isnull=True) & models.Q(value_number__isnull=False)
                       & models.Q(value_date__isnull=True) & models.Q(value_bool__isnull=True))
                    | (models.Q(value_text__isnull=True) & models.Q(value_number__isnull=True)
                       & models.Q(value_date__isnull=False) & models.Q(value_bool__isnull=True))
                    | (models.Q(value_text__isnull=True) & models.Q(value_number__isnull=True)
                       & models.Q(value_date__isnull=True) & models.Q(value_bool__isnull=False))
                ),
                name="fact_value_exactly_one",
            ),
            models.CheckConstraint(
                condition=models.Q(valid_until__isnull=True) | models.Q(valid_until__gt=models.F("valid_from")),
                name="fact_valid_until_after_from",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(scope_period_start__isnull=True) | models.Q(scope_period_end__isnull=True)
                    | models.Q(scope_period_end__gte=models.F("scope_period_start"))
                ),
                name="fact_scope_period_order",
            ),
            # Split in two, like RequiredFactSet's rule constraint: standard
            # SQL treats NULL as never equal to NULL, so a single combined
            # UniqueConstraint would silently fail to dedupe the common
            # case (no qualifier, no scope period -- true of every
            # machine field today) since qualifier/scope_period_start/
            # scope_period_end are all nullable. Splitting on whether
            # qualifier is set covers that case and the qualifier-
            # differentiated-concurrency case correctly; a fact with
            # qualifier=NULL but only one of scope_period_start/end set is
            # a narrow, documented residual gap, not expected in practice
            # since every allows_multiple_concurrent field in this schema
            # differentiates by qualifier.
            models.UniqueConstraint(
                fields=["agreement", "field", "valid_from"],
                condition=models.Q(
                    status="active", qualifier__isnull=True,
                    scope_period_start__isnull=True, scope_period_end__isnull=True,
                ),
                name="uniq_active_fact_bare",
            ),
            models.UniqueConstraint(
                fields=["agreement", "field", "qualifier", "scope_period_start", "scope_period_end", "valid_from"],
                condition=models.Q(status="active", qualifier__isnull=False),
                name="uniq_active_fact_qualified",
            ),
        ]
        indexes = [
            models.Index(fields=["agreement", "field"], name="fact_agreement_field_idx"),
            models.Index(fields=["valid_from", "valid_until"], name="fact_validity_idx"),
            models.Index(fields=["status"], name="fact_status_idx"),
        ]
        ordering = ["agreement", "field", "-valid_from"]

    def __str__(self):
        return f"{self.agreement} / {self.field.code} (valid_from={self.valid_from})"

    def clean(self):
        self._check_exactly_one_value_matches_type()
        self._check_qualifier_belongs_to_field()
        self._check_effective_date_basis_against_document_dates()
        self._check_retraction_reason_required()
        self._check_no_illegitimate_concurrency()
        self._check_supersession_target_consistency()

    def _check_exactly_one_value_matches_type(self):
        values = dict(
            text=self.value_text, number=self.value_number, date=self.value_date, bool=self.value_bool,
        )
        populated = [name for name, value in values.items() if value is not None]
        if len(populated) != 1:
            raise ValidationError(
                "Exactly one of value_text/value_number/value_date/value_bool must be set."
            )
        expected = {
            FactField.ValueType.TEXT: "text", FactField.ValueType.DATE: "date",
            FactField.ValueType.NUMBER: "number", FactField.ValueType.BOOL: "bool",
        }[self.field.value_type]
        if populated[0] != expected:
            raise ValidationError(
                f"field.value_type='{self.field.value_type}' requires value_{expected} to be set, "
                f"not value_{populated[0]}."
            )

    def _check_qualifier_belongs_to_field(self):
        if self.qualifier_id and self.qualifier.field_id != self.field_id:
            raise ValidationError("qualifier must belong to this fact's own field.")

    def _check_effective_date_basis_against_document_dates(self):
        comparison_date = self.primary_document.execution_date or self.primary_document.vote_or_approval_date
        if (
            comparison_date
            and self.valid_from < comparison_date
            and self.effective_date_basis != self.EffectiveDateBasis.STATED_IN_DOCUMENT
        ):
            raise ValidationError(
                "valid_from may only precede the primary document's execution/approval date "
                "when effective_date_basis='stated_in_document'."
            )

    def _check_retraction_reason_required(self):
        if self.status == self.Status.RETRACTED and not self.retraction_reason:
            raise ValidationError("retraction_reason is required when status='retracted'.")

    def _check_no_illegitimate_concurrency(self):
        if self.field.allows_multiple_concurrent or self.status != self.Status.ACTIVE:
            return
        others = Fact.objects.filter(agreement=self.agreement, field=self.field, status=self.Status.ACTIVE)
        if self.pk:
            others = others.exclude(pk=self.pk)
        this_end = self.valid_until  # None == open-ended
        for other in others:
            other_end = other.valid_until
            if other.pk == self.supersedes_id:
                # This fact is about to close out `other`'s window as
                # part of supersession (_close_out_superseded_fact runs
                # after save()) -- treat it as already closed here, or a
                # legitimate supersession would look like an illegitimate
                # overlap purely because of save() ordering.
                other_end = self.valid_from
            overlaps = self.valid_from < (other_end or date.max) and other.valid_from < (this_end or date.max)
            if overlaps:
                raise ValidationError(
                    f"'{self.field.code}' does not allow concurrent active facts, and this "
                    f"fact's validity window overlaps an existing one (id={other.pk})."
                )

    def _check_supersession_target_consistency(self):
        # Validated pre-save so a conflicting chain is rejected before
        # this fact is ever written, rather than discovered only after
        # (and left half-applied) by the post-save close-out step.
        if self.supersedes_id and self.supersedes.valid_until is not None:
            if self.supersedes.valid_until != self.valid_from:
                raise ValidationError(
                    "supersedes target already has a different valid_until set -- "
                    "resolve the conflicting chain manually before superseding it."
                )

    def _close_out_superseded_fact(self):
        if not self.supersedes_id:
            return
        old = self.supersedes
        if old.valid_until is None:
            old.valid_until = self.valid_from
            old.save()

    def save(self, *args, **kwargs):
        self.full_clean()
        is_new = self._state.adding
        super().save(*args, **kwargs)
        if is_new:
            self._close_out_superseded_fact()


class FactCorroboration(models.Model):
    """A document that independently corroborates a fact, distinct from
    the fact's single `primary_document`. Additive, not a replacement --
    a fact's primary source is one document with a page/section
    reference; corroboration is optional, additional support."""

    fact = models.ForeignKey(Fact, on_delete=models.PROTECT, related_name="corroborations")
    document = models.ForeignKey(Document, on_delete=models.PROTECT, related_name="corroborated_facts")
    note = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["fact", "document"], name="uniq_fact_corroboration"),
        ]

    def __str__(self):
        return f"{self.document} corroborates {self.fact}"

    def clean(self):
        if self.document_id and self.fact_id and self.document_id == self.fact.primary_document_id:
            raise ValidationError(
                "A document already cited as this fact's primary_document is redundant as corroboration."
            )

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)


class FactDispute(integrity.UndeletableModelMixin, models.Model):
    """An explicitly opened disagreement over a specific (agreement,
    field[, qualifier]) -- created deliberately by a reviewer who has
    noticed two documents genuinely contradict each other, not inferred
    automatically from multiple active facts existing (qualifiers and
    scope periods make multiplicity normal; a dispute is for the
    non-normal case). Never hard-deleted -- a dispute and how it was
    resolved is itself part of the audit trail."""

    class Status(models.TextChoices):
        OPEN = "open", "Open"
        RESOLVED_FAVORS_ONE = "resolved_favors_one", "Resolved (favors one)"
        RESOLVED_BOTH_PARTIALLY_CORRECT = "resolved_both_partially_correct", "Resolved (both partially correct)"
        RESOLVED_UNRESOLVED = "resolved_unresolved", "Resolved (left unresolved)"

    agreement = models.ForeignKey(Agreement, on_delete=models.PROTECT, related_name="fact_disputes")
    field = models.ForeignKey(FactField, on_delete=models.PROTECT, related_name="disputes")
    qualifier = models.ForeignKey(
        FactFieldQualifier, on_delete=models.PROTECT, null=True, blank=True, related_name="disputes",
    )
    status = models.CharField(max_length=32, choices=Status.choices, default=Status.OPEN)
    opened_at = models.DateTimeField(auto_now_add=True)
    opened_by = models.ForeignKey(Reviewer, on_delete=models.PROTECT, related_name="disputes_opened")
    resolution_note = models.TextField(blank=True)
    resolved_at = models.DateTimeField(null=True, blank=True)
    resolved_by = models.ForeignKey(
        Reviewer, on_delete=models.PROTECT, null=True, blank=True, related_name="disputes_resolved",
    )

    objects = integrity.UndeletableQuerySet.as_manager()

    class Meta:
        indexes = [
            models.Index(fields=["agreement", "field"], name="fact_dispute_agree_field_idx"),
        ]

    def __str__(self):
        return f"Dispute: {self.agreement} / {self.field.code} ({self.status})"

    def clean(self):
        if self.status != self.Status.OPEN and not self.resolution_note:
            raise ValidationError("resolution_note is required once a dispute leaves 'open' status.")

    def save(self, *args, **kwargs):
        self.full_clean()
        previous_status = None
        if self.pk:
            previous_status = type(self).objects.only("status").get(pk=self.pk).status
        super().save(*args, **kwargs)
        if previous_status == self.Status.OPEN and self.status != self.Status.OPEN:
            self._apply_resolution_to_member_facts()

    def _apply_resolution_to_member_facts(self):
        # Verification rows on any of these facts are never touched here
        # -- they remain a permanent record of what was checked and when,
        # independent of this dispute's outcome.
        for member in self.members.select_related("fact"):
            if member.outcome == FactDisputeMember.Outcome.UPHELD:
                member.fact.status = Fact.Status.ACTIVE
                member.fact.save()
            # REJECTED (or still UNDER_REVIEW at resolution time) stays
            # disputed permanently -- the dispute's own resolution_note
            # is that fact's lasting justification.


class FactDisputeMember(integrity.UndeletableModelMixin, models.Model):
    """One fact caught up in a dispute. Linking a fact here while its
    dispute is open puts that fact into status='disputed' -- excluding
    it from the active-uniqueness constraint and from ever being read as
    the current value until the dispute resolves."""

    class Outcome(models.TextChoices):
        UNDER_REVIEW = "under_review", "Under review"
        UPHELD = "upheld", "Upheld"
        REJECTED = "rejected", "Rejected"

    dispute = models.ForeignKey(FactDispute, on_delete=models.PROTECT, related_name="members")
    fact = models.ForeignKey(Fact, on_delete=models.PROTECT, related_name="dispute_memberships")
    position_note = models.TextField(blank=True)
    outcome = models.CharField(max_length=16, choices=Outcome.choices, default=Outcome.UNDER_REVIEW)
    created_at = models.DateTimeField(auto_now_add=True)

    objects = integrity.UndeletableQuerySet.as_manager()

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["dispute", "fact"], name="uniq_dispute_member"),
        ]

    def __str__(self):
        return f"{self.fact} in {self.dispute}"

    def clean(self):
        if self.fact_id and self.dispute_id and self.fact.agreement_id != self.dispute.agreement_id:
            raise ValidationError("A dispute's member facts must belong to the dispute's own agreement.")
        if self.fact_id and self.dispute_id and self.fact.field_id != self.dispute.field_id:
            raise ValidationError("A dispute's member facts must belong to the dispute's own field.")
        if self.fact_id and self.dispute_id and self.fact.qualifier_id != self.dispute.qualifier_id:
            raise ValidationError("A dispute's member facts must match the dispute's own qualifier.")

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)
        if self.dispute.status == FactDispute.Status.OPEN and self.fact.status != Fact.Status.DISPUTED:
            self.fact.status = Fact.Status.DISPUTED
            self.fact.save()


class Verification(integrity.UndeletableModelMixin, models.Model):
    """A review event covering a specific set of facts as of a point in
    time -- never the agreement as a whole. Permanent: a verification
    record is never invalidated by a later dispute or supersession on the
    facts it covered, only superseded in *relevance* (see
    VerificationFact / docs/schema-spec.md's tier-derivation rules,
    implemented in the future deadline/tier slice, not here)."""

    agreement = models.ForeignKey(Agreement, on_delete=models.PROTECT, related_name="verifications")
    verified_by = models.ForeignKey(Reviewer, on_delete=models.PROTECT, related_name="verifications_performed")
    verified_at = models.DateTimeField(auto_now_add=True)
    what_was_checked = models.TextField(blank=True)

    objects = integrity.UndeletableQuerySet.as_manager()

    class Meta:
        indexes = [
            models.Index(fields=["agreement"], name="verification_agreement_idx"),
        ]

    def __str__(self):
        return f"Verification of {self.agreement} by {self.verified_by} at {self.verified_at}"


class VerificationFact(integrity.UndeletableModelMixin, models.Model):
    """Which exact fact rows a verification covers. A verification's
    facts must all belong to the verification's own agreement --
    enforced in clean() so a reviewer cannot accidentally attach a fact
    from an unrelated agreement to this verification event. There is no
    portable database constraint for this (it requires joining through
    fact to agreement, which a row-local CHECK cannot express), so this
    is application-level validation only -- documented, not silently
    assumed equivalent to a DB guarantee."""

    verification = models.ForeignKey(Verification, on_delete=models.PROTECT, related_name="covered_facts")
    fact = models.ForeignKey(Fact, on_delete=models.PROTECT, related_name="verification_links")
    created_at = models.DateTimeField(auto_now_add=True)

    objects = integrity.UndeletableQuerySet.as_manager()

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["verification", "fact"], name="uniq_verification_fact"),
        ]

    def __str__(self):
        return f"{self.fact} verified by {self.verification.verified_by}"

    def clean(self):
        if self.fact_id and self.verification_id and self.fact.agreement_id != self.verification.agreement_id:
            raise ValidationError(
                "A verification cannot cover a fact from a different agreement than the "
                "verification's own agreement."
            )

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)


class RequiredFactSet(models.Model):
    """Declarative membership in a required-fact set, used by the future
    deadline/tier engine to compute deadline_confidence and
    record_completeness -- not implemented here, only the data these
    computations will read. `required_when_*` makes a requirement
    conditional (e.g. a notice-period field is only required when
    renewal_mechanism equals a specific value)."""

    class Purpose(models.TextChoices):
        DEADLINE_CONFIDENCE = "deadline_confidence", "Deadline confidence"
        RECORD_COMPLETENESS = "record_completeness", "Record completeness"

    purpose = models.CharField(max_length=24, choices=Purpose.choices)
    field = models.ForeignKey(FactField, on_delete=models.PROTECT, related_name="required_in_sets")
    required_when_field = models.ForeignKey(
        FactField, on_delete=models.PROTECT, null=True, blank=True, related_name="+",
    )
    required_when_value = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            # A single UniqueConstraint across all four fields would not
            # actually dedupe unconditional rules: standard SQL treats
            # NULL as never equal to NULL, so two rows both having
            # required_when_field=NULL would not collide under one
            # combined constraint. Split into two partial constraints so
            # each case is deduped on the fields that are actually
            # meaningful for it.
            models.UniqueConstraint(
                fields=["purpose", "field"],
                condition=models.Q(required_when_field__isnull=True),
                name="uniq_required_fact_set_unconditional",
            ),
            models.UniqueConstraint(
                fields=["purpose", "field", "required_when_field", "required_when_value"],
                condition=models.Q(required_when_field__isnull=False),
                name="uniq_required_fact_set_conditional",
            ),
        ]

    def __str__(self):
        condition = f" when {self.required_when_field.code}={self.required_when_value}" if self.required_when_field_id else ""
        return f"{self.purpose}: {self.field.code}{condition}"

    def clean(self):
        if bool(self.required_when_field_id) != bool(self.required_when_value):
            raise ValidationError(
                "required_when_field and required_when_value must be set together, or not at all."
            )

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)
