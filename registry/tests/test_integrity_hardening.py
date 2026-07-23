"""
Cited-document-field and fact-identity immutability, across every write
path: ordinary save(), QuerySet.update(), bulk_update(), and (on
Postgres only) a direct raw-SQL write that bypasses Django entirely.

SQLite has no trigger/procedural-language concept in the same sense
Postgres does, so its protection is purely the application/QuerySet
guards in registry/models.py -- those are proven on both backends here;
the Postgres-only tests additionally prove the database itself refuses
the write even when Django's guards are bypassed via a raw connection.
"""

import datetime

import pytest
from django.core.exceptions import ValidationError
from django.db import DatabaseError, connection, transaction

from registry.models import Document, Fact, FactCorroboration, Reviewer
from registry.services import fact_lifecycle
from registry.services.integrity import LockedFieldMutationNotAllowed
from registry.tests._db_helpers import is_postgres

pytestmark = pytest.mark.django_db


def make_fact(agreement, field, document, created_by, **overrides):
    defaults = dict(
        agreement=agreement, field=field, primary_document=document, created_by=created_by,
        valid_from=datetime.date(2024, 1, 1),
        effective_date_basis=Fact.EffectiveDateBasis.STATED_IN_DOCUMENT,
        value_date=datetime.date(2026, 1, 1),
    )
    defaults.update(overrides)
    return Fact.objects.create(**defaults)


class TestCitedDocumentFieldLock:
    def test_update_blocked_when_cited(self, agreement, end_date_field, document, reviewer):
        make_fact(agreement, end_date_field, document, reviewer)
        with pytest.raises(LockedFieldMutationNotAllowed):
            Document.objects.filter(pk=document.pk).update(content_sha256="c" * 64)
        document.refresh_from_db()
        assert document.content_sha256 != "c" * 64

    def test_update_permitted_when_not_cited(self, document):
        Document.objects.filter(pk=document.pk).update(content_sha256="c" * 64)
        document.refresh_from_db()
        assert document.content_sha256 == "c" * 64

    def test_update_of_non_locked_field_permitted_even_when_cited(self, agreement, end_date_field, document, reviewer):
        make_fact(agreement, end_date_field, document, reviewer)
        Document.objects.filter(pk=document.pk).update(wayback_status=Document.WaybackStatus.SUCCEEDED)
        document.refresh_from_db()
        assert document.wayback_status == Document.WaybackStatus.SUCCEEDED

    def test_bulk_update_blocked_when_cited(self, agreement, end_date_field, document, reviewer):
        make_fact(agreement, end_date_field, document, reviewer)
        document.content_sha256 = "c" * 64
        with pytest.raises(LockedFieldMutationNotAllowed):
            Document.objects.bulk_update([document], ["content_sha256"])

    def test_bulk_update_permitted_when_not_cited(self, document):
        document.content_sha256 = "c" * 64
        Document.objects.bulk_update([document], ["content_sha256"])
        document.refresh_from_db()
        assert document.content_sha256 == "c" * 64

    def test_citation_via_corroboration_also_blocks_update(self, agreement, end_date_field, document, reviewer):
        fact = make_fact(agreement, end_date_field, document, reviewer)
        minutes = Document.objects.create(
            document_type=Document.DocumentType.MINUTES,
            archived_storage_key="aa/hardening-minutes.pdf", content_sha256="d" * 64,
            file_size_bytes=512, mime_type="application/pdf",
            date_obtained=datetime.date(2026, 1, 1),
            acquisition_method=Document.AcquisitionMethod.DIRECT_SUBMISSION,
        )
        FactCorroboration.objects.create(fact=fact, document=minutes)
        with pytest.raises(LockedFieldMutationNotAllowed):
            Document.objects.filter(pk=minutes.pk).update(mime_type="image/png")

    @pytest.mark.skipif(not is_postgres(), reason="No SQLite trigger/procedural-language equivalent")
    def test_postgres_trigger_blocks_raw_sql_update_bypassing_django(
        self, agreement, end_date_field, document, reviewer,
    ):
        make_fact(agreement, end_date_field, document, reviewer)
        with pytest.raises(DatabaseError):
            with transaction.atomic():
                with connection.cursor() as cursor:
                    cursor.execute(
                        "UPDATE registry_document SET content_sha256 = %s WHERE id = %s",
                        ["f" * 64, document.pk],
                    )
        document.refresh_from_db()
        assert document.content_sha256 != "f" * 64

    @pytest.mark.skipif(not is_postgres(), reason="No SQLite trigger/procedural-language equivalent")
    def test_postgres_trigger_permits_raw_sql_update_when_not_cited(self, document):
        with connection.cursor() as cursor:
            cursor.execute(
                "UPDATE registry_document SET content_sha256 = %s WHERE id = %s",
                ["f" * 64, document.pk],
            )
        document.refresh_from_db()
        assert document.content_sha256 == "f" * 64


class TestFactIdentityLock:
    def test_save_blocked_from_changing_agreement(self, agreement, other_agreement, end_date_field, document, reviewer):
        fact = make_fact(agreement, end_date_field, document, reviewer)
        fact.agreement = other_agreement
        with pytest.raises(ValidationError):
            fact.save()

    def test_save_blocked_from_changing_typed_value(self, agreement, end_date_field, document, reviewer):
        fact = make_fact(agreement, end_date_field, document, reviewer)
        fact.value_date = datetime.date(2030, 1, 1)
        with pytest.raises(ValidationError):
            fact.save()

    def test_save_blocked_from_changing_primary_document(self, agreement, end_date_field, document, reviewer):
        other_doc = Document.objects.create(
            document_type=Document.DocumentType.AMENDMENT,
            archived_storage_key="aa/other-doc.pdf", content_sha256="9" * 64,
            file_size_bytes=256, mime_type="application/pdf",
            date_obtained=datetime.date(2026, 1, 1),
            acquisition_method=Document.AcquisitionMethod.DIRECT_SUBMISSION,
        )
        fact = make_fact(agreement, end_date_field, document, reviewer)
        fact.primary_document = other_doc
        with pytest.raises(ValidationError):
            fact.save()

    def test_save_blocked_from_changing_created_by(self, agreement, end_date_field, document, reviewer, other_reviewer):
        fact = make_fact(agreement, end_date_field, document, reviewer)
        fact.created_by = other_reviewer
        with pytest.raises(ValidationError):
            fact.save()

    def test_save_permits_changing_valid_until_and_status(self, agreement, end_date_field, document, reviewer):
        fact = make_fact(agreement, end_date_field, document, reviewer)
        fact.valid_until = datetime.date(2027, 1, 1)
        fact.save()
        fact.refresh_from_db()
        assert fact.valid_until == datetime.date(2027, 1, 1)

    def test_queryset_update_blocked_for_identity_field(self, agreement, end_date_field, document, reviewer):
        fact = make_fact(agreement, end_date_field, document, reviewer)
        with pytest.raises(LockedFieldMutationNotAllowed):
            Fact.objects.filter(pk=fact.pk).update(excerpt="rewritten")
        fact.refresh_from_db()
        assert fact.excerpt != "rewritten"

    def test_queryset_update_blocked_using_id_suffixed_kwarg(self, agreement, other_agreement, end_date_field, document, reviewer):
        fact = make_fact(agreement, end_date_field, document, reviewer)
        with pytest.raises(LockedFieldMutationNotAllowed):
            Fact.objects.filter(pk=fact.pk).update(agreement_id=other_agreement.pk)

    def test_queryset_update_permitted_for_lifecycle_field(self, agreement, end_date_field, document, reviewer):
        fact = make_fact(agreement, end_date_field, document, reviewer)
        Fact.objects.filter(pk=fact.pk).update(valid_until=datetime.date(2027, 6, 1))
        fact.refresh_from_db()
        assert fact.valid_until == datetime.date(2027, 6, 1)

    def test_bulk_update_blocked_for_identity_field(self, agreement, end_date_field, document, reviewer):
        fact = make_fact(agreement, end_date_field, document, reviewer)
        fact.excerpt = "rewritten"
        with pytest.raises(LockedFieldMutationNotAllowed):
            Fact.objects.bulk_update([fact], ["excerpt"])

    def test_bulk_update_permitted_for_lifecycle_field(self, agreement, end_date_field, document, reviewer):
        fact = make_fact(agreement, end_date_field, document, reviewer)
        fact.status = Fact.Status.RETRACTED
        fact.retraction_reason = "test"
        Fact.objects.bulk_update([fact], ["status", "retraction_reason"])
        fact.refresh_from_db()
        assert fact.status == Fact.Status.RETRACTED

    @pytest.mark.skipif(not is_postgres(), reason="No SQLite trigger/procedural-language equivalent")
    def test_postgres_trigger_blocks_raw_sql_identity_update(self, agreement, other_agreement, end_date_field, document, reviewer):
        fact = make_fact(agreement, end_date_field, document, reviewer)
        with pytest.raises(DatabaseError):
            with transaction.atomic():
                with connection.cursor() as cursor:
                    cursor.execute(
                        "UPDATE registry_fact SET agreement_id = %s WHERE id = %s",
                        [other_agreement.pk, fact.pk],
                    )
        fact.refresh_from_db()
        assert fact.agreement_id == agreement.pk

    @pytest.mark.skipif(not is_postgres(), reason="No SQLite trigger/procedural-language equivalent")
    def test_postgres_trigger_permits_raw_sql_lifecycle_update(self, agreement, end_date_field, document, reviewer):
        fact = make_fact(agreement, end_date_field, document, reviewer)
        with connection.cursor() as cursor:
            cursor.execute(
                "UPDATE registry_fact SET valid_until = %s WHERE id = %s",
                [datetime.date(2028, 1, 1), fact.pk],
            )
        fact.refresh_from_db()
        assert fact.valid_until == datetime.date(2028, 1, 1)


class TestFactLifecycleServiceFunctions:
    def test_retract_fact(self, agreement, end_date_field, document, reviewer, other_reviewer):
        fact = make_fact(agreement, end_date_field, document, reviewer)
        fact_lifecycle.retract_fact(fact, reason="Entered against the wrong agreement.", retracted_by=other_reviewer)
        fact.refresh_from_db()
        assert fact.status == Fact.Status.RETRACTED
        assert fact.retracted_by == other_reviewer
        assert fact.retracted_at is not None

    def test_supersede_fact(self, agreement, end_date_field, document, reviewer):
        original = make_fact(agreement, end_date_field, document, reviewer, value_date=datetime.date(2026, 6, 29))
        amendment = fact_lifecycle.supersede_fact(
            original, primary_document=document, created_by=reviewer,
            value_date=datetime.date(2026, 12, 29), valid_from=datetime.date(2026, 7, 15),
            effective_date_basis=Fact.EffectiveDateBasis.STATED_IN_DOCUMENT,
        )
        original.refresh_from_db()
        assert original.valid_until == datetime.date(2026, 7, 15)
        assert original.status == Fact.Status.ACTIVE
        assert amendment.supersedes == original
