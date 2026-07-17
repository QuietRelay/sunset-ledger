"""
Postgres-only. Proves that row-level security -- not the Python-level
guard in registry.services.system_fields -- is the actual authority over
machine-field writes for a non-owning role (app_runtime), and that the
guard context can never be used to bypass it. Every test in
TestAppRuntimeCannotBypassRlsViaGuardContext deliberately wraps its write
attempt in allow_system_field_mutation() specifically to prove that
context has no bearing on what the database permits.

Skipped entirely on SQLite, which has no RLS/role concept -- the
equivalent SQLite-only positive controls (the guard genuinely permitting
a write when it's the only enforcement layer) live in test_fact_field.py
and test_fact_field_bulk_operations.py.

Root-cause note on why save() surfaces as IntegrityError rather than a
clean rejection: Django's Model._save_table() (django/db/models/base.py)
tries an UPDATE first and only falls back to INSERT "if that doesn't
update anything" (its own comment). RLS's USING clause makes a
machine-category row invisible to app_runtime's UPDATE policy, so the
UPDATE matches zero rows *without erroring* -- Postgres does not
distinguish "no such row" from "row exists but you can't see it" for a
permissive USING-clause mismatch. Django reads that as "the row doesn't
exist yet" and attempts an INSERT, which then collides with the
already-existing primary key. Confirmed by reading _save_table's source
directly, not inferred from symptoms alone -- this is a real Django/RLS
interaction, not test isolation, fixture state, or object state.
"""

import os

import pytest
from django.db import DatabaseError, IntegrityError

from registry.models import FactField
from registry.services import system_fields
from registry.tests._db_helpers import is_postgres

pytestmark = [
    pytest.mark.django_db,
    pytest.mark.skipif(not is_postgres(), reason="Postgres RLS has no SQLite equivalent"),
]


def _attempt(fn):
    """Postgres surfaces an RLS rejection differently per operation: a
    USING-clause mismatch (row exists but isn't targetable) silently
    matches zero rows; a WITH CHECK violation (row targetable, but the
    new values are disallowed) raises. Both are acceptable proof the
    write had no effect -- only the persisted state is asserted after."""
    try:
        fn()
    except DatabaseError:
        pass


class TestAppRuntimeCannotBypassRlsViaGuardContext:
    def test_save_cannot_modify_a_machine_row(self):
        field = FactField.objects.get(machine_key="end_date")
        original = field.description
        with system_fields.allow_system_field_mutation():
            field.description = "attempted change"
            with pytest.raises(IntegrityError):
                field.save()
        field.refresh_from_db()
        assert field.description == original

    def test_delete_cannot_remove_a_machine_row(self):
        with system_fields.allow_system_field_mutation():
            _attempt(FactField.objects.get(machine_key="end_date").delete)
        assert FactField.objects.filter(machine_key="end_date").exists()

    def test_bulk_create_cannot_introduce_a_machine_row(self):
        with system_fields.allow_system_field_mutation():
            _attempt(lambda: FactField.objects.bulk_create([
                FactField(
                    code="pg_bulk_created_machine", machine_key="pg_escape_attempt",
                    category=FactField.Category.MACHINE, value_type=FactField.ValueType.DATE,
                )
            ]))
        assert not FactField.objects.filter(code="pg_bulk_created_machine").exists()

    def test_bulk_update_cannot_modify_a_machine_row(self):
        field = FactField.objects.get(machine_key="end_date")
        original = field.description
        with system_fields.allow_system_field_mutation():
            field.description = "attempted bulk_update change"
            _attempt(lambda: FactField.objects.bulk_update([field], ["description"]))
        field.refresh_from_db()
        assert field.description == original

    def test_queryset_update_cannot_modify_a_machine_row(self):
        original = FactField.objects.get(machine_key="end_date").description
        with system_fields.allow_system_field_mutation():
            _attempt(lambda: FactField.objects.filter(machine_key="end_date").update(
                description="attempted queryset update"
            ))
        assert FactField.objects.get(machine_key="end_date").description == original

    def test_queryset_delete_cannot_remove_a_machine_row(self):
        with system_fields.allow_system_field_mutation():
            _attempt(lambda: FactField.objects.filter(machine_key="end_date").delete())
        assert FactField.objects.filter(machine_key="end_date").exists()

    def test_cannot_flip_machine_row_into_descriptive(self):
        # USING-clause mismatch (existing row is category='machine', fails
        # app_runtime's UPDATE policy) -- same silent-then-insert-collision
        # mechanism as test_save_cannot_modify_a_machine_row above.
        field = FactField.objects.get(machine_key="end_date")
        with system_fields.allow_system_field_mutation():
            field.category = FactField.Category.DESCRIPTIVE
            field.machine_key = None
            with pytest.raises(IntegrityError):
                field.save()
        field.refresh_from_db()
        assert field.category == FactField.Category.MACHINE

    def test_cannot_flip_descriptive_row_into_machine(self):
        # WITH CHECK violation, not a USING mismatch: the row *is*
        # currently descriptive (visible/targetable), but the new values
        # would set category='machine', which fails the UPDATE policy's
        # WITH CHECK (category='descriptive') -- Postgres raises for this
        # directly rather than silently matching zero rows.
        descriptive = FactField.objects.create(
            code="pg_flip_target", category=FactField.Category.DESCRIPTIVE,
            value_type=FactField.ValueType.TEXT,
        )
        with system_fields.allow_system_field_mutation():
            descriptive.category = FactField.Category.MACHINE
            descriptive.machine_key = "pg_escape_attempt_2"
            with pytest.raises(DatabaseError):
                descriptive.save()
        descriptive.refresh_from_db()
        assert descriptive.category == FactField.Category.DESCRIPTIVE

    def test_descriptive_field_writes_still_succeed(self):
        # RLS is a scoped restriction, not a blanket lockout -- app_runtime
        # must still be able to do its ordinary job on descriptive fields.
        field = FactField.objects.create(
            code="pg_descriptive_control", category=FactField.Category.DESCRIPTIVE,
            value_type=FactField.ValueType.TEXT,
        )
        field.description = "ordinary edit"
        field.save()
        field.refresh_from_db()
        assert field.description == "ordinary edit"
        field.delete()
        assert not FactField.objects.filter(code="pg_descriptive_control").exists()


class TestMigratorRoleIntegration:
    """Django's ORM in this test process is bound to whatever role the
    suite itself connected as (app_runtime, in CI) -- proving migrator has
    genuinely different privileges requires a second, independent
    connection authenticated as migrator, not the ORM's default one."""

    def _migrator_connection(self):
        import psycopg
        dsn = os.environ.get("MIGRATOR_DATABASE_URL")
        if not dsn:
            pytest.skip("MIGRATOR_DATABASE_URL not set -- see .github/workflows/test.yml")
        return psycopg.connect(dsn)

    def test_migrations_seeded_exactly_the_nine_machine_fields(self):
        # Readable via app_runtime's own connection too -- RLS's SELECT
        # policy is open to everyone regardless of who wrote a row.
        machine_fields = FactField.objects.filter(category=FactField.Category.MACHINE)
        assert machine_fields.count() == 9
        assert set(machine_fields.values_list("machine_key", flat=True)) == set(FactField.MACHINE_KEYS)

    def test_migrator_can_alter_a_machine_field_and_app_runtime_still_cannot_afterward(self):
        with self._migrator_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE registry_factfield SET description = %s WHERE machine_key = %s",
                    ("altered by migrator directly", "start_date"),
                )
            conn.commit()

        # migrator's write took effect -- confirmed via app_runtime's own
        # connection (SELECT is open to everyone under RLS).
        field = FactField.objects.get(machine_key="start_date")
        assert field.description == "altered by migrator directly"

        # app_runtime still cannot write to it, even after migrator's change,
        # and even inside the Python guard context.
        with system_fields.allow_system_field_mutation():
            field.description = "attempted app_runtime edit after migrator's change"
            with pytest.raises(IntegrityError):
                field.save()
        field.refresh_from_db()
        assert field.description == "altered by migrator directly"
