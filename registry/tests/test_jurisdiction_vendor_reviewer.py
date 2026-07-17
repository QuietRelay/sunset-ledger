import pytest
from django.db import IntegrityError, transaction

from registry.models import Jurisdiction, Reviewer, Vendor, VendorAlias

pytestmark = pytest.mark.django_db


def test_jurisdiction_create():
    j = Jurisdiction.objects.create(
        name="Cleveland", type=Jurisdiction.JurisdictionType.CITY, state="OH",
        governing_body_name="Cleveland City Council",
    )
    assert str(j) == "Cleveland, OH (City)"


def test_jurisdiction_name_state_type_unique():
    Jurisdiction.objects.create(name="Springfield", type=Jurisdiction.JurisdictionType.CITY, state="OH")
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            Jurisdiction.objects.create(name="Springfield", type=Jurisdiction.JurisdictionType.CITY, state="OH")
    # A same-named city in a different state is a different jurisdiction.
    Jurisdiction.objects.create(name="Springfield", type=Jurisdiction.JurisdictionType.CITY, state="IN")


def test_jurisdiction_fips_code_unique_allows_multiple_nulls():
    Jurisdiction.objects.create(name="A", type=Jurisdiction.JurisdictionType.CITY, state="OH")
    Jurisdiction.objects.create(name="B", type=Jurisdiction.JurisdictionType.CITY, state="OH")
    # Two jurisdictions with no fips_code at all must not collide.
    assert Jurisdiction.objects.filter(fips_code__isnull=True).count() == 2


def test_jurisdiction_slug_is_assigned_and_stable_across_renames():
    j = Jurisdiction.objects.create(name="Fort Wayne", type=Jurisdiction.JurisdictionType.CITY, state="IN")
    assert j.slug == "fort-wayne-in"
    j.name = "Fort Wayne (renamed)"
    j.save()
    j.refresh_from_db()
    # The slug is the project's stable internal identity -- it must not
    # silently change just because the display name did.
    assert j.slug == "fort-wayne-in"


def test_jurisdiction_slug_conflict_gets_a_stable_suffix():
    a = Jurisdiction.objects.create(name="Springfield", type=Jurisdiction.JurisdictionType.CITY, state="OH")
    b = Jurisdiction.objects.create(name="Springfield", type=Jurisdiction.JurisdictionType.COUNTY, state="OH")
    assert a.slug != b.slug
    assert b.slug == "springfield-oh-2"


def test_vendor_and_alias_normalization():
    flock = Vendor.objects.create(canonical_name="Flock Safety")
    motorola = Vendor.objects.create(canonical_name="Motorola Solutions")
    VendorAlias.objects.create(vendor=motorola, alias_text="Vigilant Solutions")
    assert VendorAlias.objects.get(alias_text="Vigilant Solutions").vendor == motorola
    assert flock.aliases.count() == 0


def test_vendor_alias_unique_case_insensitive():
    vendor = Vendor.objects.create(canonical_name="Rekor Systems")
    VendorAlias.objects.create(vendor=vendor, alias_text="Rekor")
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            VendorAlias.objects.create(vendor=vendor, alias_text="REKOR")


def test_vendor_alias_unique_ignores_surrounding_whitespace():
    vendor = Vendor.objects.create(canonical_name="Genetec")
    VendorAlias.objects.create(vendor=vendor, alias_text="Genetec Inc")
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            VendorAlias.objects.create(vendor=vendor, alias_text="  Genetec Inc  ")


def test_vendor_alias_unique_across_unicode_equivalent_forms():
    # Precomposed U+00E9 ("e with acute accent" as a single codepoint)
    # versus the decomposed form (plain "e", U+0065, followed by a
    # combining acute accent, U+0301) render identically but are
    # different byte sequences. They must collide after NFKC
    # normalization -- exactly the case bare lower()/LOWER() would not
    # reliably catch (SQLite's LOWER() is ASCII-only; Postgres's lower()
    # does not perform full Unicode case folding either). Built from
    # explicit escapes, not typed glyphs, so the two forms are
    # unambiguously distinct regardless of source encoding.
    precomposed = "Vigilanté"
    decomposed = "Vigilant" + "é"
    assert precomposed != decomposed  # sanity check: genuinely different strings
    assert len(precomposed) != len(decomposed)
    vendor = Vendor.objects.create(canonical_name="Some Vendor")
    VendorAlias.objects.create(vendor=vendor, alias_text=precomposed)
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            VendorAlias.objects.create(vendor=vendor, alias_text=decomposed)


def test_vendor_parent_company_relationship():
    parent = Vendor.objects.create(canonical_name="Motorola Solutions")
    subsidiary = Vendor.objects.create(canonical_name="Vigilant Solutions (legacy entity)",
                                        parent_company=parent)
    assert subsidiary in parent.subsidiaries.all()


def test_reviewer_contact_email_unique():
    Reviewer.objects.create(display_name="A. Maintainer", contact_email="a@example.org",
                             role=Reviewer.Role.MAINTAINER)
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            Reviewer.objects.create(display_name="Duplicate", contact_email="a@example.org",
                                     role=Reviewer.Role.TRUSTED_REVIEWER)
