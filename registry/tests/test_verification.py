import datetime
import uuid

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction

from registry.models import Fact, Reviewer, Verification, VerificationFact
from registry.services.integrity import RecordDeletionNotAllowed

pytestmark = pytest.mark.django_db


def make_fact(agreement, field, document, **overrides):
    overrides.setdefault("created_by", Reviewer.objects.create(
        display_name=f"Auto reviewer {uuid.uuid4()}",
        contact_email=f"auto-{uuid.uuid4()}@example.org",
        role=Reviewer.Role.TRUSTED_REVIEWER,
    ))
    defaults = dict(
        agreement=agreement, field=field, primary_document=document,
        valid_from=datetime.date(2024, 1, 1),
        effective_date_basis=Fact.EffectiveDateBasis.STATED_IN_DOCUMENT,
        value_date=datetime.date(2026, 1, 1),
    )
    defaults.update(overrides)
    return Fact.objects.create(**defaults)


def test_verification_covers_specific_facts(agreement, end_date_field, document, reviewer):
    fact = make_fact(agreement, end_date_field, document)
    verification = Verification.objects.create(
        agreement=agreement, verified_by=reviewer, what_was_checked="Confirmed end date against page 2.",
    )
    link = VerificationFact.objects.create(verification=verification, fact=fact)
    assert link in verification.covered_facts.all()
    assert link in fact.verification_links.all()


def test_verification_cannot_cover_a_fact_from_a_different_agreement(
    agreement, other_agreement, end_date_field, document, reviewer,
):
    verification = Verification.objects.create(agreement=agreement, verified_by=reviewer)
    foreign_fact = make_fact(other_agreement, end_date_field, document)
    with pytest.raises(ValidationError):
        VerificationFact.objects.create(verification=verification, fact=foreign_fact)


def test_verification_cannot_cover_a_fact_from_a_different_agreement_enforced_by_database(
    agreement, other_agreement, end_date_field, document, reviewer,
):
    # Bypasses full_clean() via bulk_create() -- this is intentionally
    # application-level only (no portable DB constraint can join across
    # verification -> fact -> agreement), so this test documents that
    # limitation rather than proving a DB-level guarantee that doesn't
    # exist here.
    verification = Verification.objects.create(agreement=agreement, verified_by=reviewer)
    foreign_fact = make_fact(other_agreement, end_date_field, document)
    # No IntegrityError here -- there is no database constraint for this,
    # by design (see docs/schema-spec.md). The row is created successfully,
    # which is exactly why the application-level guard above is required.
    VerificationFact._base_manager.bulk_create([
        VerificationFact(verification=verification, fact=foreign_fact)
    ])
    assert VerificationFact.objects.filter(verification=verification, fact=foreign_fact).exists()


def test_duplicate_verification_fact_link_rejected(agreement, end_date_field, document, reviewer):
    fact = make_fact(agreement, end_date_field, document)
    verification = Verification.objects.create(agreement=agreement, verified_by=reviewer)
    VerificationFact.objects.create(verification=verification, fact=fact)
    with pytest.raises(ValidationError):
        VerificationFact.objects.create(verification=verification, fact=fact)


def test_duplicate_verification_fact_link_rejected_by_database_independent_of_validation(
    agreement, end_date_field, document, reviewer,
):
    fact = make_fact(agreement, end_date_field, document)
    verification = Verification.objects.create(agreement=agreement, verified_by=reviewer)
    VerificationFact._base_manager.bulk_create([VerificationFact(verification=verification, fact=fact)])
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            VerificationFact._base_manager.bulk_create([VerificationFact(verification=verification, fact=fact)])


class TestVerificationDeletion:
    def test_verification_delete_is_not_allowed(self, agreement, reviewer):
        verification = Verification.objects.create(agreement=agreement, verified_by=reviewer)
        with pytest.raises(RecordDeletionNotAllowed):
            verification.delete()

    def test_verification_fact_delete_is_not_allowed(self, agreement, end_date_field, document, reviewer):
        fact = make_fact(agreement, end_date_field, document)
        verification = Verification.objects.create(agreement=agreement, verified_by=reviewer)
        link = VerificationFact.objects.create(verification=verification, fact=fact)
        with pytest.raises(RecordDeletionNotAllowed):
            link.delete()

    def test_verification_queryset_delete_is_not_allowed(self, agreement, reviewer):
        Verification.objects.create(agreement=agreement, verified_by=reviewer)
        with pytest.raises(RecordDeletionNotAllowed):
            Verification.objects.all().delete()
