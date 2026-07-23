import datetime
import uuid

import pytest
from django.core.exceptions import ValidationError

from registry.models import Fact, FactDispute, FactDisputeMember, Reviewer, Verification, VerificationFact
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


def test_linking_a_fact_to_an_open_dispute_flips_it_to_disputed(agreement, end_date_field, document, reviewer):
    fact = make_fact(agreement, end_date_field, document)
    dispute = FactDispute.objects.create(agreement=agreement, field=end_date_field, opened_by=reviewer)
    FactDisputeMember.objects.create(dispute=dispute, fact=fact)
    fact.refresh_from_db()
    assert fact.status == Fact.Status.DISPUTED


def test_member_fact_must_share_the_disputes_agreement(
    agreement, other_agreement, end_date_field, document, reviewer,
):
    dispute = FactDispute.objects.create(agreement=agreement, field=end_date_field, opened_by=reviewer)
    foreign_fact = make_fact(other_agreement, end_date_field, document)
    with pytest.raises(ValidationError):
        FactDisputeMember.objects.create(dispute=dispute, fact=foreign_fact)


def test_member_fact_must_share_the_disputes_field(agreement, end_date_field, notice_days_field, document, reviewer):
    dispute = FactDispute.objects.create(agreement=agreement, field=end_date_field, opened_by=reviewer)
    wrong_field_fact = make_fact(
        agreement, notice_days_field, document, value_date=None, value_number=90,
        period_unit=Fact.PeriodUnit.CALENDAR_DAYS,
    )
    with pytest.raises(ValidationError):
        FactDisputeMember.objects.create(dispute=dispute, fact=wrong_field_fact)


def test_resolution_note_required_to_leave_open_status(agreement, end_date_field, reviewer):
    dispute = FactDispute.objects.create(agreement=agreement, field=end_date_field, opened_by=reviewer)
    dispute.status = FactDispute.Status.RESOLVED_FAVORS_ONE
    with pytest.raises(ValidationError):
        dispute.save()


def test_resolving_a_dispute_reactivates_the_upheld_fact_and_leaves_rejected_disputed(
    agreement, end_date_field, document, reviewer, other_reviewer,
):
    disputed_fact = make_fact(agreement, end_date_field, document, value_date=datetime.date(2026, 6, 29))
    dispute = FactDispute.objects.create(agreement=agreement, field=end_date_field, opened_by=reviewer)
    membership = FactDisputeMember.objects.create(
        dispute=dispute, fact=disputed_fact, position_note="Contradicted by a later amendment.",
    )
    disputed_fact.refresh_from_db()
    assert disputed_fact.status == Fact.Status.DISPUTED

    membership.outcome = FactDisputeMember.Outcome.UPHELD
    membership.save()
    dispute.status = FactDispute.Status.RESOLVED_FAVORS_ONE
    dispute.resolution_note = "Confirmed correct against the original signed contract."
    dispute.resolved_at = datetime.datetime(2026, 8, 1, tzinfo=datetime.timezone.utc)
    dispute.resolved_by = other_reviewer
    dispute.save()

    disputed_fact.refresh_from_db()
    assert disputed_fact.status == Fact.Status.ACTIVE


def test_resolving_a_dispute_leaves_a_rejected_member_permanently_disputed(
    agreement, end_date_field, document, reviewer,
):
    losing_fact = make_fact(agreement, end_date_field, document, value_date=datetime.date(2027, 1, 1))
    dispute = FactDispute.objects.create(agreement=agreement, field=end_date_field, opened_by=reviewer)
    membership = FactDisputeMember.objects.create(dispute=dispute, fact=losing_fact)
    membership.outcome = FactDisputeMember.Outcome.REJECTED
    membership.save()

    dispute.status = FactDispute.Status.RESOLVED_FAVORS_ONE
    dispute.resolution_note = "The later document controls; this figure was superseded."
    dispute.save()

    losing_fact.refresh_from_db()
    assert losing_fact.status == Fact.Status.DISPUTED


def test_verification_history_remains_intact_when_a_verified_fact_becomes_disputed(
    agreement, end_date_field, document, reviewer,
):
    fact = make_fact(agreement, end_date_field, document, value_date=datetime.date(2026, 6, 29))
    verification = Verification.objects.create(agreement=agreement, verified_by=reviewer)
    verification_link = VerificationFact.objects.create(verification=verification, fact=fact)

    dispute = FactDispute.objects.create(agreement=agreement, field=end_date_field, opened_by=reviewer)
    FactDisputeMember.objects.create(dispute=dispute, fact=fact)

    fact.refresh_from_db()
    assert fact.status == Fact.Status.DISPUTED
    # The verification event and its link to this fact are untouched --
    # a permanent record of what was checked and when, independent of
    # the fact's current dispute status.
    assert VerificationFact.objects.filter(pk=verification_link.pk).exists()
    verification_link.refresh_from_db()
    assert verification_link.verification_id == verification.pk
    assert verification_link.fact_id == fact.pk


class TestDisputeDeletion:
    def test_dispute_delete_is_not_allowed(self, agreement, end_date_field, reviewer):
        dispute = FactDispute.objects.create(agreement=agreement, field=end_date_field, opened_by=reviewer)
        with pytest.raises(RecordDeletionNotAllowed):
            dispute.delete()

    def test_dispute_member_delete_is_not_allowed(self, agreement, end_date_field, document, reviewer):
        fact = make_fact(agreement, end_date_field, document)
        dispute = FactDispute.objects.create(agreement=agreement, field=end_date_field, opened_by=reviewer)
        member = FactDisputeMember.objects.create(dispute=dispute, fact=fact)
        with pytest.raises(RecordDeletionNotAllowed):
            member.delete()
