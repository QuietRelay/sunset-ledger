import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction

from registry.models import RequiredFactSet


pytestmark = pytest.mark.django_db


def test_unconditional_requirement(end_date_field):
    rule = RequiredFactSet.objects.create(
        purpose=RequiredFactSet.Purpose.DEADLINE_CONFIDENCE, field=end_date_field,
    )
    assert rule.required_when_field is None


def test_conditional_requirement(end_date_field, notice_days_field):
    rule = RequiredFactSet.objects.create(
        purpose=RequiredFactSet.Purpose.DEADLINE_CONFIDENCE, field=notice_days_field,
        required_when_field=end_date_field, required_when_value="auto_renew_unless_cancelled",
    )
    assert rule.required_when_value == "auto_renew_unless_cancelled"


def test_required_when_field_and_value_must_be_set_together(end_date_field, notice_days_field):
    with pytest.raises(ValidationError):
        RequiredFactSet.objects.create(
            purpose=RequiredFactSet.Purpose.DEADLINE_CONFIDENCE, field=notice_days_field,
            required_when_field=end_date_field,
        )
    with pytest.raises(ValidationError):
        RequiredFactSet.objects.create(
            purpose=RequiredFactSet.Purpose.DEADLINE_CONFIDENCE, field=notice_days_field,
            required_when_value="some_value",
        )


def test_duplicate_rule_rejected(end_date_field):
    RequiredFactSet.objects.create(purpose=RequiredFactSet.Purpose.RECORD_COMPLETENESS, field=end_date_field)
    with pytest.raises(ValidationError):
        RequiredFactSet.objects.create(purpose=RequiredFactSet.Purpose.RECORD_COMPLETENESS, field=end_date_field)


def test_duplicate_rule_rejected_by_database_independent_of_validation(end_date_field):
    RequiredFactSet._base_manager.bulk_create([
        RequiredFactSet(purpose=RequiredFactSet.Purpose.RECORD_COMPLETENESS, field=end_date_field)
    ])
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            RequiredFactSet._base_manager.bulk_create([
                RequiredFactSet(purpose=RequiredFactSet.Purpose.RECORD_COMPLETENESS, field=end_date_field)
            ])


def test_same_field_different_purpose_is_not_a_duplicate(end_date_field):
    RequiredFactSet.objects.create(purpose=RequiredFactSet.Purpose.DEADLINE_CONFIDENCE, field=end_date_field)
    RequiredFactSet.objects.create(purpose=RequiredFactSet.Purpose.RECORD_COMPLETENESS, field=end_date_field)
    assert RequiredFactSet.objects.filter(field=end_date_field).count() == 2
