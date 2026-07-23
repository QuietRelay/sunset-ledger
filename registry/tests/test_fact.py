import datetime

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction

from registry.models import Fact, FactField, FactFieldQualifier
from registry.services.integrity import RecordDeletionNotAllowed

pytestmark = pytest.mark.django_db


def make_fact(agreement, field, document, **overrides):
    defaults = dict(
        agreement=agreement, field=field, primary_document=document,
        valid_from=datetime.date(2024, 1, 1),
        effective_date_basis=Fact.EffectiveDateBasis.STATED_IN_DOCUMENT,
    )
    defaults.update(overrides)
    return Fact.objects.create(**defaults)


class TestExactlyOneValue:
    def test_date_field_requires_value_date(self, agreement, end_date_field, document):
        with pytest.raises(ValidationError):
            make_fact(agreement, end_date_field, document, value_text="2026-01-01")
        fact = make_fact(agreement, end_date_field, document, value_date=datetime.date(2026, 1, 1))
        assert fact.value_date == datetime.date(2026, 1, 1)

    def test_number_field_requires_value_number(self, agreement, notice_days_field, document):
        with pytest.raises(ValidationError):
            make_fact(agreement, notice_days_field, document, value_date=datetime.date(2026, 1, 1))
        fact = make_fact(
            agreement, notice_days_field, document, value_number=90,
            period_unit=Fact.PeriodUnit.CALENDAR_DAYS,
        )
        assert fact.value_number == 90

    def test_more_than_one_value_populated_rejected(self, agreement, end_date_field, document):
        fact = Fact(
            agreement=agreement, field=end_date_field, primary_document=document,
            valid_from=datetime.date(2024, 1, 1),
            effective_date_basis=Fact.EffectiveDateBasis.STATED_IN_DOCUMENT,
            value_date=datetime.date(2026, 1, 1), value_text="also set",
        )
        with pytest.raises(ValidationError):
            fact.save()

    def test_zero_values_populated_rejected(self, agreement, end_date_field, document):
        fact = Fact(
            agreement=agreement, field=end_date_field, primary_document=document,
            valid_from=datetime.date(2024, 1, 1),
            effective_date_basis=Fact.EffectiveDateBasis.STATED_IN_DOCUMENT,
        )
        with pytest.raises(ValidationError):
            fact.save()

    def test_boolean_false_counts_as_populated_not_absent(self, agreement, document):
        bool_field = FactField.objects.create(
            code="bool_field_test", category=FactField.Category.DESCRIPTIVE,
            value_type=FactField.ValueType.BOOL,
        )
        # value_bool=False must not be treated the same as "not set" --
        # this is exactly why the check is `is not None`, not truthiness.
        fact = make_fact(agreement, bool_field, document, value_bool=False)
        assert fact.value_bool is False

    def test_exactly_one_value_enforced_at_database_level_independent_of_validation(
        self, agreement, end_date_field, document,
    ):
        # Bypasses full_clean() via bulk_create() to prove the CHECK
        # constraint itself rejects a zero-values-set row, not merely the
        # application-level check duplicating it.
        with pytest.raises(IntegrityError):
            with transaction.atomic():
                Fact._base_manager.bulk_create([Fact(
                    agreement=agreement, field=end_date_field, primary_document=document,
                    valid_from=datetime.date(2024, 1, 1),
                    effective_date_basis=Fact.EffectiveDateBasis.STATED_IN_DOCUMENT,
                )])


class TestQualifierBelongsToField:
    def test_qualifier_from_a_different_field_rejected(self, agreement, end_date_field, notice_days_field, document):
        foreign_qualifier = FactFieldQualifier.objects.create(field=notice_days_field, qualifier_key="option_a")
        with pytest.raises(ValidationError):
            make_fact(
                agreement, end_date_field, document, value_date=datetime.date(2026, 1, 1),
                qualifier=foreign_qualifier,
            )

    def test_matching_qualifier_accepted(self, agreement, notice_days_field, document):
        qualifier = FactFieldQualifier.objects.create(field=notice_days_field, qualifier_key="option_a")
        fact = make_fact(
            agreement, notice_days_field, document, value_number=90,
            period_unit=Fact.PeriodUnit.CALENDAR_DAYS, qualifier=qualifier,
            status=Fact.Status.PROPOSED_OPTION,
        )
        assert fact.qualifier == qualifier


class TestBareDuplicatePreventionAcrossNullQualifierAndScope:
    def test_exact_bare_duplicate_rejected_at_database_level(self, agreement, document):
        # Uses a field with allows_multiple_concurrent=True and no
        # qualifier/scope_period set on either fact, specifically to
        # isolate the database constraint from
        # _check_no_illegitimate_concurrency (which would also catch this
        # for allows_multiple_concurrent=False fields, but for a
        # different reason). Standard SQL treats NULL as never equal to
        # NULL in a UniqueConstraint, so this exercises the dedicated
        # uniq_active_fact_bare constraint added specifically to close
        # that gap -- without it, this would NOT raise.
        field = FactField.objects.create(
            code="bare_duplicate_test", category=FactField.Category.DESCRIPTIVE,
            value_type=FactField.ValueType.NUMBER, allows_multiple_concurrent=True,
        )
        Fact._base_manager.bulk_create([Fact(
            agreement=agreement, field=field, primary_document=document, value_number=5,
            valid_from=datetime.date(2024, 1, 1),
            effective_date_basis=Fact.EffectiveDateBasis.STATED_IN_DOCUMENT,
        )])
        with pytest.raises(IntegrityError):
            with transaction.atomic():
                Fact._base_manager.bulk_create([Fact(
                    agreement=agreement, field=field, primary_document=document, value_number=7,
                    valid_from=datetime.date(2024, 1, 1),
                    effective_date_basis=Fact.EffectiveDateBasis.STATED_IN_DOCUMENT,
                )])


class TestTemporalSemantics:
    def test_valid_until_must_be_after_valid_from(self, agreement, end_date_field, document):
        with pytest.raises(ValidationError):
            Fact(
                agreement=agreement, field=end_date_field, primary_document=document,
                value_date=datetime.date(2026, 1, 1),
                valid_from=datetime.date(2024, 6, 1), valid_until=datetime.date(2024, 1, 1),
                effective_date_basis=Fact.EffectiveDateBasis.STATED_IN_DOCUMENT,
            ).save()

    def test_valid_until_ordering_enforced_at_database_level(self, agreement, end_date_field, document):
        with pytest.raises(IntegrityError):
            with transaction.atomic():
                Fact._base_manager.bulk_create([Fact(
                    agreement=agreement, field=end_date_field, primary_document=document,
                    value_date=datetime.date(2026, 1, 1),
                    valid_from=datetime.date(2024, 6, 1), valid_until=datetime.date(2024, 1, 1),
                    effective_date_basis=Fact.EffectiveDateBasis.STATED_IN_DOCUMENT,
                )])

    def test_open_ended_valid_until_permitted(self, agreement, end_date_field, document):
        fact = make_fact(agreement, end_date_field, document, value_date=datetime.date(2026, 1, 1))
        assert fact.valid_until is None

    def test_scope_period_end_before_start_rejected(self, agreement, document):
        annual_value_field = FactField.objects.create(
            code="annual_value_test", category=FactField.Category.DESCRIPTIVE,
            value_type=FactField.ValueType.NUMBER, allows_multiple_concurrent=True,
        )
        with pytest.raises(ValidationError):
            make_fact(
                agreement, annual_value_field, document, value_number=1000,
                scope_period_start=datetime.date(2025, 1, 1), scope_period_end=datetime.date(2024, 1, 1),
            )

    def test_operative_at_resolves_the_fact_whose_interval_covers_the_date(
        self, agreement, end_date_field, document,
    ):
        original = make_fact(
            agreement, end_date_field, document, value_date=datetime.date(2026, 6, 29),
            valid_from=datetime.date(2024, 1, 1),
        )
        amendment = make_fact(
            agreement, end_date_field, document, value_date=datetime.date(2026, 12, 29),
            valid_from=datetime.date(2026, 7, 15), supersedes=original,
        )
        original.refresh_from_db()
        assert original.valid_until == datetime.date(2026, 7, 15)

        as_of_before = Fact.objects.operative_at(agreement, end_date_field, datetime.date(2026, 7, 1))
        as_of_after = Fact.objects.operative_at(agreement, end_date_field, datetime.date(2026, 8, 1))
        assert as_of_before == original
        assert as_of_after == amendment

    def test_superseding_a_fact_that_already_has_a_conflicting_valid_until_is_rejected(
        self, agreement, end_date_field, document,
    ):
        original = make_fact(
            agreement, end_date_field, document, value_date=datetime.date(2026, 6, 29),
            valid_from=datetime.date(2024, 1, 1), valid_until=datetime.date(2025, 1, 1),
        )
        with pytest.raises(ValidationError):
            make_fact(
                agreement, end_date_field, document, value_date=datetime.date(2026, 12, 29),
                valid_from=datetime.date(2026, 7, 15), supersedes=original,
            )


class TestLegitimateConcurrency:
    def test_field_without_multiple_concurrent_rejects_overlapping_active_facts(
        self, agreement, end_date_field, document,
    ):
        make_fact(
            agreement, end_date_field, document, value_date=datetime.date(2026, 1, 1),
            valid_from=datetime.date(2024, 1, 1),
        )
        with pytest.raises(ValidationError):
            make_fact(
                agreement, end_date_field, document, value_date=datetime.date(2027, 1, 1),
                valid_from=datetime.date(2024, 6, 1),
            )

    def test_field_without_multiple_concurrent_permits_non_overlapping_supersession(
        self, agreement, end_date_field, document,
    ):
        original = make_fact(
            agreement, end_date_field, document, value_date=datetime.date(2026, 6, 29),
            valid_from=datetime.date(2024, 1, 1),
        )
        # Non-overlapping once supersession closes the old interval.
        amendment = make_fact(
            agreement, end_date_field, document, value_date=datetime.date(2026, 12, 29),
            valid_from=datetime.date(2026, 7, 15), supersedes=original,
        )
        assert amendment.pk is not None

    def test_field_with_multiple_concurrent_permits_overlapping_facts_by_qualifier(
        self, agreement, document,
    ):
        annual_value_field = FactField.objects.create(
            code="annual_value_concurrency_test", category=FactField.Category.DESCRIPTIVE,
            value_type=FactField.ValueType.NUMBER, allows_multiple_concurrent=True,
        )
        year1 = FactFieldQualifier.objects.create(field=annual_value_field, qualifier_key="year_1")
        year2 = FactFieldQualifier.objects.create(field=annual_value_field, qualifier_key="year_2")
        make_fact(
            agreement, annual_value_field, document, value_number=100000, qualifier=year1,
            valid_from=datetime.date(2024, 1, 1),
        )
        # Same overlapping window, different qualifier -- legitimate.
        fact2 = make_fact(
            agreement, annual_value_field, document, value_number=105000, qualifier=year2,
            valid_from=datetime.date(2024, 1, 1),
        )
        assert fact2.pk is not None


class TestRetraction:
    def test_retraction_requires_a_reason(self, agreement, end_date_field, document):
        fact = make_fact(agreement, end_date_field, document, value_date=datetime.date(2026, 1, 1))
        fact.status = Fact.Status.RETRACTED
        with pytest.raises(ValidationError):
            fact.save()

    def test_retraction_with_reason_and_attribution(self, agreement, end_date_field, document, reviewer):
        fact = make_fact(agreement, end_date_field, document, value_date=datetime.date(2026, 1, 1))
        fact.status = Fact.Status.RETRACTED
        fact.retraction_reason = "Entered against the wrong agreement by mistake."
        fact.retracted_by = reviewer
        fact.retracted_at = datetime.datetime(2026, 2, 1, tzinfo=datetime.timezone.utc)
        fact.save()
        fact.refresh_from_db()
        assert fact.status == Fact.Status.RETRACTED
        assert fact.retracted_by == reviewer


class TestEffectiveDateBasis:
    def test_retroactive_valid_from_requires_stated_in_document_basis(self, agreement, end_date_field, document):
        # document.execution_date is 2025-06-05; valid_from before that
        # without explicitly stating the document says so is rejected.
        with pytest.raises(ValidationError):
            make_fact(
                agreement, end_date_field, document, value_date=datetime.date(2026, 1, 1),
                valid_from=datetime.date(2025, 1, 1),
                effective_date_basis=Fact.EffectiveDateBasis.EXECUTION_DATE,
            )

    def test_retroactive_valid_from_permitted_when_stated_in_document(self, agreement, end_date_field, document):
        fact = make_fact(
            agreement, end_date_field, document, value_date=datetime.date(2026, 1, 1),
            valid_from=datetime.date(2025, 1, 1),
            effective_date_basis=Fact.EffectiveDateBasis.STATED_IN_DOCUMENT,
        )
        assert fact.valid_from == datetime.date(2025, 1, 1)

    def test_valid_from_after_document_dates_needs_no_special_basis(self, agreement, end_date_field, document):
        fact = make_fact(
            agreement, end_date_field, document, value_date=datetime.date(2026, 1, 1),
            valid_from=datetime.date(2025, 7, 1),
            effective_date_basis=Fact.EffectiveDateBasis.EXECUTION_DATE,
        )
        assert fact.effective_date_basis == Fact.EffectiveDateBasis.EXECUTION_DATE


class TestFinancialFacts:
    def test_currency_and_pricing_basis_are_explicit_not_inferred(self, agreement, document):
        value_field = FactField.objects.create(
            code="total_contract_value_test", category=FactField.Category.DESCRIPTIVE,
            value_type=FactField.ValueType.NUMBER,
        )
        fact = make_fact(
            agreement, value_field, document, value_number=150000,
            currency="USD", pricing_basis=Fact.PricingBasis.TOTAL_CONTRACT_VALUE,
        )
        assert fact.currency == "USD"
        assert fact.pricing_basis == Fact.PricingBasis.TOTAL_CONTRACT_VALUE


class TestFactDeletion:
    def test_fact_delete_is_not_allowed(self, agreement, end_date_field, document):
        fact = make_fact(agreement, end_date_field, document, value_date=datetime.date(2026, 1, 1))
        with pytest.raises(RecordDeletionNotAllowed):
            fact.delete()
        assert Fact.objects.filter(pk=fact.pk).exists()

    def test_fact_queryset_delete_is_not_allowed(self, agreement, end_date_field, document):
        make_fact(agreement, end_date_field, document, value_date=datetime.date(2026, 1, 1))
        with pytest.raises(RecordDeletionNotAllowed):
            Fact.objects.all().delete()
        assert Fact.objects.count() == 1


class TestDocumentImmutabilityOnceCited:
    def test_locked_fields_cannot_change_once_a_fact_cites_the_document(
        self, agreement, end_date_field, document,
    ):
        make_fact(agreement, end_date_field, document, value_date=datetime.date(2026, 1, 1))
        document.content_sha256 = "c" * 64
        with pytest.raises(ValidationError):
            document.save()

    def test_non_locked_fields_remain_editable_once_cited(self, agreement, end_date_field, document):
        make_fact(agreement, end_date_field, document, value_date=datetime.date(2026, 1, 1))
        document.wayback_status = document.WaybackStatus.SUCCEEDED
        document.save()
        document.refresh_from_db()
        assert document.wayback_status == document.WaybackStatus.SUCCEEDED

    def test_locked_fields_remain_editable_before_any_fact_cites_the_document(self, document):
        document.content_sha256 = "c" * 64
        document.save()
        document.refresh_from_db()
        assert document.content_sha256 == "c" * 64
