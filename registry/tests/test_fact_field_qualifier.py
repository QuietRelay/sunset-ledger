import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction

from registry.models import FactFieldQualifier

pytestmark = pytest.mark.django_db


def test_qualifier_scoped_to_its_field(notice_days_field):
    qualifier = FactFieldQualifier.objects.create(field=notice_days_field, qualifier_key="option_a")
    assert qualifier in notice_days_field.qualifiers.all()


def test_same_qualifier_key_allowed_across_different_fields(end_date_field, notice_days_field):
    FactFieldQualifier.objects.create(field=end_date_field, qualifier_key="option_a")
    FactFieldQualifier.objects.create(field=notice_days_field, qualifier_key="option_a")
    assert FactFieldQualifier.objects.filter(qualifier_key="option_a").count() == 2


def test_duplicate_qualifier_key_within_same_field_rejected(notice_days_field):
    # save() calls full_clean(), so Django's own validate_unique() catches
    # this before any SQL is sent -- ValidationError on the ordinary
    # create() path.
    FactFieldQualifier.objects.create(field=notice_days_field, qualifier_key="option_a")
    with pytest.raises(ValidationError):
        FactFieldQualifier.objects.create(field=notice_days_field, qualifier_key="option_a")


def test_duplicate_qualifier_key_rejected_by_database_independent_of_validation(notice_days_field):
    FactFieldQualifier._base_manager.bulk_create([
        FactFieldQualifier(field=notice_days_field, qualifier_key="option_a")
    ])
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            FactFieldQualifier._base_manager.bulk_create([
                FactFieldQualifier(field=notice_days_field, qualifier_key="option_a")
            ])
