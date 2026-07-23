"""
The only two sanctioned ways to transition an existing Fact: retracting
it (entry-error correction) or superseding it (a documented change in
the underlying terms). Both only ever touch Fact's narrow mutable
lifecycle fields (status, valid_until, retraction_reason, retracted_by,
retracted_at) -- never its evidentiary identity fields (agreement,
field, qualifier, the typed value, scope_period, valid_from,
primary_document, page_or_section_reference, excerpt,
effective_date_basis, created_by), which Fact's own save()/clean() and
QuerySet guards refuse to let anyone change on an existing row, by
anyone, with no bypass (see registry.models.Fact and
registry.services.integrity.LockedFieldMutationNotAllowed).

Because the mutable and identity field sets are disjoint by construction,
neither function here needs an escape-hatch context manager the way
FactField's migration-seeding guard does -- there is nothing for these
functions to bypass. They exist to give retraction and supersession a
single, named, obvious call site instead of ad hoc attribute assignment
scattered across admin code or (eventually) a moderation workflow.
"""

from django.utils import timezone


def retract_fact(fact, *, reason, retracted_by, retracted_at=None):
    """Mark an existing fact as retracted (entry error), never deleted --
    the row and its full history stay queryable."""
    fact.status = fact.Status.RETRACTED
    fact.retraction_reason = reason
    fact.retracted_by = retracted_by
    fact.retracted_at = retracted_at or timezone.now()
    fact.save()
    return fact


def supersede_fact(old_fact, **new_fact_fields):
    """Create a new fact that supersedes `old_fact`. `old_fact` itself is
    never edited in place beyond what Fact.save()'s own supersession
    close-out does (setting its valid_until once the new fact is saved)
    -- callers should not attempt to also mutate `old_fact` directly.
    """
    from registry.models import Fact

    new_fact_fields.setdefault("agreement", old_fact.agreement)
    new_fact_fields.setdefault("field", old_fact.field)
    new_fact = Fact(supersedes=old_fact, **new_fact_fields)
    new_fact.save()
    return new_fact
