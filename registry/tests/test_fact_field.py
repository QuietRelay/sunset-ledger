import pytest
from django.core.exceptions import ValidationError

from registry.models import FactField
from registry.services import system_fields

pytestmark = pytest.mark.django_db


def test_nine_machine_fields_are_seeded():
    machine_fields = FactField.objects.filter(category=FactField.Category.MACHINE)
    assert machine_fields.count() == 9
    assert set(machine_fields.values_list("machine_key", flat=True)) == set(FactField.MACHINE_KEYS)


def test_descriptive_field_is_freely_creatable():
    field = FactField.objects.create(
        code="camera_count",
        category=FactField.Category.DESCRIPTIVE,
        value_type=FactField.ValueType.NUMBER,
        description="Number of cameras at signing.",
    )
    field.allows_multiple_concurrent = True
    field.save()
    field.refresh_from_db()
    assert field.allows_multiple_concurrent is True


def test_cannot_create_machine_field_outside_migration_context():
    with pytest.raises(system_fields.SystemFieldMutationNotAllowed):
        FactField.objects.create(
            code="near_duplicate_notice",
            machine_key="non_renewal_notice_days_v2",
            category=FactField.Category.MACHINE,
            value_type=FactField.ValueType.NUMBER,
            is_period=True,
        )


def test_cannot_modify_existing_machine_field_outside_migration_context():
    field = FactField.objects.get(machine_key="end_date")
    field.description = "sneaky edit"
    with pytest.raises(system_fields.SystemFieldMutationNotAllowed):
        field.save()


def test_cannot_delete_machine_field_outside_migration_context():
    field = FactField.objects.get(machine_key="end_date")
    with pytest.raises(system_fields.SystemFieldMutationNotAllowed):
        field.delete()


def test_cannot_flip_descriptive_field_into_machine_to_escape_guard():
    field = FactField.objects.create(
        code="some_descriptive_field",
        category=FactField.Category.DESCRIPTIVE,
        value_type=FactField.ValueType.TEXT,
    )
    field.category = FactField.Category.MACHINE
    field.machine_key = "not_a_real_machine_key"
    with pytest.raises(system_fields.SystemFieldMutationNotAllowed):
        field.save()


def test_cannot_flip_machine_field_into_descriptive_to_escape_protection():
    field = FactField.objects.get(machine_key="end_date")
    field.category = FactField.Category.DESCRIPTIVE
    field.machine_key = None
    with pytest.raises(system_fields.SystemFieldMutationNotAllowed):
        field.save()


def test_machine_field_requires_a_frozen_machine_key():
    with system_fields.allow_system_field_mutation():
        with pytest.raises(ValidationError):
            FactField(
                code="bogus",
                machine_key="not_in_the_frozen_list",
                category=FactField.Category.MACHINE,
                value_type=FactField.ValueType.TEXT,
            ).save()


def test_descriptive_field_must_not_set_machine_key():
    with pytest.raises(ValidationError):
        FactField(
            code="bogus_descriptive",
            machine_key="end_date",
            category=FactField.Category.DESCRIPTIVE,
            value_type=FactField.ValueType.TEXT,
        ).save()


def test_within_guard_context_machine_field_writes_are_allowed():
    # Positive control: the same mechanism the seed migration relies on
    # (allow_system_field_mutation) genuinely permits a machine-field
    # write when used deliberately, rather than the guard being an
    # unconditional block dressed up as a context manager.
    field = FactField.objects.get(machine_key="end_date")
    with system_fields.allow_system_field_mutation():
        field.description = "updated inside a sanctioned migration context"
        field.save()
    field.refresh_from_db()
    assert field.description == "updated inside a sanctioned migration context"
