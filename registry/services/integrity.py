"""
Deletion-integrity guard for entities that must never be hard-deleted in
v1, even before anything technically references them yet.

Agreement and Document rows are protected from deletion two ways:
PROTECT on every FK pointing at them from this slice's other tables (real
protection once a relationship exists), and this unconditional guard
(covering the gap requirement 6 explicitly calls out: a freshly-created,
currently-unreferenced row that PROTECT alone wouldn't stop). Unlike
FactField's machine-field guard, there is no legitimate bypass scenario
here (no migration ever needs to delete an agreement or document), so
there is no escape-hatch context manager -- this is unconditional.

Fact, FactDispute, FactDisputeMember, Verification, and VerificationFact
(the fact/verification slice) are permanent evidentiary/audit records for
the same reason and use the shared mixin/queryset below rather than
repeating Agreement/Document's hand-written versions -- by this point the
pattern has repeated enough times that sharing it is a cleanup, not a
premature abstraction. Agreement and Document's own already-committed,
already-tested delete() overrides are left exactly as they are rather
than retrofitted to use this mixin, since that slice is not being
reopened.
"""

from django.db import models


class RecordDeletionNotAllowed(Exception):
    """Raised by Agreement/Document/Fact/FactDispute/FactDisputeMember/
    Verification/VerificationFact .delete() and their QuerySets -- these
    records are never hard-deleted in v1."""


class UndeletableQuerySet(models.QuerySet):
    """QuerySet.delete() bypasses an instance-level delete() guard
    entirely -- this closes that gap for models using
    UndeletableModelMixin. Set `objects = UndeletableQuerySet.as_manager()`
    on the model."""

    def delete(self):
        raise RecordDeletionNotAllowed(
            f"{self.model.__name__} rows are never hard-deleted in v1 -- see docs/schema-spec.md."
        )


class UndeletableModelMixin:
    """Mix in first (before models.Model) on any model whose rows must
    never be hard-deleted. Pairs with UndeletableQuerySet for the bulk
    path -- both are needed; neither alone is sufficient."""

    def delete(self, *args, **kwargs):
        raise RecordDeletionNotAllowed(
            f"{type(self).__name__} rows are never hard-deleted in v1 -- see docs/schema-spec.md."
        )


class LockedFieldMutationNotAllowed(Exception):
    """Raised when an attempt is made to change a field that is locked
    once a precondition holds -- Document's cited-document fields
    (archived_storage_key/content_sha256/file_size_bytes/mime_type, once
    any Fact/FactCorroboration cites the row) and Fact's evidentiary
    identity fields (agreement/field/qualifier/value/scope_period/
    valid_from/primary_document/page_or_section_reference/excerpt/
    effective_date_basis/created_by, unconditionally once the row
    exists). Both are one exception class because the shape of the
    violation is identical -- a locked field targeted by
    QuerySet.update()/bulk_update(), which bypass save()/clean() and
    therefore need their own guard, exactly like RecordDeletionNotAllowed
    above needs both an instance and a QuerySet path.

    Unlike FactField's machine-field guard, neither of these has a
    legitimate bypass scenario -- there is no context manager here.
    Corrections always mean creating a new row (a new Document version
    linked via DocumentRelationship, or a replacement Fact via
    registry.services.fact_lifecycle.supersede_fact), never editing the
    locked fields of an existing one in place.
    """
