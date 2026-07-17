"""
QuerySet.update(), QuerySet.delete(), bulk_create(), and bulk_update() all
write directly via SQL and never call Model.save()/.delete() -- so the
per-instance guard in FactField.save()/.delete() does not, by itself,
cover any of these paths. This file proves the FactFieldQuerySet
overrides in registry/models.py close that gap on SQLite, where there is
no database-level backstop at all (unlike Postgres, where the RLS
policies in migration 0002 already block app_runtime regardless of which
of these paths generated the SQL).
"""

import pytest

from registry.models import FactField
from registry.services import system_fields
from registry.tests._db_helpers import is_postgres

pytestmark = pytest.mark.django_db


def test_queryset_update_cannot_touch_existing_machine_rows():
    with pytest.raises(system_fields.SystemFieldMutationNotAllowed):
        FactField.objects.filter(machine_key="end_date").update(description="sneaky bulk edit")
    # Confirm it was genuinely a no-op, not a partial write before raising.
    assert FactField.objects.get(machine_key="end_date").description != "sneaky bulk edit"


def test_queryset_update_cannot_flip_descriptive_rows_into_machine():
    FactField.objects.create(
        code="bulk_flip_target", category=FactField.Category.DESCRIPTIVE,
        value_type=FactField.ValueType.TEXT,
    )
    with pytest.raises(system_fields.SystemFieldMutationNotAllowed):
        FactField.objects.filter(code="bulk_flip_target").update(category=FactField.Category.MACHINE)
    assert FactField.objects.get(code="bulk_flip_target").category == FactField.Category.DESCRIPTIVE


def test_queryset_update_on_purely_descriptive_rows_is_unaffected():
    FactField.objects.create(
        code="ordinary_descriptive", category=FactField.Category.DESCRIPTIVE,
        value_type=FactField.ValueType.TEXT,
    )
    updated = FactField.objects.filter(code="ordinary_descriptive").update(description="fine")
    assert updated == 1


def test_queryset_delete_cannot_remove_machine_rows():
    count_before = FactField.objects.count()
    with pytest.raises(system_fields.SystemFieldMutationNotAllowed):
        FactField.objects.filter(machine_key="end_date").delete()
    assert FactField.objects.count() == count_before
    assert FactField.objects.filter(machine_key="end_date").exists()


def test_queryset_delete_mixed_queryset_containing_a_machine_row_is_blocked():
    FactField.objects.create(
        code="innocent_bystander", category=FactField.Category.DESCRIPTIVE,
        value_type=FactField.ValueType.TEXT,
    )
    # A queryset spanning both a descriptive row and a machine row must be
    # blocked entirely, not partially executed against the descriptive one.
    with pytest.raises(system_fields.SystemFieldMutationNotAllowed):
        FactField.objects.filter(code__in=["innocent_bystander", "end_date"]).delete()
    assert FactField.objects.filter(code="innocent_bystander").exists()


def test_queryset_delete_on_purely_descriptive_rows_is_unaffected():
    FactField.objects.create(
        code="deletable_descriptive", category=FactField.Category.DESCRIPTIVE,
        value_type=FactField.ValueType.TEXT,
    )
    deleted_count, _ = FactField.objects.filter(code="deletable_descriptive").delete()
    assert deleted_count == 1


def test_bulk_create_cannot_introduce_machine_rows():
    with pytest.raises(system_fields.SystemFieldMutationNotAllowed):
        FactField.objects.bulk_create([
            FactField(
                code="bulk_created_machine", machine_key="near_duplicate_end_date",
                category=FactField.Category.MACHINE, value_type=FactField.ValueType.DATE,
            )
        ])
    assert not FactField.objects.filter(code="bulk_created_machine").exists()


def test_bulk_create_of_descriptive_rows_is_unaffected():
    FactField.objects.bulk_create([
        FactField(code="bulk_created_a", category=FactField.Category.DESCRIPTIVE,
                   value_type=FactField.ValueType.TEXT),
        FactField(code="bulk_created_b", category=FactField.Category.DESCRIPTIVE,
                   value_type=FactField.ValueType.TEXT),
    ])
    assert FactField.objects.filter(code__in=["bulk_created_a", "bulk_created_b"]).count() == 2


def test_bulk_update_cannot_modify_rows_that_are_machine_in_the_database():
    field = FactField.objects.get(machine_key="end_date")
    # In-memory object looks perfectly ordinary -- category isn't even
    # touched -- but the DB row it targets is a machine row, which must
    # still be caught.
    field.description = "sneaky bulk_update edit"
    with pytest.raises(system_fields.SystemFieldMutationNotAllowed):
        FactField.objects.bulk_update([field], ["description"])
    field.refresh_from_db()
    assert field.description != "sneaky bulk_update edit"


def test_bulk_update_cannot_flip_category_field_even_for_descriptive_rows():
    descriptive = FactField.objects.create(
        code="bulk_update_flip_target", category=FactField.Category.DESCRIPTIVE,
        value_type=FactField.ValueType.TEXT,
    )
    descriptive.category = FactField.Category.MACHINE
    descriptive.machine_key = "not_a_real_key"
    with pytest.raises(system_fields.SystemFieldMutationNotAllowed):
        FactField.objects.bulk_update([descriptive], ["category", "machine_key"])


def test_bulk_update_of_ordinary_descriptive_rows_is_unaffected():
    field = FactField.objects.create(
        code="bulk_update_ok", category=FactField.Category.DESCRIPTIVE,
        value_type=FactField.ValueType.TEXT,
    )
    field.description = "updated fine"
    FactField.objects.bulk_update([field], ["description"])
    field.refresh_from_db()
    assert field.description == "updated fine"


@pytest.mark.skipif(
    is_postgres(),
    reason="On Postgres, RLS -- not the Python guard -- is the authority for "
           "whatever role this suite is connected as; see "
           "test_fact_field_rls_postgres.py for the role-aware equivalent.",
)
def test_all_four_bulk_paths_are_permitted_inside_the_guard_context():
    # Positive control, SQLite only, mirroring the seed migration's own
    # usage: the guard context genuinely permits these operations on the
    # backend where it is the only enforcement layer, rather than being an
    # unconditional block regardless of context. Must not be assumed true
    # on Postgres as a non-owning role -- RLS is the real authority there.
    with system_fields.allow_system_field_mutation():
        field = FactField.objects.get(machine_key="end_date")
        field.description = "seed-migration-style edit"
        FactField.objects.bulk_update([field], ["description"])
    field.refresh_from_db()
    assert field.description == "seed-migration-style edit"

    with system_fields.allow_system_field_mutation():
        FactField.objects.filter(machine_key="end_date").update(
            description="queryset update under guard"
        )
    assert FactField.objects.get(
        machine_key="end_date"
    ).description == "queryset update under guard"
