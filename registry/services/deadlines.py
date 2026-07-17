"""
Deadline computation -- pure logic, no Django ORM dependency beyond reading
Fact-like inputs, no I/O. This module is the frozen v1 machine-rules engine
described in docs/schema-spec.md.

Deliberately empty of model-backed logic until the registry models exist
(models.py is not implemented yet -- this file is scaffolding only, wired
up so the intended shape and its test file are visible from commit one).
"""

from dataclasses import dataclass, field
from datetime import date
from enum import Enum


class DeadlineType(str, Enum):
    NOTICE_DEADLINE_NON_RENEWAL = "notice_deadline_non_renewal"
    NOTICE_DEADLINE_TERMINATION_FOR_CONVENIENCE = "notice_deadline_termination_for_convenience"
    SCHEDULED_VOTE = "scheduled_vote"
    AGENDA_WATCH_WINDOW = "agenda_watch_window"
    UNKNOWN = "unknown"


class Confidence(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    NONE = "none"


@dataclass
class ActionOpportunity:
    """One independently-computed action date for an agreement.

    A single agreement can legitimately produce more than one of these (a
    non-renewal notice deadline and a termination-for-convenience deadline
    are separate legal mechanisms and must never fall back to one another).
    The v1 UI highlights only the entry with is_primary=True; the interface
    itself always returns the full list so v2 can surface more without a
    signature change.
    """

    deadline_type: DeadlineType
    calculated_date: date | None = None
    next_documented_public_event: date | None = None
    recommended_agenda_monitoring_start: date | None = None
    input_fact_ids: list[int] = field(default_factory=list)
    formula_id: str | None = None
    calculation_version: str | None = None
    confidence: Confidence = Confidence.NONE
    unresolvable_reason: str | None = None
    blocking_dispute_id: int | None = None
    is_primary: bool = False


def compute_action_opportunities(agreement, as_of: date | None = None) -> list[ActionOpportunity]:
    """Return every currently-computable action opportunity for `agreement`.

    Not implemented yet -- registry.models does not exist. This signature
    and the dataclass above are frozen per docs/schema-spec.md; filling in
    the body is the first task of actual implementation, not scaffolding.
    """
    raise NotImplementedError(
        "compute_action_opportunities: implementation follows registry models, not scaffolding"
    )
