import datetime

import pytest

from registry.models import Agreement, Document, FactField, Jurisdiction, Reviewer, Vendor


@pytest.fixture
def jurisdiction():
    return Jurisdiction.objects.create(name="Cleveland", type=Jurisdiction.JurisdictionType.CITY, state="OH")


@pytest.fixture
def vendor():
    return Vendor.objects.create(canonical_name="Flock Safety")


@pytest.fixture
def agreement(jurisdiction, vendor):
    return Agreement.objects.create(
        jurisdiction=jurisdiction, vendor=vendor,
        agreement_type=Agreement.AgreementType.STANDALONE_AGREEMENT,
    )


@pytest.fixture
def other_agreement(jurisdiction, vendor):
    return Agreement.objects.create(
        jurisdiction=jurisdiction, vendor=vendor,
        agreement_type=Agreement.AgreementType.STANDALONE_AGREEMENT, scope_note="a second, unrelated agreement",
    )


@pytest.fixture
def document():
    return Document.objects.create(
        document_type=Document.DocumentType.ORIGINAL_AGREEMENT,
        archived_storage_key="aa/conftest-doc.pdf",
        content_sha256="b" * 64,
        file_size_bytes=2048,
        mime_type="application/pdf",
        date_obtained=datetime.date(2026, 1, 1),
        acquisition_method=Document.AcquisitionMethod.DIRECT_SUBMISSION,
        vote_or_approval_date=datetime.date(2025, 6, 1),
        execution_date=datetime.date(2025, 6, 5),
    )


@pytest.fixture
def reviewer():
    return Reviewer.objects.create(
        display_name="A. Reviewer", contact_email="reviewer-a@example.org",
        role=Reviewer.Role.TRUSTED_REVIEWER,
    )


@pytest.fixture
def other_reviewer():
    return Reviewer.objects.create(
        display_name="B. Reviewer", contact_email="reviewer-b@example.org",
        role=Reviewer.Role.TRUSTED_REVIEWER,
    )


@pytest.fixture
def end_date_field():
    return FactField.objects.get(machine_key="end_date")


@pytest.fixture
def notice_days_field():
    return FactField.objects.get(machine_key="non_renewal_notice_days")


@pytest.fixture
def descriptive_field():
    return FactField.objects.create(
        code="camera_count_conftest", category=FactField.Category.DESCRIPTIVE,
        value_type=FactField.ValueType.NUMBER, allows_multiple_concurrent=False,
    )
