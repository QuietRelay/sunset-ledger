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

Things confirmed against real runs, not assumed:

1. Exception class. Where an operation does raise, it's `ProgrammingError`
   (a `DatabaseError` subclass), not `IntegrityError` specifically --
   assert the broad `django.db.DatabaseError`, since the exact
   sub-mechanism (an RLS policy violation vs. a downstream primary-key
   collision) can legitimately differ per operation and per which values
   are involved, and both are DatabaseError.

2. Transaction isolation. Postgres aborts the *entire* enclosing
   transaction after any error within it (unlike SQLite) -- without an
   inner savepoint, a test's own follow-up assertions (refresh_from_db(),
   a confirming SELECT) would themselves fail with "current transaction
   is aborted" rather than actually verifying anything. Every operation
   confirmed to raise is wrapped in its own transaction.atomic(), which
   rolls back to a savepoint automatically when the exception propagates
   out, leaving the outer test transaction usable again.

3. Not every rejected write raises at all. `Model.save()` and
   `bulk_create()` do (see `_attempt_raises` below). `Model.delete()`,
   `bulk_update()`, `QuerySet.update()`, and `QuerySet.delete()` do NOT --
   confirmed by an actual failing run where three tests failed with
   "DID NOT RAISE DatabaseError" precisely because RLS's USING clause
   just filters the target row out of the statement entirely, leaving a
   clean, non-erroring zero-row result with nothing to catch (see
   `_attempt_noop` below). The dividing line is whether the operation has
   anything resembling save()'s update-then-insert fallback to collide
   with -- these four don't.

Root-cause note on the INSERT-fallback behavior (still accurate, only the
expected exception class was wrong): Django's Model._save_table()
(django/db/models/base.py) tries an UPDATE first and only falls back to
INSERT "if that doesn't update anything" (its own comment). RLS's USING
clause makes a machine-category row invisible to app_runtime's UPDATE
policy, so the UPDATE matches zero rows *without erroring* -- Postgres
does not distinguish "no such row" from "row exists but you can't see
it" for a permissive USING-clause mismatch. Django reads that as "the row
doesn't exist yet" and attempts an INSERT with the existing primary key,
which Postgres then rejects -- either the INSERT policy's WITH CHECK
clause or plain primary-key uniqueness, depending on what values are
being inserted. This is understood, confirmed behavior; it does not
require force_update or any model change.
"""

import os

import pytest
from django.db import DatabaseError, transaction
from django.core.exceptions import ValidationError

from registry.models import FactField
from registry.services import system_fields
from registry.tests._db_helpers import is_postgres

pytestmark = [
    pytest.mark.django_db,
    pytest.mark.skipif(not is_postgres(), reason="Postgres RLS has no SQLite equivalent"),
]


def _attempt_raises(fn):
    """Run fn expecting a DatabaseError, inside its own savepoint.

    Confirmed against a real run to apply to save() (via its
    update-then-insert fallback colliding with RLS or the primary key)
    and bulk_create() (a WITH CHECK violation on INSERT, evaluated
    against the row being inserted, which Postgres rejects immediately
    rather than silently).

    The savepoint (transaction.atomic() nested inside the test's own
    outer transaction) is not optional: Postgres aborts the whole
    enclosing transaction after any error, so without it, the assertions
    that follow this call would themselves fail with "current transaction
    is aborted" instead of actually checking anything. atomic() rolls
    back to the savepoint automatically once the exception propagates out
    of the `with` block, leaving the outer transaction usable again.
    """
    with pytest.raises(DatabaseError):
        with transaction.atomic():
            fn()


def _attempt_noop(fn):
    """Run fn tolerating a DatabaseError, expecting none.

    Confirmed against a real run: Model.delete(), bulk_update(),
    QuerySet.update(), and QuerySet.delete() do NOT raise when their
    target row is filtered out by RLS's USING clause -- the statement
    executes, matches zero rows, and returns completely normally, no
    exception, no aborted transaction. None of these have an equivalent
    to save()'s update-then-insert fallback, so there is nothing for a
    zero-row result to collide with. No savepoint is needed since nothing
    raises; the `except` here is defensive, not the real assertion -- the
    real proof is the unchanged-state check that follows this call.
    """
    try:
        fn()
    except DatabaseError:
        pass


class TestAppRuntimeCannotBypassRlsViaGuardContext:
    def test_save_cannot_modify_a_machine_row(self):
        field = FactField.objects.get(machine_key="end_date")
        original_description = field.description
        with system_fields.allow_system_field_mutation():
            field.description = "attempted change"
            _attempt_raises(field.save)
        field.refresh_from_db()
        assert field.category == FactField.Category.MACHINE
        assert field.machine_key == "end_date"
        assert field.description == original_description

    def test_delete_cannot_remove_a_machine_row(self):
        with system_fields.allow_system_field_mutation():
            _attempt_noop(FactField.objects.get(machine_key="end_date").delete)
        field = FactField.objects.get(machine_key="end_date")
        assert field.category == FactField.Category.MACHINE
        assert field.machine_key == "end_date"

    def test_bulk_create_cannot_introduce_a_machine_row(self):
        with system_fields.allow_system_field_mutation():
            _attempt_raises(lambda: FactField.objects.bulk_create([
                FactField(
                    code="pg_bulk_created_machine", machine_key="pg_escape_attempt",
                    category=FactField.Category.MACHINE, value_type=FactField.ValueType.DATE,
                )
            ]))
        assert not FactField.objects.filter(code="pg_bulk_created_machine").exists()

    def test_bulk_update_cannot_modify_a_machine_row(self):
        field = FactField.objects.get(machine_key="end_date")
        original_description = field.description
        with system_fields.allow_system_field_mutation():
            field.description = "attempted bulk_update change"
            _attempt_noop(lambda: FactField.objects.bulk_update([field], ["description"]))
        field.refresh_from_db()
        assert field.category == FactField.Category.MACHINE
        assert field.machine_key == "end_date"
        assert field.description == original_description

    def test_queryset_update_cannot_modify_a_machine_row(self):
        original_description = FactField.objects.get(machine_key="end_date").description
        with system_fields.allow_system_field_mutation():
            _attempt_noop(lambda: FactField.objects.filter(machine_key="end_date").update(
                description="attempted queryset update"
            ))
        field = FactField.objects.get(machine_key="end_date")
        assert field.category == FactField.Category.MACHINE
        assert field.machine_key == "end_date"
        assert field.description == original_description

    def test_queryset_delete_cannot_remove_a_machine_row(self):
        with system_fields.allow_system_field_mutation():
            _attempt_noop(lambda: FactField.objects.filter(machine_key="end_date").delete())
        field = FactField.objects.get(machine_key="end_date")
        assert field.category == FactField.Category.MACHINE
        assert field.machine_key == "end_date"

    def test_cannot_flip_machine_row_into_descriptive(self):
        # Existing row's category is 'machine' -- fails app_runtime's
        # UPDATE USING clause (row not targetable at all), same silent
        # zero-row-then-insert-collision mechanism as the save() test
        # above.
        field = FactField.objects.get(machine_key="end_date")
        with system_fields.allow_system_field_mutation():
            field.category = FactField.Category.DESCRIPTIVE
            field.machine_key = None
            _attempt_raises(field.save)
        field.refresh_from_db()
        assert field.category == FactField.Category.MACHINE
        assert field.machine_key == "end_date"

    def test_cannot_flip_descriptive_row_into_machine_via_save(self):
        # This is caught by FactField.clean() -- machine_key must be one
        # of the frozen MACHINE_KEYS -- entirely at the *application*
        # layer, before any SQL is sent. It is a genuinely different,
        # earlier line of defense than RLS, and must not be conflated
        # with it: this test proves validation catches it; the next test
        # proves RLS *independently* catches the same transition when
        # validation is bypassed entirely.
        descriptive = FactField.objects.create(
            code="pg_flip_target_via_save", category=FactField.Category.DESCRIPTIVE,
            value_type=FactField.ValueType.TEXT,
        )
        with system_fields.allow_system_field_mutation():
            descriptive.category = FactField.Category.MACHINE
            descriptive.machine_key = "pg_escape_attempt_2"
            with pytest.raises(ValidationError):
                descriptive.save()
        descriptive.refresh_from_db()
        assert descriptive.category == FactField.Category.DESCRIPTIVE

    def test_rls_independently_blocks_descriptive_to_machine_flip_when_validation_is_bypassed(self):
        # QuerySet.update() never calls clean()/full_clean() -- this
        # bypasses the application-level validation from the test above
        # entirely, so a rejection here can only be RLS: the row is
        # currently descriptive (visible/targetable under the UPDATE
        # USING clause), but the new value (category='machine') violates
        # the UPDATE policy's WITH CHECK clause, which Postgres enforces
        # regardless of what Django-level validation would have said.
        descriptive = FactField.objects.create(
            code="pg_flip_target_bypass_validation", category=FactField.Category.DESCRIPTIVE,
            value_type=FactField.ValueType.TEXT,
        )
        with system_fields.allow_system_field_mutation():
            _attempt_raises(lambda: FactField.objects.filter(pk=descriptive.pk).update(
                category=FactField.Category.MACHINE
            ))
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
        # connection (SELECT is open to everyone under RLS). This
        # assertion is preserved unchanged from the previous version; only
        # the follow-up app_runtime failure expectation below changed.
        field = FactField.objects.get(machine_key="start_date")
        assert field.description == "altered by migrator directly"

        # app_runtime still cannot write to it, even after migrator's
        # change, and even inside the Python guard context.
        with system_fields.allow_system_field_mutation():
            field.description = "attempted app_runtime edit after migrator's change"
            _attempt_raises(field.save)
        field.refresh_from_db()
        assert field.category == FactField.Category.MACHINE
        assert field.machine_key == "start_date"
        assert field.description == "altered by migrator directly"
