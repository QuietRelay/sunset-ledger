from django.contrib import admin

from .models import FactField, Jurisdiction, Reviewer, Vendor, VendorAlias


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
