"""
compute_action_opportunities / select_primary_opportunity test matrix.

No Postgres-specific tests here: this module is pure ORM reads (standard
Q-object filters) and Python-side arithmetic, none of it backend-specific
-- SQLite/Postgres parity is exercised the same way as every other test
in this suite, by the existing CI matrix running the whole suite on both
backends, not by a separate mechanism.
"""

import datetime

import pytest
from django.core.exceptions import ValidationError

from registry.models import Fact, FactDispute, FactDisputeMember, FactField, Verification, VerificationFact
from registry.services import deadlines
from registry.services.deadlines import (
    ActionType,
    Confidence,
    ReasonCode,
    compute_action_opportunities,
    select_primary_opportunity,
)

pytestmark = pytest.mark.django_db


def make_fact(agreement, machine_key, document, reviewer, **overrides):
    field = FactField.objects.get(machine_key=machine_key)
    defaults = dict(
        agreement=agreement, field=field, primary_document=document, created_by=reviewer,
        valid_from=datetime.date(2024, 1, 1),
        effective_date_basis=Fact.EffectiveDateBasis.STATED_IN_DOCUMENT,
    )
    defaults.update(overrides)
    return Fact.objects.create(**defaults)


def set_end_date(agreement, document, reviewer, value, **overrides):
    return make_fact(agreement, "end_date", document, reviewer, value_date=value, **overrides)


def set_mechanism(agreement, document, reviewer, value, **overrides):
    return make_fact(agreement, "renewal_mechanism", document, reviewer, value_text=value, **overrides)


def set_non_renewal_notice(agreement, document, reviewer, value, unit, **overrides):
    return make_fact(
        agreement, "non_renewal_notice_days", document, reviewer,
        value_number=value, period_unit=unit, **overrides,
    )


def set_termination_notice(agreement, document, reviewer, value, unit, **overrides):
    return make_fact(
        agreement, "termination_for_convenience_notice_days", document, reviewer,
        value_number=value, period_unit=unit, **overrides,
    )


def set_scheduled_vote(agreement, document, reviewer, value, **overrides):
    return make_fact(agreement, "scheduled_vote_date", document, reviewer, value_date=value, **overrides)


def get(opportunities, action_type):
    matches = [o for o in opportunities if o.action_type == action_type]
    assert len(matches) == 1, f"expected exactly one {action_type}, got {len(matches)}"
    return matches[0]


class TestDateArithmetic:
    def test_calendar_days_subtraction(self, agreement, document, reviewer):
        set_mechanism(agreement, document, reviewer, "auto_renew_unless_cancelled")
        set_end_date(agreement, document, reviewer, datetime.date(2026, 1, 31))
        set_non_renewal_notice(agreement, document, reviewer, 30, Fact.PeriodUnit.CALENDAR_DAYS)
        opp = get(compute_action_opportunities(agreement, datetime.date(2025, 6, 1)), ActionType.NON_RENEWAL_NOTICE)
        assert opp.action_date == datetime.date(2026, 1, 1)
        assert opp.confidence == Confidence.HIGH

    def test_end_of_month_clamping(self, agreement, document, reviewer):
        set_mechanism(agreement, document, reviewer, "auto_renew_unless_cancelled")
        set_end_date(agreement, document, reviewer, datetime.date(2026, 3, 31))
        set_non_renewal_notice(agreement, document, reviewer, 1, Fact.PeriodUnit.MONTHS)
        opp = get(compute_action_opportunities(agreement, datetime.date(2025, 6, 1)), ActionType.NON_RENEWAL_NOTICE)
        assert opp.action_date == datetime.date(2026, 2, 28)  # 2026 is not a leap year
        assert opp.confidence == Confidence.HIGH

    def test_leap_year_clamping(self, agreement, document, reviewer):
        set_mechanism(agreement, document, reviewer, "auto_renew_unless_cancelled")
        set_end_date(agreement, document, reviewer, datetime.date(2028, 3, 31))
        set_non_renewal_notice(agreement, document, reviewer, 1, Fact.PeriodUnit.MONTHS)
        opp = get(compute_action_opportunities(agreement, datetime.date(2025, 6, 1)), ActionType.NON_RENEWAL_NOTICE)
        assert opp.action_date == datetime.date(2028, 2, 29)  # 2028 is a leap year

    def test_years_subtraction_with_clamping(self, agreement, document, reviewer):
        set_mechanism(agreement, document, reviewer, "auto_renew_unless_cancelled")
        set_end_date(agreement, document, reviewer, datetime.date(2028, 2, 29))
        set_non_renewal_notice(agreement, document, reviewer, 1, Fact.PeriodUnit.YEARS)
        opp = get(compute_action_opportunities(agreement, datetime.date(2025, 6, 1)), ActionType.NON_RENEWAL_NOTICE)
        assert opp.action_date == datetime.date(2027, 2, 28)  # 2027 is not a leap year

    def test_business_days_refused(self, agreement, document, reviewer):
        set_mechanism(agreement, document, reviewer, "auto_renew_unless_cancelled")
        set_end_date(agreement, document, reviewer, datetime.date(2026, 6, 1))
        set_non_renewal_notice(agreement, document, reviewer, 30, Fact.PeriodUnit.BUSINESS_DAYS)
        opp = get(compute_action_opportunities(agreement, datetime.date(2025, 6, 1)), ActionType.NON_RENEWAL_NOTICE)
        assert opp.action_date is None
        assert opp.confidence == Confidence.NONE
        assert opp.reason_code == ReasonCode.UNSUPPORTED_PERIOD_UNIT

    def test_missing_period_unit_refused(self, agreement, document, reviewer):
        set_mechanism(agreement, document, reviewer, "auto_renew_unless_cancelled")
        set_end_date(agreement, document, reviewer, datetime.date(2026, 6, 1))
        set_non_renewal_notice(agreement, document, reviewer, 30, None)
        opp = get(compute_action_opportunities(agreement, datetime.date(2025, 6, 1)), ActionType.NON_RENEWAL_NOTICE)
        assert opp.action_date is None
        assert opp.reason_code == ReasonCode.MISSING_OR_UNRECOGNIZED_PERIOD_UNIT


class TestNonRenewalOpportunity:
    def test_missing_end_date(self, agreement, document, reviewer):
        set_mechanism(agreement, document, reviewer, "auto_renew_unless_cancelled")
        opp = get(compute_action_opportunities(agreement, datetime.date(2025, 6, 1)), ActionType.NON_RENEWAL_NOTICE)
        assert opp.action_date is None
        assert opp.reason_code == ReasonCode.REQUIRED_FACT_MISSING
        assert opp.missing_field_name == "end_date"

    def test_missing_notice_days_still_reports_non_renewal(self, agreement, document, reviewer):
        # Unlike termination-for-convenience, non-renewal is always
        # reported, never silently omitted, even when the notice fact
        # was never entered.
        set_mechanism(agreement, document, reviewer, "auto_renew_unless_cancelled")
        set_end_date(agreement, document, reviewer, datetime.date(2026, 6, 1))
        opp = get(compute_action_opportunities(agreement, datetime.date(2025, 6, 1)), ActionType.NON_RENEWAL_NOTICE)
        assert opp.action_date is None
        assert opp.reason_code == ReasonCode.REQUIRED_FACT_MISSING
        assert opp.missing_field_name == "non_renewal_notice_days"

    def test_resolved_is_eligible_for_alert(self, agreement, document, reviewer):
        set_mechanism(agreement, document, reviewer, "auto_renew_unless_cancelled")
        set_end_date(agreement, document, reviewer, datetime.date(2026, 6, 1))
        set_non_renewal_notice(agreement, document, reviewer, 90, Fact.PeriodUnit.CALENDAR_DAYS)
        opp = get(compute_action_opportunities(agreement, datetime.date(2025, 6, 1)), ActionType.NON_RENEWAL_NOTICE)
        assert opp.action_date is not None
        assert opp.is_eligible_for_alert is True
        assert opp.is_documented_event is False
        assert opp.is_advisory is False


class TestTerminationForConvenience:
    def test_computed_and_labeled_separately_from_non_renewal(self, agreement, document, reviewer):
        set_mechanism(agreement, document, reviewer, "auto_renew_unless_cancelled")
        set_end_date(agreement, document, reviewer, datetime.date(2026, 12, 31))
        set_non_renewal_notice(agreement, document, reviewer, 90, Fact.PeriodUnit.CALENDAR_DAYS)
        set_termination_notice(agreement, document, reviewer, 30, Fact.PeriodUnit.CALENDAR_DAYS)
        opportunities = compute_action_opportunities(agreement, datetime.date(2025, 6, 1))
        non_renewal = get(opportunities, ActionType.NON_RENEWAL_NOTICE)
        termination = get(opportunities, ActionType.TERMINATION_FOR_CONVENIENCE)
        assert non_renewal.action_date == datetime.date(2026, 12, 31) - datetime.timedelta(days=90)
        assert termination.action_date == datetime.date(2026, 12, 31) - datetime.timedelta(days=30)
        assert non_renewal.action_date != termination.action_date
        assert set(non_renewal.input_fact_ids) != set(termination.input_fact_ids)

    def test_absent_entirely_when_fact_never_entered(self, agreement, document, reviewer):
        set_mechanism(agreement, document, reviewer, "auto_renew_unless_cancelled")
        set_end_date(agreement, document, reviewer, datetime.date(2026, 12, 31))
        set_non_renewal_notice(agreement, document, reviewer, 90, Fact.PeriodUnit.CALENDAR_DAYS)
        opportunities = compute_action_opportunities(agreement, datetime.date(2025, 6, 1))
        assert not [o for o in opportunities if o.action_type == ActionType.TERMINATION_FOR_CONVENIENCE]

    def test_independent_of_renewal_mechanism(self, agreement, document, reviewer):
        # A termination-for-convenience clause can exist regardless of
        # how renewal itself works.
        set_mechanism(agreement, document, reviewer, "affirmative_vote_required")
        set_end_date(agreement, document, reviewer, datetime.date(2026, 12, 31))
        set_termination_notice(agreement, document, reviewer, 30, Fact.PeriodUnit.CALENDAR_DAYS)
        opportunities = compute_action_opportunities(agreement, datetime.date(2025, 6, 1))
        termination = get(opportunities, ActionType.TERMINATION_FOR_CONVENIENCE)
        assert termination.action_date == datetime.date(2026, 12, 1)

    def test_never_substitutes_for_non_renewal_deadline(self, agreement, document, reviewer):
        set_mechanism(agreement, document, reviewer, "auto_renew_unless_cancelled")
        set_end_date(agreement, document, reviewer, datetime.date(2026, 12, 31))
        set_termination_notice(agreement, document, reviewer, 30, Fact.PeriodUnit.CALENDAR_DAYS)
        # non_renewal_notice_days deliberately absent.
        opportunities = compute_action_opportunities(agreement, datetime.date(2025, 6, 1))
        non_renewal = get(opportunities, ActionType.NON_RENEWAL_NOTICE)
        termination = get(opportunities, ActionType.TERMINATION_FOR_CONVENIENCE)
        assert non_renewal.action_date is None
        assert non_renewal.reason_code == ReasonCode.REQUIRED_FACT_MISSING
        assert termination.action_date is not None


class TestScheduledVoteAndAdvisory:
    def test_scheduled_vote_is_documented(self, agreement, document, reviewer):
        set_mechanism(agreement, document, reviewer, "affirmative_vote_required")
        set_scheduled_vote(agreement, document, reviewer, datetime.date(2026, 9, 15))
        opp = get(compute_action_opportunities(agreement, datetime.date(2025, 6, 1)), ActionType.SCHEDULED_VOTE)
        assert opp.action_date == datetime.date(2026, 9, 15)
        assert opp.is_documented_event is True
        assert opp.is_advisory is False
        assert opp.confidence == Confidence.HIGH

    def test_advisory_only_case(self, agreement, document, reviewer):
        set_mechanism(agreement, document, reviewer, "affirmative_vote_required")
        set_end_date(agreement, document, reviewer, datetime.date(2026, 12, 29))
        opp = get(compute_action_opportunities(agreement, datetime.date(2025, 6, 1)), ActionType.ADVISORY_AGENDA_WATCH)
        assert opp.action_date == datetime.date(2026, 12, 29) - datetime.timedelta(
            days=deadlines.DEFAULT_AGENDA_WATCH_LEAD_DAYS,
        )
        assert opp.is_advisory is True
        assert opp.is_documented_event is False
        assert opp.confidence == Confidence.LOW

    def test_advisory_never_populates_documented_event_field(self, agreement, document, reviewer):
        set_mechanism(agreement, document, reviewer, "affirmative_vote_required")
        set_end_date(agreement, document, reviewer, datetime.date(2026, 12, 29))
        opp = get(compute_action_opportunities(agreement, datetime.date(2025, 6, 1)), ActionType.ADVISORY_AGENDA_WATCH)
        assert opp.is_documented_event is False

    def test_no_expiration_date_no_scheduled_vote(self, agreement, document, reviewer):
        set_mechanism(agreement, document, reviewer, "affirmative_vote_required")
        opp = get(compute_action_opportunities(agreement, datetime.date(2025, 6, 1)), ActionType.ADVISORY_AGENDA_WATCH)
        assert opp.action_date is None
        assert opp.reason_code == ReasonCode.NO_EXPIRATION_DATE_FOR_ADVISORY_WINDOW

    def test_disputed_scheduled_vote_blocks_calculation(self, agreement, document, reviewer):
        vote_fact = set_scheduled_vote(agreement, document, reviewer, datetime.date(2026, 9, 15))
        dispute = FactDispute.objects.create(
            agreement=agreement, field=vote_fact.field, opened_by=reviewer,
        )
        FactDisputeMember.objects.create(dispute=dispute, fact=vote_fact)
        set_mechanism(agreement, document, reviewer, "affirmative_vote_required")
        opp = get(compute_action_opportunities(agreement, datetime.date(2025, 6, 1)), ActionType.SCHEDULED_VOTE)
        assert opp.action_date is None
        assert opp.reason_code == ReasonCode.REQUIRED_FACT_DISPUTED
        assert opp.blocking_dispute_id == dispute.pk


class TestUnknownMechanism:
    def test_mechanism_missing(self, agreement, document, reviewer):
        opp = get(compute_action_opportunities(agreement, datetime.date(2025, 6, 1)), ActionType.UNKNOWN_MECHANISM)
        assert opp.action_date is None
        assert opp.reason_code == ReasonCode.REQUIRED_FACT_MISSING

    def test_mechanism_unrecognized_value(self, agreement, document, reviewer):
        set_mechanism(agreement, document, reviewer, "some_other_value_nobody_expected")
        opp = get(compute_action_opportunities(agreement, datetime.date(2025, 6, 1)), ActionType.UNKNOWN_MECHANISM)
        assert opp.reason_code == ReasonCode.RENEWAL_MECHANISM_UNRECOGNIZED

    def test_never_substitutes_expiration_for_unknown_mechanism(self, agreement, document, reviewer):
        set_end_date(agreement, document, reviewer, datetime.date(2026, 12, 29))
        opp = get(compute_action_opportunities(agreement, datetime.date(2025, 6, 1)), ActionType.UNKNOWN_MECHANISM)
        assert opp.action_date is None

    def test_mechanism_disputed(self, agreement, document, reviewer):
        mechanism_fact = set_mechanism(agreement, document, reviewer, "auto_renew_unless_cancelled")
        dispute = FactDispute.objects.create(
            agreement=agreement, field=mechanism_fact.field, opened_by=reviewer,
        )
        FactDisputeMember.objects.create(dispute=dispute, fact=mechanism_fact)
        opp = get(compute_action_opportunities(agreement, datetime.date(2025, 6, 1)), ActionType.UNKNOWN_MECHANISM)
        assert opp.reason_code == ReasonCode.REQUIRED_FACT_DISPUTED
        assert opp.blocking_dispute_id == dispute.pk


class TestVerificationDoesNotAffectComputation:
    def test_unverified_tier2_fact_still_eligible(self, agreement, document, reviewer):
        set_mechanism(agreement, document, reviewer, "auto_renew_unless_cancelled")
        set_end_date(agreement, document, reviewer, datetime.date(2026, 6, 1))
        set_non_renewal_notice(agreement, document, reviewer, 90, Fact.PeriodUnit.CALENDAR_DAYS)
        opp = get(compute_action_opportunities(agreement, datetime.date(2025, 6, 1)), ActionType.NON_RENEWAL_NOTICE)
        assert opp.action_date is not None
        assert opp.is_eligible_for_alert is True

    def test_verified_fact_computes_identically_to_unverified(self, agreement, document, reviewer):
        set_mechanism(agreement, document, reviewer, "auto_renew_unless_cancelled")
        end_date_fact = set_end_date(agreement, document, reviewer, datetime.date(2026, 6, 1))
        set_non_renewal_notice(agreement, document, reviewer, 90, Fact.PeriodUnit.CALENDAR_DAYS)
        verification = Verification.objects.create(agreement=agreement, verified_by=reviewer)
        VerificationFact.objects.create(verification=verification, fact=end_date_fact)
        opp = get(compute_action_opportunities(agreement, datetime.date(2025, 6, 1)), ActionType.NON_RENEWAL_NOTICE)
        assert opp.action_date == datetime.date(2026, 6, 1) - datetime.timedelta(days=90)
        assert opp.is_eligible_for_alert is True


class TestTemporalResolution:
    def test_future_effective_replacement_fact_not_yet_operative(self, agreement, document, reviewer):
        set_mechanism(agreement, document, reviewer, "auto_renew_unless_cancelled")
        original = set_end_date(
            agreement, document, reviewer, datetime.date(2026, 6, 29), valid_from=datetime.date(2024, 1, 1),
        )
        set_non_renewal_notice(agreement, document, reviewer, 90, Fact.PeriodUnit.CALENDAR_DAYS)
        # Signed now, effective only in the future.
        make_fact(
            agreement, "end_date", document, reviewer, value_date=datetime.date(2026, 12, 29),
            valid_from=datetime.date(2026, 7, 15), supersedes=original,
        )
        opp = get(compute_action_opportunities(agreement, datetime.date(2026, 7, 1)), ActionType.NON_RENEWAL_NOTICE)
        assert opp.action_date == datetime.date(2026, 6, 29) - datetime.timedelta(days=90)
        assert original.pk in opp.input_fact_ids

    def test_historical_as_of_resolves_the_fact_operative_then(self, agreement, document, reviewer):
        set_mechanism(agreement, document, reviewer, "auto_renew_unless_cancelled")
        original = set_end_date(
            agreement, document, reviewer, datetime.date(2026, 6, 29), valid_from=datetime.date(2024, 1, 1),
        )
        set_non_renewal_notice(agreement, document, reviewer, 90, Fact.PeriodUnit.CALENDAR_DAYS)
        amendment = make_fact(
            agreement, "end_date", document, reviewer, value_date=datetime.date(2026, 12, 29),
            valid_from=datetime.date(2026, 7, 15), supersedes=original,
        )
        opp_before = get(
            compute_action_opportunities(agreement, datetime.date(2026, 7, 1)), ActionType.NON_RENEWAL_NOTICE,
        )
        opp_after = get(
            compute_action_opportunities(agreement, datetime.date(2026, 8, 1)), ActionType.NON_RENEWAL_NOTICE,
        )
        assert opp_before.action_date == datetime.date(2026, 6, 29) - datetime.timedelta(days=90)
        assert opp_after.action_date == datetime.date(2026, 12, 29) - datetime.timedelta(days=90)
        assert original.pk in opp_before.input_fact_ids
        assert amendment.pk in opp_after.input_fact_ids


class TestMultipleOpportunitiesAndPrimarySelection:
    def test_multiple_simultaneous_opportunities_returned_together(self, agreement, document, reviewer):
        set_mechanism(agreement, document, reviewer, "auto_renew_unless_cancelled")
        set_end_date(agreement, document, reviewer, datetime.date(2026, 12, 31))
        set_non_renewal_notice(agreement, document, reviewer, 90, Fact.PeriodUnit.CALENDAR_DAYS)
        set_termination_notice(agreement, document, reviewer, 30, Fact.PeriodUnit.CALENDAR_DAYS)
        opportunities = compute_action_opportunities(agreement, datetime.date(2025, 6, 1))
        assert len(opportunities) == 2
        assert {o.action_type for o in opportunities} == {
            ActionType.NON_RENEWAL_NOTICE, ActionType.TERMINATION_FOR_CONVENIENCE,
        }

    def test_non_renewal_outranks_equal_date_termination(self, agreement, document, reviewer):
        set_mechanism(agreement, document, reviewer, "auto_renew_unless_cancelled")
        set_end_date(agreement, document, reviewer, datetime.date(2026, 12, 31))
        set_non_renewal_notice(agreement, document, reviewer, 90, Fact.PeriodUnit.CALENDAR_DAYS)
        set_termination_notice(agreement, document, reviewer, 90, Fact.PeriodUnit.CALENDAR_DAYS)
        opportunities = compute_action_opportunities(agreement, datetime.date(2025, 6, 1))
        non_renewal = get(opportunities, ActionType.NON_RENEWAL_NOTICE)
        termination = get(opportunities, ActionType.TERMINATION_FOR_CONVENIENCE)
        assert non_renewal.action_date == termination.action_date  # genuinely tied
        primary = select_primary_opportunity(opportunities)
        assert primary.action_type == ActionType.NON_RENEWAL_NOTICE

    def test_scheduled_vote_outranks_termination_regardless_of_date(self, agreement, document, reviewer):
        set_mechanism(agreement, document, reviewer, "affirmative_vote_required")
        set_scheduled_vote(agreement, document, reviewer, datetime.date(2026, 12, 31))  # later
        set_end_date(agreement, document, reviewer, datetime.date(2026, 6, 30))
        set_termination_notice(agreement, document, reviewer, 30, Fact.PeriodUnit.CALENDAR_DAYS)  # earlier date
        opportunities = compute_action_opportunities(agreement, datetime.date(2025, 6, 1))
        termination = get(opportunities, ActionType.TERMINATION_FOR_CONVENIENCE)
        assert termination.action_date < datetime.date(2026, 12, 31)  # confirms termination really is earlier
        primary = select_primary_opportunity(opportunities)
        assert primary.action_type == ActionType.SCHEDULED_VOTE

    def test_advisory_never_selected_as_primary_over_a_calculated_deadline(
        self, agreement, other_agreement, document, reviewer,
    ):
        # Merges opportunities from two different agreements into one
        # list purely to exercise select_primary_opportunity's ordering
        # directly -- it sorts a flat list without caring which agreement
        # each entry came from, so this is a legitimate, direct way to
        # prove an advisory recommendation (however much earlier its
        # date) never outranks a calculated deadline.
        set_mechanism(agreement, document, reviewer, "auto_renew_unless_cancelled")
        set_end_date(agreement, document, reviewer, datetime.date(2027, 1, 1))  # later date
        set_non_renewal_notice(agreement, document, reviewer, 90, Fact.PeriodUnit.CALENDAR_DAYS)
        calculated = compute_action_opportunities(agreement, datetime.date(2025, 6, 1))
        non_renewal = get(calculated, ActionType.NON_RENEWAL_NOTICE)

        set_mechanism(other_agreement, document, reviewer, "affirmative_vote_required")
        set_end_date(other_agreement, document, reviewer, datetime.date(2026, 1, 1))  # earlier date
        advisory_opportunities = compute_action_opportunities(other_agreement, datetime.date(2025, 6, 1))
        advisory = get(advisory_opportunities, ActionType.ADVISORY_AGENDA_WATCH)

        assert advisory.action_date < non_renewal.action_date  # advisory really is earlier
        primary = select_primary_opportunity([non_renewal, advisory])
        assert primary.action_type == ActionType.NON_RENEWAL_NOTICE

    def test_primary_selection_returns_none_when_nothing_resolved(self, agreement, document, reviewer):
        opportunities = compute_action_opportunities(agreement, datetime.date(2025, 6, 1))
        assert select_primary_opportunity(opportunities) is None

    def test_primary_selection_is_deterministic(self, agreement, document, reviewer):
        set_mechanism(agreement, document, reviewer, "auto_renew_unless_cancelled")
        set_end_date(agreement, document, reviewer, datetime.date(2026, 12, 31))
        set_non_renewal_notice(agreement, document, reviewer, 90, Fact.PeriodUnit.CALENDAR_DAYS)
        set_termination_notice(agreement, document, reviewer, 90, Fact.PeriodUnit.CALENDAR_DAYS)
        opportunities = compute_action_opportunities(agreement, datetime.date(2025, 6, 1))
        results = {select_primary_opportunity(opportunities).action_type for _ in range(20)}
        assert results == {ActionType.NON_RENEWAL_NOTICE}


class TestAuditability:
    def test_exact_input_fact_ids_on_resolved_opportunity(self, agreement, document, reviewer):
        set_mechanism(agreement, document, reviewer, "auto_renew_unless_cancelled")
        end_date_fact = set_end_date(agreement, document, reviewer, datetime.date(2026, 12, 31))
        notice_fact = set_non_renewal_notice(agreement, document, reviewer, 90, Fact.PeriodUnit.CALENDAR_DAYS)
        opp = get(compute_action_opportunities(agreement, datetime.date(2025, 6, 1)), ActionType.NON_RENEWAL_NOTICE)
        assert set(opp.input_fact_ids) == {end_date_fact.pk, notice_fact.pk}
        assert opp.formula_id == "auto_renew_notice_v1"
        assert opp.calculation_version == deadlines.CALCULATION_VERSION

    def test_exact_input_fact_ids_on_unresolved_opportunity(self, agreement, document, reviewer):
        set_mechanism(agreement, document, reviewer, "auto_renew_unless_cancelled")
        end_date_fact = set_end_date(agreement, document, reviewer, datetime.date(2026, 12, 31))
        opp = get(compute_action_opportunities(agreement, datetime.date(2025, 6, 1)), ActionType.NON_RENEWAL_NOTICE)
        assert opp.action_date is None
        assert opp.input_fact_ids == (end_date_fact.pk,)

    def test_computation_is_deterministic_across_repeated_calls(self, agreement, document, reviewer):
        set_mechanism(agreement, document, reviewer, "auto_renew_unless_cancelled")
        set_end_date(agreement, document, reviewer, datetime.date(2026, 12, 31))
        set_non_renewal_notice(agreement, document, reviewer, 90, Fact.PeriodUnit.CALENDAR_DAYS)
        results = [compute_action_opportunities(agreement, datetime.date(2025, 6, 1)) for _ in range(5)]
        assert all(r == results[0] for r in results)

    def test_source_document_ids_present_on_resolved_opportunity(self, agreement, document, reviewer):
        set_mechanism(agreement, document, reviewer, "auto_renew_unless_cancelled")
        set_end_date(agreement, document, reviewer, datetime.date(2026, 12, 31))
        set_non_renewal_notice(agreement, document, reviewer, 90, Fact.PeriodUnit.CALENDAR_DAYS)
        opp = get(compute_action_opportunities(agreement, datetime.date(2025, 6, 1)), ActionType.NON_RENEWAL_NOTICE)
        assert document.pk in opp.source_document_ids
