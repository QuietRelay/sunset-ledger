"""
Application-level enforcement of the machine-field immutability rule,
independent of database backend.

Postgres additionally enforces this at the database layer via row-level
security (see registry/migrations for the policy-creation migration and
docs/schema-spec.md, "Machine-field database protection"). This module is
what gives SQLite dev/test environments the identical rule, and is
defense in depth even on Postgres, where the database would already have
refused the write.

The only sanctioned caller of allow_system_field_mutation() is the data
migration that seeds the nine frozen machine fact_field rows. Nothing in
application request-handling code should ever call it.
"""

import contextvars
from contextlib import contextmanager

_mutation_allowed: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "system_field_mutation_allowed", default=False
)


class SystemFieldMutationNotAllowed(Exception):
    """Raised when code outside a sanctioned migration context attempts to
    create, modify, or delete a migration-managed (category='machine')
    fact_field row."""


@contextmanager
def allow_system_field_mutation():
    token = _mutation_allowed.set(True)
    try:
        yield
    finally:
        _mutation_allowed.reset(token)


def mutation_allowed() -> bool:
    return _mutation_allowed.get()
