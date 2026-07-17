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

Full, independent protection arrives with the `fact` slice, once
`fact.primary_document_id` is a real PROTECT-ed reference.
"""


class RecordDeletionNotAllowed(Exception):
    """Raised by Agreement/Document .delete() and their QuerySets --
    these records are never hard-deleted in v1."""
