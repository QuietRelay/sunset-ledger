import datetime

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction

from registry.models import (
    Agreement,
    Document,
    DocumentAgreement,
    DocumentRelationship,
    Jurisdiction,
    Vendor,
)
from registry.services.integrity import RecordDeletionNotAllowed

pytestmark = pytest.mark.django_db

VALID_SHA = "a" * 64


def make_document(**overrides):
    defaults = dict(
        document_type=Document.DocumentType.ORIGINAL_AGREEMENT,
        archived_storage_key=f"aa/{overrides.get('_key_suffix', 'default')}.pdf",
        content_sha256=VALID_SHA,
        file_size_bytes=1024,
        mime_type="application/pdf",
        date_obtained=datetime.date(2026, 1, 1),
        acquisition_method=Document.AcquisitionMethod.DIRECT_SUBMISSION,
    )
    overrides.pop("_key_suffix", None)
    defaults.update(overrides)
    return Document.objects.create(**defaults)


def test_document_create_with_required_fields():
    doc = make_document(_key_suffix="1")
    assert doc.wayback_status == Document.WaybackStatus.PENDING
    assert doc.unredacted_copy_status == Document.UnredactedCopyStatus.NOT_APPLICABLE


def test_document_sha256_must_be_valid_hex():
    with pytest.raises(ValidationError):
        make_document(_key_suffix="2", content_sha256="not-a-valid-hash")


def test_identical_document_hashes_are_permitted():
    # Duplicates are resolved editorially via DocumentRelationship(duplicate_of),
    # never rejected at insert -- this is a deliberate design choice, not
    # an oversight.
    a = make_document(_key_suffix="dup-a")
    b = make_document(_key_suffix="dup-b")
    assert a.content_sha256 == b.content_sha256 == VALID_SHA
    assert Document.objects.filter(content_sha256=VALID_SHA).count() == 2


def test_archived_storage_key_must_be_unique():
    # save() calls full_clean(), so Django's own validate_unique() catches
    # this before any SQL is sent -- ValidationError on the ordinary
    # create() path.
    make_document(_key_suffix="unique-key", archived_storage_key="same/key.pdf")
    with pytest.raises(ValidationError):
        make_document(_key_suffix="unique-key-2", archived_storage_key="same/key.pdf")


def test_archived_storage_key_uniqueness_enforced_by_database_independent_of_validation():
    # Bypasses full_clean() (bulk_create() never calls it) to prove the
    # unique=True constraint itself rejects this, not merely the
    # application-level check duplicating it.
    make_document(_key_suffix="unique-key-db", archived_storage_key="same/key-db.pdf")
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            Document._base_manager.bulk_create([Document(
                document_type=Document.DocumentType.ORIGINAL_AGREEMENT,
                archived_storage_key="same/key-db.pdf",
                content_sha256=VALID_SHA,
                file_size_bytes=1024,
                mime_type="application/pdf",
                date_obtained=datetime.date(2026, 1, 1),
                acquisition_method=Document.AcquisitionMethod.DIRECT_SUBMISSION,
            )])


def test_redaction_note_required_when_project_performed_redaction():
    with pytest.raises(ValidationError):
        make_document(
            _key_suffix="redaction-missing-note",
            redaction_performed_by=Document.RedactionPerformedBy.PROJECT,
        )
    # With a note, it's fine.
    doc = make_document(
        _key_suffix="redaction-with-note",
        redaction_performed_by=Document.RedactionPerformedBy.PROJECT,
        redaction_note="Removed a signatory's personal cell number.",
    )
    assert doc.redaction_note


def test_retention_reason_required_when_unredacted_copy_retained():
    with pytest.raises(ValidationError):
        make_document(
            _key_suffix="retention-missing-reason",
            unredacted_copy_status=Document.UnredactedCopyStatus.RETAINED_RESTRICTED,
        )
    doc = make_document(
        _key_suffix="retention-with-reason",
        unredacted_copy_status=Document.UnredactedCopyStatus.RETAINED_RESTRICTED,
        unredacted_retention_reason="Reviewer may need to re-check redaction context.",
    )
    assert doc.unredacted_retention_reason


def test_archival_retry_fields_default_state():
    doc = make_document(_key_suffix="archival-defaults")
    assert doc.wayback_attempts == 0
    assert doc.wayback_last_attempt_at is None
    assert doc.wayback_saved_at is None
    assert doc.next_archive_attempt_at is None


def test_archival_retry_state_can_be_updated_after_a_failed_attempt():
    doc = make_document(_key_suffix="archival-retry")
    doc.wayback_status = Document.WaybackStatus.FAILED
    doc.wayback_attempts = 1
    doc.wayback_last_attempt_at = datetime.datetime(2026, 1, 2, tzinfo=datetime.timezone.utc)
    doc.wayback_last_error = "Save Page Now timed out."
    doc.next_archive_attempt_at = datetime.datetime(2026, 1, 3, tzinfo=datetime.timezone.utc)
    doc.save()
    doc.refresh_from_db()
    assert doc.wayback_status == Document.WaybackStatus.FAILED
    assert doc.wayback_attempts == 1
    assert doc.wayback_last_error == "Save Page Now timed out."


def test_document_delete_is_not_allowed():
    doc = make_document(_key_suffix="no-delete")
    with pytest.raises(RecordDeletionNotAllowed):
        doc.delete()
    assert Document.objects.filter(pk=doc.pk).exists()


def test_document_queryset_delete_is_not_allowed():
    make_document(_key_suffix="no-bulk-delete")
    with pytest.raises(RecordDeletionNotAllowed):
        Document.objects.all().delete()
    assert Document.objects.count() == 1


class TestDocumentRelationship:
    def test_create_relationship(self):
        original = make_document(_key_suffix="original")
        redacted = make_document(_key_suffix="redacted")
        rel = DocumentRelationship.objects.create(
            from_document=redacted, to_document=original,
            relationship_type=DocumentRelationship.RelationshipType.REDACTED_VERSION_OF,
        )
        assert rel in redacted.outgoing_relationships.all()
        assert rel in original.incoming_relationships.all()

    def test_self_link_rejected_by_application_validation(self):
        doc = make_document(_key_suffix="self-link")
        with pytest.raises(ValidationError):
            DocumentRelationship.objects.create(
                from_document=doc, to_document=doc,
                relationship_type=DocumentRelationship.RelationshipType.DUPLICATE_OF,
            )

    def test_self_link_rejected_by_database_constraint(self):
        doc = make_document(_key_suffix="self-link-db")
        with pytest.raises(IntegrityError):
            with transaction.atomic():
                DocumentRelationship._base_manager.bulk_create([
                    DocumentRelationship(
                        from_document=doc, to_document=doc,
                        relationship_type=DocumentRelationship.RelationshipType.DUPLICATE_OF,
                    )
                ])

    def test_exact_duplicate_relationship_rejected(self):
        # save() calls full_clean(), so Django's own validate_unique()
        # catches this before any SQL is sent -- ValidationError on the
        # ordinary create() path.
        a = make_document(_key_suffix="rel-a")
        b = make_document(_key_suffix="rel-b")
        DocumentRelationship.objects.create(
            from_document=a, to_document=b,
            relationship_type=DocumentRelationship.RelationshipType.REPLACEMENT_FOR,
        )
        with pytest.raises(ValidationError):
            DocumentRelationship.objects.create(
                from_document=a, to_document=b,
                relationship_type=DocumentRelationship.RelationshipType.REPLACEMENT_FOR,
            )

    def test_exact_duplicate_relationship_rejected_by_database_independent_of_validation(self):
        # Bypasses full_clean() (bulk_create() never calls it) to prove
        # the UniqueConstraint itself rejects this, not merely the
        # application-level check duplicating it.
        a = make_document(_key_suffix="rel-a-db")
        b = make_document(_key_suffix="rel-b-db")
        DocumentRelationship._base_manager.bulk_create([
            DocumentRelationship(
                from_document=a, to_document=b,
                relationship_type=DocumentRelationship.RelationshipType.REPLACEMENT_FOR,
            )
        ])
        with pytest.raises(IntegrityError):
            with transaction.atomic():
                DocumentRelationship._base_manager.bulk_create([
                    DocumentRelationship(
                        from_document=a, to_document=b,
                        relationship_type=DocumentRelationship.RelationshipType.REPLACEMENT_FOR,
                    )
                ])

    def test_different_relationship_type_same_pair_allowed(self):
        a = make_document(_key_suffix="rel-c")
        b = make_document(_key_suffix="rel-d")
        DocumentRelationship.objects.create(
            from_document=a, to_document=b,
            relationship_type=DocumentRelationship.RelationshipType.DUPLICATE_OF,
        )
        DocumentRelationship.objects.create(
            from_document=a, to_document=b,
            relationship_type=DocumentRelationship.RelationshipType.ATTACHMENT_TO,
        )
        assert DocumentRelationship.objects.count() == 2


class TestDocumentAgreement:
    @pytest.fixture
    def jurisdiction(self):
        return Jurisdiction.objects.create(
            name="Columbus", type=Jurisdiction.JurisdictionType.CITY, state="OH",
        )

    @pytest.fixture
    def vendor(self):
        return Vendor.objects.create(canonical_name="Flock Safety")

    def test_one_document_can_support_multiple_agreements(self, jurisdiction, vendor):
        master = Agreement.objects.create(
            jurisdiction=jurisdiction, vendor=vendor,
            agreement_type=Agreement.AgreementType.MASTER_AGREEMENT,
        )
        participating = Agreement.objects.create(
            jurisdiction=jurisdiction, vendor=vendor,
            agreement_type=Agreement.AgreementType.PARTICIPATING_AGREEMENT,
            parent_agreement=master,
        )
        doc = make_document(_key_suffix="shared-master-doc")
        DocumentAgreement.objects.create(
            document=doc, agreement=master,
            relationship_role=DocumentAgreement.RelationshipRole.GOVERNING_INSTRUMENT,
        )
        DocumentAgreement.objects.create(
            document=doc, agreement=participating,
            relationship_role=DocumentAgreement.RelationshipRole.GOVERNING_INSTRUMENT,
        )
        assert doc.agreement_links.count() == 2

    def test_duplicate_document_agreement_pair_rejected(self, jurisdiction, vendor):
        # save() calls full_clean(), so Django's own validate_unique()
        # catches this before any SQL is sent -- ValidationError on the
        # ordinary create() path.
        agreement = Agreement.objects.create(
            jurisdiction=jurisdiction, vendor=vendor,
            agreement_type=Agreement.AgreementType.STANDALONE_AGREEMENT,
        )
        doc = make_document(_key_suffix="duplicate-pair")
        DocumentAgreement.objects.create(document=doc, agreement=agreement)
        with pytest.raises(ValidationError):
            DocumentAgreement.objects.create(document=doc, agreement=agreement)

    def test_duplicate_document_agreement_pair_rejected_by_database_independent_of_validation(
        self, jurisdiction, vendor,
    ):
        # Bypasses full_clean() (bulk_create() never calls it) to prove
        # the UniqueConstraint itself rejects this, not merely the
        # application-level check duplicating it.
        agreement = Agreement.objects.create(
            jurisdiction=jurisdiction, vendor=vendor,
            agreement_type=Agreement.AgreementType.STANDALONE_AGREEMENT,
        )
        doc = make_document(_key_suffix="duplicate-pair-db")
        DocumentAgreement._base_manager.bulk_create([
            DocumentAgreement(document=doc, agreement=agreement)
        ])
        with pytest.raises(IntegrityError):
            with transaction.atomic():
                DocumentAgreement._base_manager.bulk_create([
                    DocumentAgreement(document=doc, agreement=agreement)
                ])

    def test_relationship_role_is_optional(self, jurisdiction, vendor):
        agreement = Agreement.objects.create(
            jurisdiction=jurisdiction, vendor=vendor,
            agreement_type=Agreement.AgreementType.STANDALONE_AGREEMENT,
        )
        doc = make_document(_key_suffix="no-role")
        link = DocumentAgreement.objects.create(document=doc, agreement=agreement)
        assert link.relationship_role is None
