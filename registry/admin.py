from django.contrib import admin

from .models import (
    Agreement,
    AgreementRelationship,
    Document,
    DocumentAgreement,
    DocumentRelationship,
    Fact,
    FactCorroboration,
    FactDispute,
    FactDisputeMember,
    FactField,
    FactFieldQualifier,
    Jurisdiction,
    RequiredFactSet,
    Reviewer,
    Vendor,
    VendorAlias,
    Verification,
    VerificationFact,
)


@admin.register(Jurisdiction)
class JurisdictionAdmin(admin.ModelAdmin):
    list_display = ("name", "state", "type", "population", "agenda_platform")
    list_filter = ("state", "type")
    search_fields = ("name", "governing_body_name", "fips_code")


class VendorAliasInline(admin.TabularInline):
    model = VendorAlias
    extra = 1


@admin.register(Vendor)
class VendorAdmin(admin.ModelAdmin):
    list_display = ("canonical_name", "parent_company", "website")
    search_fields = ("canonical_name",)
    inlines = [VendorAliasInline]


@admin.register(Reviewer)
class ReviewerAdmin(admin.ModelAdmin):
    list_display = ("display_name", "role", "active", "created_at")
    list_filter = ("role", "active")
    search_fields = ("display_name", "contact_email")


@admin.register(FactField)
class FactFieldAdmin(admin.ModelAdmin):
    list_display = ("code", "category", "machine_key", "value_type", "is_period",
                     "allows_multiple_concurrent")
    list_filter = ("category", "value_type")
    search_fields = ("code", "machine_key")

    def get_readonly_fields(self, request, obj=None):
        # Machine fields are migration-managed -- the model layer already
        # raises SystemFieldMutationNotAllowed on any attempted write, but
        # making every field read-only here means an editor sees a locked
        # form instead of a raised exception after filling one out.
        if obj is not None and obj.category == FactField.Category.MACHINE:
            return [f.name for f in obj._meta.fields]
        return []

    def has_delete_permission(self, request, obj=None):
        if obj is not None and obj.category == FactField.Category.MACHINE:
            return False
        return super().has_delete_permission(request, obj)


class NeverDeletableAdminMixin:
    """Agreement and Document raise RecordDeletionNotAllowed at the model
    layer regardless -- this just gives a consistent UI signal (no delete
    button/action at all) instead of a raised exception after the fact."""

    def has_delete_permission(self, request, obj=None):
        return False

    def get_actions(self, request):
        actions = super().get_actions(request)
        actions.pop("delete_selected", None)
        return actions


@admin.register(Agreement)
class AgreementAdmin(NeverDeletableAdminMixin, admin.ModelAdmin):
    list_display = ("__str__", "jurisdiction", "vendor", "agreement_type", "parent_agreement")
    list_filter = ("agreement_type", "jurisdiction__state")
    search_fields = ("jurisdiction__name", "vendor__canonical_name", "scope_note")
    autocomplete_fields = ("jurisdiction", "vendor", "parent_agreement")


@admin.register(AgreementRelationship)
class AgreementRelationshipAdmin(admin.ModelAdmin):
    list_display = ("from_agreement", "relationship_type", "to_agreement", "created_at")
    list_filter = ("relationship_type",)
    autocomplete_fields = ("from_agreement", "to_agreement")


class DocumentAgreementInline(admin.TabularInline):
    model = DocumentAgreement
    extra = 1
    autocomplete_fields = ("agreement",)


@admin.register(Document)
class DocumentAdmin(NeverDeletableAdminMixin, admin.ModelAdmin):
    list_display = (
        "__str__", "document_type", "date_obtained", "acquisition_method",
        "wayback_status", "unredacted_copy_status",
    )
    list_filter = ("document_type", "acquisition_method", "wayback_status", "unredacted_copy_status")
    search_fields = ("archived_storage_key", "content_sha256", "original_url")
    readonly_fields = (
        "wayback_status", "wayback_attempts", "wayback_last_attempt_at",
        "wayback_last_error", "wayback_url", "wayback_saved_at", "next_archive_attempt_at",
    )
    inlines = [DocumentAgreementInline]


@admin.register(DocumentRelationship)
class DocumentRelationshipAdmin(admin.ModelAdmin):
    list_display = ("from_document", "relationship_type", "to_document", "created_at")
    list_filter = ("relationship_type",)
    autocomplete_fields = ("from_document", "to_document")


@admin.register(DocumentAgreement)
class DocumentAgreementAdmin(admin.ModelAdmin):
    list_display = ("document", "agreement", "relationship_role", "created_at")
    list_filter = ("relationship_role",)
    autocomplete_fields = ("document", "agreement")


@admin.register(FactFieldQualifier)
class FactFieldQualifierAdmin(admin.ModelAdmin):
    list_display = ("field", "qualifier_key", "qualifier_description")
    list_filter = ("field",)
    search_fields = ("field__code", "qualifier_key")
    autocomplete_fields = ("field",)


class FactCorroborationInline(admin.TabularInline):
    model = FactCorroboration
    fk_name = "fact"
    extra = 1
    autocomplete_fields = ("document",)


@admin.register(Fact)
class FactAdmin(NeverDeletableAdminMixin, admin.ModelAdmin):
    list_display = (
        "__str__", "agreement", "field", "qualifier", "status",
        "valid_from", "valid_until", "effective_date_basis",
    )
    list_filter = ("status", "field", "effective_date_basis")
    search_fields = (
        "agreement__jurisdiction__name", "agreement__vendor__canonical_name",
        "field__code", "excerpt",
    )
    autocomplete_fields = (
        "agreement", "field", "qualifier", "primary_document", "supersedes",
        "created_by", "retracted_by",
    )
    inlines = [FactCorroborationInline]

    def get_readonly_fields(self, request, obj=None):
        # A fact's core identity/value fields are conceptually immutable
        # once recorded (corrections supersede, they don't edit in place)
        # -- only the fields legitimately involved in a status transition
        # (retraction) stay editable on an existing row.
        if obj is None:
            return []
        editable = {"status", "retraction_reason", "retracted_by", "retracted_at"}
        return [f.name for f in obj._meta.fields if f.name not in editable and f.name != "id"]


class FactDisputeMemberInline(admin.TabularInline):
    model = FactDisputeMember
    extra = 1
    autocomplete_fields = ("fact",)


@admin.register(FactDispute)
class FactDisputeAdmin(NeverDeletableAdminMixin, admin.ModelAdmin):
    list_display = ("__str__", "status", "opened_by", "opened_at", "resolved_by", "resolved_at")
    list_filter = ("status", "field")
    autocomplete_fields = ("agreement", "field", "qualifier", "opened_by", "resolved_by")
    inlines = [FactDisputeMemberInline]


class VerificationFactInline(admin.TabularInline):
    model = VerificationFact
    extra = 1
    autocomplete_fields = ("fact",)


@admin.register(Verification)
class VerificationAdmin(NeverDeletableAdminMixin, admin.ModelAdmin):
    list_display = ("__str__", "agreement", "verified_by", "verified_at")
    list_filter = ("verified_by",)
    autocomplete_fields = ("agreement", "verified_by")
    inlines = [VerificationFactInline]


@admin.register(RequiredFactSet)
class RequiredFactSetAdmin(admin.ModelAdmin):
    list_display = ("purpose", "field", "required_when_field", "required_when_value")
    list_filter = ("purpose",)
    autocomplete_fields = ("field", "required_when_field")
