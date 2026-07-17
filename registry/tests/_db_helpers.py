"""
Small helpers for tests whose expected behavior legitimately differs by
backend and role -- NOT a general testing utility, deliberately narrow.

On SQLite there is no role/RLS concept at all, so
registry.services.system_fields.allow_system_field_mutation() is the
*only* enforcement layer, and it is expected to genuinely permit a
deliberate machine-field write.

On Postgres, row-level security (see registry/migrations/0002) is the
actual authority for a non-owning role (app_runtime in production and in
CI's "Test as app_runtime" step). The Python guard context must NEVER be
able to override RLS -- if it did, RLS would be decorative, not a real
enforcement boundary. Tests that assert the guard context "permits" a
write must not run unmodified against that role; see
test_fact_field_rls_postgres.py.
"""

from django.db import connection


def is_postgres() -> bool:
    return connection.vendor == "postgresql"


def current_db_role() -> str | None:
    """None on SQLite. On Postgres, the actual connected role (e.g.
    'app_runtime' or 'migrator'), read from the live connection rather
    than assumed from configuration -- this is what the test suite is
    *actually* authenticated as, not what a settings file claims."""
    if not is_postgres():
        return None
    with connection.cursor() as cursor:
        cursor.execute("SELECT current_user")
        return cursor.fetchone()[0]
