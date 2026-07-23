import datetime

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction

from registry.models import Document, Fact, FactCorroboration

pytestmark = pytest.mark.django_db


def make_fact(agreement, field, document, **overrides):
    defaults = dict(
        agreement=agreement, field=field, primary_document=document,
        valid_from=datetime.date(2024, 1, 1),
        effective_date_basis=Fact.EffectiveDateBasis.STATED_IN_DOCUMENT,
        value_date=datetime.date(2026, 1, 1),
    )
    defaults.update(overrides)
    return Fact.objects.create(**defaults)


def make_document(**overrides):
    defaults = dict(
        document_type=Document.DocumentType.MINUTES,
        archived_storage_key=f"aa/{overrides.get('_key', 'corroborating')}.pdf",
        content_sha256="d" * 64,
        file_size_bytes=512,
        mime_type="application/pdf",
        date_obtained=datetime.date(2026, 1, 1),
        acquisition_method=Document.AcquisitionMethod.DIRECT_SUBMISSION,
    )
    overrides.pop("_key", None)
    defaults.update(overrides)
    return Document.objects.create(**defaults)


def test_corroboration_links_an_additional_document(agreement, end_date_field, document):
    fact = make_fact(agreement, end_date_field, document)
    minutes = make_document(_key="minutes-1")
    corroboration = FactCorroboration.objects.create(fact=fact, document=minutes, note="Independently confirms the date.")
    assert corroboration in fact.corroborations.all()
    # `corroborated_facts` is a reverse FK manager -- it yields
    # FactCorroboration link rows (from Document's side), not Fact
    # instances directly.
    assert corroboration in minutes.corroborated_facts.all()


def test_primary_document_cannot_also_be_listed_as_corroboration(agreement, end_date_field, document):
    fact = make_fact(agreement, end_date_field, document)
    with pytest.raises(ValidationError):
        FactCorroboration.objects.create(fact=fact, document=document)


def test_duplicate_corroboration_rejected(agreement, end_date_field, document):
    fact = make_fact(agreement, end_date_field, document)
    minutes = make_document(_key="minutes-2")
    FactCorroboration.objects.create(fact=fact, document=minutes)
    with pytest.raises(ValidationError):
        FactCorroboration.objects.create(fact=fact, document=minutes)


def test_duplicate_corroboration_rejected_by_database_independent_of_validation(agreement, end_date_field, document):
    fact = make_fact(agreement, end_date_field, document)
    minutes = make_document(_key="minutes-3")
    FactCorroboration._base_manager.bulk_create([FactCorroboration(fact=fact, document=minutes)])
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            FactCorroboration._base_manager.bulk_create([FactCorroboration(fact=fact, document=minutes)])


def test_citing_a_document_as_corroboration_also_locks_its_core_fields(agreement, end_date_field, document):
    fact = make_fact(agreement, end_date_field, document)
    minutes = make_document(_key="minutes-4")
    FactCorroboration.objects.create(fact=fact, document=minutes)
    minutes.content_sha256 = "e" * 64
    with pytest.raises(ValidationError):
        minutes.save()
