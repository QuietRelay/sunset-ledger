"""
Deadline and action-opportunity computation -- a pure domain-service
layer over the frozen fact model. No public pages, forms, submissions,
subscriptions, email alerts, exports, agenda scraping, or background
jobs live here; this module only ever reads via the ORM and returns
plain data.

`compute_action_opportunities(agreement, as_of)` is the whole public
surface most callers need. `select_primary_opportunity` is a second,
deliberately separate function -- picking which opportunity to highlight
is not part of computing what exists, and the two must never be fused
into one so that a v2 UI can apply a different selection rule without
touching computation at all.

Every fact used as an input is resolved through `_resolve_operative_fact`,
which is where disputed and effective_date_basis=unspecified_defaulted
facts are excluded from ever driving a calculation -- see its docstring.
Nothing here mutates a Fact, a Verification, or a FactDispute; this
module is read-only.
"""

from __future__ import annotations

import calendar
import dataclasses
from dataclasses import dataclass
from datetime import date, timedelta
from enum import Enum

from django.conf import settings
from django.db.models import Q

from registry.models import Fact, FactDispute, FactField

CALCULATION_VERSION = settings.DEADLINE_CALCULATION_VERSION
DEFAULT_AGENDA_WATCH_LEAD_DAYS = settings.DEFAULT_AGENDA_WATCH_LEAD_DAYS

RENEWAL_MECHANISM_AUTO_RENEW = "auto_renew_unless_cancelled"
RENEWAL_MECHANISM_AFFIRMATIVE_VOTE = "affirmative_vote_required"


class ActionType(str, Enum):
    NON_RENEWAL_NOTICE = "non_renewal_notice"
    TERMINATION_FOR_CONVENIENCE = "termination_for_convenience"
    SCHEDULED_VOTE = "scheduled_vote"
    ADVISORY_AGENDA_WATCH = "advisory_agenda_watch"
    UNKNOWN_MECHANISM = "unknown_mechanism"


class Confidence(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    NONE = "none"


class ReasonCode(str, Enum):
    REQUIRED_FACT_MISSING = "required_fact_missing"
    REQUIRED_FACT_DISPUTED = "required_fact_disputed"
    REQUIRED_FACT_UNSPECIFIED_BASIS = "required_fact_unspecified_basis"
    UNSUPPORTED_PERIOD_UNIT = "unsupported_period_unit"
    MISSING_OR_UNRECOGNIZED_PERIOD_UNIT = "missing_or_unrecognized_period_unit"
    RENEWAL_MECHANISM_UNRECOGNIZED = "renewal_mechanism_unrecognized"
    NO_DOCUMENTED_VOTE_SCHEDULED = "no_documented_vote_scheduled"
    NO_EXPIRATION_DATE_FOR_ADVISORY_WINDOW = "no_expiration_date_for_advisory_window"


class PriorityClass:
    """Sortable metadata, not UI wording. Lower sorts first in
    select_primary_opportunity -- documented events always beat
    calculated deadlines always beat advisory recommendations,
    regardless of date."""

    DOCUMENTED = 0
    CALCULATED = 1
    ADVISORY = 2
    UNKNOWN = 3


@dataclass(frozen=True)
class ActionOpportunity:
    agreement_id: int
    action_type: ActionType
    action_date: date | None
    is_documented_event: bool
    is_advisory: bool
    input_fact_ids: tuple[int, ...]
    source_document_ids: tuple[int, ...]
    formula_id: str
    calculation_version: str
    confidence: Confidence
    reason_code: ReasonCode | None
    missing_field_name: str
    unresolvable_reason: str
    blocking_dispute_id: int | None
    is_eligible_for_alert: bool
    priority_class: int


@dataclass(frozen=True)
class _Resolution:
    fact: Fact | None
    blocking_dispute_id: int | None
    # None (resolved) | "required_fact_missing" | "required_fact_disputed" | "required_fact_unspecified_basis"
    exclusion_reason: str | None


def _resolve_operative_fact(agreement, machine_key: str, as_of: date) -> _Resolution:
    """The operative fact for (agreement, machine_key) as of `as_of`, or a
    structured reason it isn't usable.

    1. Fact.objects.operative_at() already excludes disputed/retracted/
       proposed_option facts (it only ever considers status=active).
    2. A resolved fact whose effective_date_basis is
       unspecified_defaulted is still excluded here -- an unclear basis
       on the *current* fact doesn't make an older, superseded fact more
       trustworthy, so this is not a fallback search through history, it
       is a hard stop.
    3. If nothing resolves, check whether a *disputed* fact would
       otherwise have covered `as_of` -- distinguishes "blocked by a live
       dispute" (surfaces the open dispute's id) from "never entered at
       all". Nothing here touches the disputed fact or any
       VerificationFact row that may cover it; this is a read-only
       classification of why resolution failed.
    """
    field = FactField.objects.get(machine_key=machine_key)
    fact = Fact.objects.operative_at(agreement, field, as_of)
    if fact is not None:
        if fact.effective_date_basis == Fact.EffectiveDateBasis.UNSPECIFIED_DEFAULTED:
            return _Resolution(None, None, "required_fact_unspecified_basis")
        return _Resolution(fact, None, None)

    disputed = (
        Fact.objects.filter(
            agreement=agreement, field=field, status=Fact.Status.DISPUTED, valid_from__lte=as_of,
        )
        .filter(Q(valid_until__isnull=True) | Q(valid_until__gt=as_of))
        .order_by("-valid_from")
        .first()
    )
    if disputed is not None:
        membership = disputed.dispute_memberships.filter(
            dispute__status=FactDispute.Status.OPEN,
        ).select_related("dispute").first()
        blocking_dispute_id = membership.dispute_id if membership else None
        return _Resolution(None, blocking_dispute_id, "required_fact_disputed")

    return _Resolution(None, None, "required_fact_missing")


def _agreement_has_any_active_fact(agreement) -> bool:
    return Fact.objects.filter(agreement=agreement, status=Fact.Status.ACTIVE).exists()


def _document_ids(*facts: Fact) -> tuple[int, ...]:
    seen: list[int] = []
    for f in facts:
        if f.primary_document_id not in seen:
            seen.append(f.primary_document_id)
    return tuple(seen)


_REASON_TEXT = {
    ReasonCode.REQUIRED_FACT_MISSING: "No active fact exists for '{field}' as of the requested date.",
    ReasonCode.REQUIRED_FACT_DISPUTED: (
        "The current fact for '{field}' is under an open dispute and cannot be used."
    ),
    ReasonCode.REQUIRED_FACT_UNSPECIFIED_BASIS: (
        "The current fact for '{field}' has an unspecified effective-date basis and cannot "
        "drive this calculation until documented."
    ),
    ReasonCode.UNSUPPORTED_PERIOD_UNIT: (
        "The notice period is specified in business days; no business-day calendar is "
        "supported yet."
    ),
    ReasonCode.MISSING_OR_UNRECOGNIZED_PERIOD_UNIT: "The notice period's unit is missing or not recognized.",
    ReasonCode.RENEWAL_MECHANISM_UNRECOGNIZED: (
        "The agreement's renewal_mechanism value is not one of the recognized values."
    ),
    ReasonCode.NO_DOCUMENTED_VOTE_SCHEDULED: "No documented vote date is currently scheduled.",
    ReasonCode.NO_EXPIRATION_DATE_FOR_ADVISORY_WINDOW: (
        "No expiration date is available to compute an advisory monitoring window."
    ),
}


def _reason_text(reason_code: ReasonCode, field_name: str = "") -> str:
    return _REASON_TEXT[reason_code].format(field=field_name)


_RESOLUTION_REASON_CODE = {
    "required_fact_missing": ReasonCode.REQUIRED_FACT_MISSING,
    "required_fact_disputed": ReasonCode.REQUIRED_FACT_DISPUTED,
    "required_fact_unspecified_basis": ReasonCode.REQUIRED_FACT_UNSPECIFIED_BASIS,
}


def _unresolved_from_resolution(
    agreement, action_type: ActionType, resolution: _Resolution, *,
    field_name: str, formula_id: str, prior_input_fact_ids: tuple[int, ...] = (),
    is_documented_event: bool = False, is_advisory: bool = False, priority_class: int,
) -> ActionOpportunity:
    reason_code = _RESOLUTION_REASON_CODE[resolution.exclusion_reason]
    return ActionOpportunity(
        agreement_id=agreement.pk,
        action_type=action_type,
        action_date=None,
        is_documented_event=is_documented_event,
        is_advisory=is_advisory,
        input_fact_ids=prior_input_fact_ids,
        source_document_ids=(),
        formula_id=formula_id,
        calculation_version=CALCULATION_VERSION,
        confidence=Confidence.NONE,
        reason_code=reason_code,
        missing_field_name=field_name,
        unresolvable_reason=_reason_text(reason_code, field_name),
        blocking_dispute_id=resolution.blocking_dispute_id,
        is_eligible_for_alert=_agreement_has_any_active_fact(agreement),
        priority_class=priority_class,
    )


def _subtract_months(base: date, months: int) -> date:
    """Calendar-arithmetic convention: same day-of-month in the target
    month, clamped to that month's last valid day on overflow (e.g. Mar
    31 minus 1 month -> Feb 28, or 29 in a leap year)."""
    total_months = base.year * 12 + (base.month - 1) - months
    year, month = divmod(total_months, 12)
    month += 1
    day = min(base.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def _subtract_period(base: date, amount, period_unit: str | None):
    """Returns (result_date_or_None, Confidence, ReasonCode_or_None)."""
    if period_unit == Fact.PeriodUnit.CALENDAR_DAYS:
        return base - timedelta(days=int(amount)), Confidence.HIGH, None
    if period_unit == Fact.PeriodUnit.MONTHS:
        return _subtract_months(base, int(amount)), Confidence.HIGH, None
    if period_unit == Fact.PeriodUnit.YEARS:
        return _subtract_months(base, int(amount) * 12), Confidence.HIGH, None
    if period_unit == Fact.PeriodUnit.BUSINESS_DAYS:
        return None, Confidence.NONE, ReasonCode.UNSUPPORTED_PERIOD_UNIT
    return None, Confidence.NONE, ReasonCode.MISSING_OR_UNRECOGNIZED_PERIOD_UNIT


def _notice_based_opportunity(
    agreement, as_of: date, *, action_type: ActionType, notice_machine_key: str, formula_id: str,
    always_report: bool = False,
) -> ActionOpportunity | None:
    """Shared computation for non_renewal_notice and
    termination_for_convenience -- both are end_date minus a notice
    period, differing only in which notice-days field applies. Neither
    is ever used as a fallback for the other; each resolves entirely
    independently, including independently failing to resolve.

    Returns None when the notice-days fact is genuinely absent (never
    entered at all) and `always_report` is False --
    termination_for_convenience must not generate an opportunity at all
    in that case. non_renewal_notice passes always_report=True instead:
    an auto-renewing agreement always has *some* non-renewal deadline in
    reality, so silence there would be misleading even when the notice
    fact hasn't been entered yet. A disputed or unspecified-basis notice
    fact always counts as "the clause is present" regardless of
    always_report, producing an unresolved opportunity instead of
    silence either way.

    end_date is checked as the more fundamental precondition: if it's
    also missing, that's reported as the blocker regardless of the
    notice fact's own state, rather than reporting whichever fact
    happened to be checked first.
    """
    notice_res = _resolve_operative_fact(agreement, notice_machine_key, as_of)
    if notice_res.exclusion_reason == "required_fact_missing" and not always_report:
        return None

    end_date_res = _resolve_operative_fact(agreement, "end_date", as_of)
    if end_date_res.fact is None:
        return _unresolved_from_resolution(
            agreement, action_type, end_date_res, field_name="end_date",
            formula_id=formula_id, priority_class=PriorityClass.CALCULATED,
        )

    if notice_res.fact is None:
        return _unresolved_from_resolution(
            agreement, action_type, notice_res, field_name=notice_machine_key,
            formula_id=formula_id, prior_input_fact_ids=(end_date_res.fact.pk,),
            priority_class=PriorityClass.CALCULATED,
        )

    computed_date, confidence, unit_reason_code = _subtract_period(
        end_date_res.fact.value_date, notice_res.fact.value_number, notice_res.fact.period_unit,
    )
    input_fact_ids = (end_date_res.fact.pk, notice_res.fact.pk)
    source_document_ids = _document_ids(end_date_res.fact, notice_res.fact)

    if computed_date is None:
        return ActionOpportunity(
            agreement_id=agreement.pk, action_type=action_type, action_date=None,
            is_documented_event=False, is_advisory=False,
            input_fact_ids=input_fact_ids, source_document_ids=source_document_ids,
            formula_id=formula_id, calculation_version=CALCULATION_VERSION,
            confidence=Confidence.NONE, reason_code=unit_reason_code,
            missing_field_name=notice_machine_key,
            unresolvable_reason=_reason_text(unit_reason_code, notice_machine_key),
            blocking_dispute_id=None,
            is_eligible_for_alert=_agreement_has_any_active_fact(agreement),
            priority_class=PriorityClass.CALCULATED,
        )

    return ActionOpportunity(
        agreement_id=agreement.pk, action_type=action_type, action_date=computed_date,
        is_documented_event=False, is_advisory=False,
        input_fact_ids=input_fact_ids, source_document_ids=source_document_ids,
        formula_id=formula_id, calculation_version=CALCULATION_VERSION,
        confidence=confidence, reason_code=None, missing_field_name="",
        unresolvable_reason="", blocking_dispute_id=None,
        is_eligible_for_alert=True, priority_class=PriorityClass.CALCULATED,
    )


def _scheduled_vote_or_advisory_opportunity(agreement, as_of: date) -> ActionOpportunity:
    """Only called when renewal_mechanism resolves to
    affirmative_vote_required. Returns exactly one opportunity: a
    documented scheduled vote if one exists, otherwise an advisory
    agenda-watch window if end_date is available, otherwise an
    unresolved advisory-typed result explaining neither is available.
    """
    vote_res = _resolve_operative_fact(agreement, "scheduled_vote_date", as_of)
    if vote_res.fact is not None:
        return ActionOpportunity(
            agreement_id=agreement.pk, action_type=ActionType.SCHEDULED_VOTE,
            action_date=vote_res.fact.value_date, is_documented_event=True, is_advisory=False,
            input_fact_ids=(vote_res.fact.pk,), source_document_ids=_document_ids(vote_res.fact),
            formula_id="scheduled_vote_passthrough_v1", calculation_version=CALCULATION_VERSION,
            confidence=Confidence.HIGH, reason_code=None, missing_field_name="",
            unresolvable_reason="", blocking_dispute_id=None,
            is_eligible_for_alert=True, priority_class=PriorityClass.DOCUMENTED,
        )
    if vote_res.exclusion_reason in ("required_fact_disputed", "required_fact_unspecified_basis"):
        return _unresolved_from_resolution(
            agreement, ActionType.SCHEDULED_VOTE, vote_res, field_name="scheduled_vote_date",
            formula_id="scheduled_vote_passthrough_v1", is_documented_event=True,
            priority_class=PriorityClass.DOCUMENTED,
        )

    # No documented vote at all (not even a disputed candidate) -- fall
    # through to the advisory window. Never populates a documented-event
    # field: is_documented_event is always False from here on.
    end_date_res = _resolve_operative_fact(agreement, "end_date", as_of)
    if end_date_res.fact is None:
        result = _unresolved_from_resolution(
            agreement, ActionType.ADVISORY_AGENDA_WATCH, end_date_res, field_name="end_date",
            formula_id="advisory_watch_window_v1", is_advisory=True,
            priority_class=PriorityClass.ADVISORY,
        )
        if end_date_res.exclusion_reason == "required_fact_missing":
            # Neither a scheduled vote nor an expiration date exists at
            # all -- a more specific reason than the generic "missing
            # end_date" text, per requirement 5's spirit of never
            # silently reaching for expiration as a stand-in deadline.
            result = dataclasses.replace(
                result,
                reason_code=ReasonCode.NO_EXPIRATION_DATE_FOR_ADVISORY_WINDOW,
                unresolvable_reason=_reason_text(ReasonCode.NO_EXPIRATION_DATE_FOR_ADVISORY_WINDOW),
            )
        return result

    advisory_date = end_date_res.fact.value_date - timedelta(days=DEFAULT_AGENDA_WATCH_LEAD_DAYS)
    return ActionOpportunity(
        agreement_id=agreement.pk, action_type=ActionType.ADVISORY_AGENDA_WATCH,
        action_date=advisory_date, is_documented_event=False, is_advisory=True,
        input_fact_ids=(end_date_res.fact.pk,), source_document_ids=_document_ids(end_date_res.fact),
        formula_id="advisory_watch_window_v1", calculation_version=CALCULATION_VERSION,
        confidence=Confidence.LOW, reason_code=None, missing_field_name="",
        unresolvable_reason="", blocking_dispute_id=None,
        is_eligible_for_alert=True, priority_class=PriorityClass.ADVISORY,
    )


def _unknown_mechanism_opportunity(
    agreement, mechanism_res: _Resolution, *, reason_code: ReasonCode | None = None,
) -> ActionOpportunity:
    code = reason_code or _RESOLUTION_REASON_CODE[mechanism_res.exclusion_reason]
    input_fact_ids = (mechanism_res.fact.pk,) if mechanism_res.fact is not None else ()
    return ActionOpportunity(
        agreement_id=agreement.pk, action_type=ActionType.UNKNOWN_MECHANISM, action_date=None,
        is_documented_event=False, is_advisory=False,
        input_fact_ids=input_fact_ids,
        source_document_ids=_document_ids(mechanism_res.fact) if mechanism_res.fact else (),
        formula_id="unknown_mechanism_v1", calculation_version=CALCULATION_VERSION,
        confidence=Confidence.NONE, reason_code=code, missing_field_name="renewal_mechanism",
        unresolvable_reason=_reason_text(code, "renewal_mechanism"),
        blocking_dispute_id=mechanism_res.blocking_dispute_id,
        is_eligible_for_alert=_agreement_has_any_active_fact(agreement),
        priority_class=PriorityClass.UNKNOWN,
    )


def compute_action_opportunities(agreement, as_of: date | None = None) -> list[ActionOpportunity]:
    """Every currently-computable action opportunity for `agreement` as of
    `as_of` (defaults to today). Deterministic: a pure function of the
    agreement's fact set, `as_of`, and CALCULATION_VERSION -- no randomness,
    no reliance on anything but what's already in the database.
    """
    as_of = as_of or date.today()
    opportunities: list[ActionOpportunity] = []

    mechanism_res = _resolve_operative_fact(agreement, "renewal_mechanism", as_of)
    if mechanism_res.fact is None:
        opportunities.append(_unknown_mechanism_opportunity(agreement, mechanism_res))
    elif mechanism_res.fact.value_text == RENEWAL_MECHANISM_AUTO_RENEW:
        opportunities.append(_notice_based_opportunity(
            agreement, as_of, action_type=ActionType.NON_RENEWAL_NOTICE,
            notice_machine_key="non_renewal_notice_days", formula_id="auto_renew_notice_v1",
            always_report=True,
        ))
    elif mechanism_res.fact.value_text == RENEWAL_MECHANISM_AFFIRMATIVE_VOTE:
        opportunities.append(_scheduled_vote_or_advisory_opportunity(agreement, as_of))
    else:
        opportunities.append(_unknown_mechanism_opportunity(
            agreement, mechanism_res, reason_code=ReasonCode.RENEWAL_MECHANISM_UNRECOGNIZED,
        ))

    termination = _notice_based_opportunity(
        agreement, as_of, action_type=ActionType.TERMINATION_FOR_CONVENIENCE,
        notice_machine_key="termination_for_convenience_notice_days",
        formula_id="termination_for_convenience_v1",
    )
    if termination is not None:
        opportunities.append(termination)

    return opportunities


def select_primary_opportunity(opportunities: list[ActionOpportunity]) -> ActionOpportunity | None:
    """v1's rule for which single opportunity to highlight, kept entirely
    separate from computing what exists -- a v2 UI can change this
    without touching compute_action_opportunities. Only resolved
    opportunities (action_date is not None) are eligible; if none
    resolved, returns None (never substitutes an unresolved placeholder).

    Documented events (priority_class=0) always outrank calculated
    deadlines (1) always outrank advisory recommendations (2), regardless
    of date -- a low-confidence advisory can never displace a known
    deadline or scheduled vote. Within the same class, the earlier date
    wins; identical dates fall back to non_renewal_notice outranking
    termination_for_convenience, then action_type as a final
    deterministic tiebreaker.
    """
    resolved = [o for o in opportunities if o.action_date is not None]
    if not resolved:
        return None

    def sort_key(o: ActionOpportunity):
        return (
            o.priority_class,
            o.action_date,
            0 if o.action_type == ActionType.NON_RENEWAL_NOTICE else 1,
            o.action_type.value,
        )

    return sorted(resolved, key=sort_key)[0]
