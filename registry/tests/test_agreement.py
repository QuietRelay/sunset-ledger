import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction

from registry.models import Agreement, AgreementRelationship, Jurisdiction, Vendor
from registry.services.integrity import RecordDeletionNotAllowed

pytestmark = pytest.mark.django_db


@pytest.fixture
def jurisdiction():
    return Jurisdiction.objects.create(
        name="Cleveland", type=Jurisdiction.JurisdictionType.CITY, state="OH",
    )


@pytest.fixture
def vendor():
    return Vendor.objects.create(canonical_name="Flock Safety")


def test_standalone_agreement_create(jurisdiction, vendor):
    agreement = Agreement.objects.create(
        jurisdiction=jurisdiction, vendor=vendor,
        agreement_type=Agreement.AgreementType.STANDALONE_AGREEMENT,
    )
    assert agreement.parent_agreement is None
    assert str(jurisdiction) in str(agreement)


def test_participating_agreement_can_reference_a_master(jurisdiction, vendor):
    master = Agreement.objects.create(
        jurisdiction=jurisdiction, vendor=vendor,
        agreement_type=Agreement.AgreementType.MASTER_AGREEMENT,
    )
    participating = Agreement.objects.create(
        jurisdiction=jurisdiction, vendor=vendor,
        agreement_type=Agreement.AgreementType.PARTICIPATING_AGREEMENT,
        parent_agreement=master,
    )
    assert participating in master.participating_agreements.all()


def test_master_agreement_cannot_have_a_parent(jurisdiction, vendor):
    other_master = Agreement.objects.create(
        jurisdiction=jurisdiction, vendor=vendor,
        agreement_type=Agreement.AgreementType.MASTER_AGREEMENT,
    )
    with pytest.raises(ValidationError):
        Agreement.objects.create(
            jurisdiction=jurisdiction, vendor=vendor,
            agreement_type=Agreement.AgreementType.MASTER_AGREEMENT,
            parent_agreement=other_master,
        )


def test_agreement_cannot_be_its_own_parent(jurisdiction, vendor):
    agreement = Agreement.objects.create(
        jurisdiction=jurisdiction, vendor=vendor,
        agreement_type=Agreement.AgreementType.STANDALONE_AGREEMENT,
    )
    agreement.parent_agreement = agreement
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            # bypass application validation to prove the DB-level CHECK
            # constraint independently blocks this, not just clean()
            Agreement.objects.filter(pk=agreement.pk).update(parent_agreement=agreement)


def test_multiple_simultaneous_agreements_same_jurisdiction_and_vendor(jurisdiction, vendor):
    # A jurisdiction can have more than one concurrent agreement with the
    # same vendor (e.g. a camera agreement and a separate software
    # license) -- these must not collide on any unique constraint.
    a = Agreement.objects.create(
        jurisdiction=jurisdiction, vendor=vendor,
        agreement_type=Agreement.AgreementType.STANDALONE_AGREEMENT, scope_note="ALPR cameras",
    )
    b = Agreement.objects.create(
        jurisdiction=jurisdiction, vendor=vendor,
        agreement_type=Agreement.AgreementType.STANDALONE_AGREEMENT, scope_note="RTCC software license",
    )
    assert a.pk != b.pk


def test_agreement_has_no_mutable_contract_term_fields(jurisdiction, vendor):
    agreement = Agreement.objects.create(
        jurisdiction=jurisdiction, vendor=vendor,
        agreement_type=Agreement.AgreementType.STANDALONE_AGREEMENT,
    )
    field_names = {f.name for f in Agreement._meta.get_fields()}
    for forbidden in ("end_date", "start_date", "total_contract_value", "camera_count",
                      "renewal_mechanism", "status"):
        assert forbidden not in field_names


def test_agreement_delete_is_not_allowed(jurisdiction, vendor):
    agreement = Agreement.objects.create(
        jurisdiction=jurisdiction, vendor=vendor,
        agreement_type=Agreement.AgreementType.STANDALONE_AGREEMENT,
    )
    with pytest.raises(RecordDeletionNotAllowed):
        agreement.delete()
    assert Agreement.objects.filter(pk=agreement.pk).exists()


def test_agreement_queryset_delete_is_not_allowed(jurisdiction, vendor):
    Agreement.objects.create(
        jurisdiction=jurisdiction, vendor=vendor,
        agreement_type=Agreement.AgreementType.STANDALONE_AGREEMENT,
    )
    with pytest.raises(RecordDeletionNotAllowed):
        Agreement.objects.all().delete()
    assert Agreement.objects.count() == 1


class TestAgreementRelationship:
    def test_create_relationship(self, jurisdiction, vendor):
        old = Agreement.objects.create(
            jurisdiction=jurisdiction, vendor=vendor,
            agreement_type=Agreement.AgreementType.STANDALONE_AGREEMENT,
        )
        new = Agreement.objects.create(
            jurisdiction=jurisdiction, vendor=vendor,
            agreement_type=Agreement.AgreementType.STANDALONE_AGREEMENT,
        )
        rel = AgreementRelationship.objects.create(
            from_agreement=new, to_agreement=old,
            relationship_type=AgreementRelationship.RelationshipType.REPLACES,
        )
        assert rel in new.outgoing_relationships.all()
        assert rel in old.incoming_relationships.all()

    def test_self_link_rejected_by_application_validation(self, jurisdiction, vendor):
        agreement = Agreement.objects.create(
            jurisdiction=jurisdiction, vendor=vendor,
            agreement_type=Agreement.AgreementType.STANDALONE_AGREEMENT,
        )
        with pytest.raises(ValidationError):
            AgreementRelationship.objects.create(
                from_agreement=agreement, to_agreement=agreement,
                relationship_type=AgreementRelationship.RelationshipType.REPLACES,
            )

    def test_self_link_rejected_by_database_constraint(self, jurisdiction, vendor):
        agreement = Agreement.objects.create(
            jurisdiction=jurisdiction, vendor=vendor,
            agreement_type=Agreement.AgreementType.STANDALONE_AGREEMENT,
        )
        with pytest.raises(IntegrityError):
            with transaction.atomic():
                AgreementRelationship._base_manager.bulk_create([
                    AgreementRelationship(
                        from_agreement=agreement, to_agreement=agreement,
                        relationship_type=AgreementRelationship.RelationshipType.REPLACES,
                    )
                ])

    def test_duplicate_directional_relationship_rejected(self, jurisdiction, vendor):
        # save() calls full_clean(), so Django's own validate_unique()
        # catches this before any SQL is sent -- ValidationError, not
        # IntegrityError, on the ordinary create() path.
        a = Agreement.objects.create(
            jurisdiction=jurisdiction, vendor=vendor,
            agreement_type=Agreement.AgreementType.STANDALONE_AGREEMENT,
        )
        b = Agreement.objects.create(
            jurisdiction=jurisdiction, vendor=vendor,
            agreement_type=Agreement.AgreementType.STANDALONE_AGREEMENT,
        )
        AgreementRelationship.objects.create(
            from_agreement=a, to_agreement=b,
            relationship_type=AgreementRelationship.RelationshipType.REPLACES,
        )
        with pytest.raises(ValidationError):
            AgreementRelationship.objects.create(
                from_agreement=a, to_agreement=b,
                relationship_type=AgreementRelationship.RelationshipType.REPLACES,
            )

    def test_duplicate_directional_relationship_rejected_by_database_independent_of_validation(
        self, jurisdiction, vendor,
    ):
        # Bypasses full_clean() (bulk_create() never calls it) to prove
        # the UniqueConstraint itself rejects this, not merely the
        # application-level check duplicating it.
        a = Agreement.objects.create(
            jurisdiction=jurisdiction, vendor=vendor,
            agreement_type=Agreement.AgreementType.STANDALONE_AGREEMENT,
        )
        b = Agreement.objects.create(
            jurisdiction=jurisdiction, vendor=vendor,
            agreement_type=Agreement.AgreementType.STANDALONE_AGREEMENT,
        )
        AgreementRelationship._base_manager.bulk_create([
            AgreementRelationship(
                from_agreement=a, to_agreement=b,
                relationship_type=AgreementRelationship.RelationshipType.REPLACES,
            )
        ])
        with pytest.raises(IntegrityError):
            with transaction.atomic():
                AgreementRelationship._base_manager.bulk_create([
                    AgreementRelationship(
                        from_agreement=a, to_agreement=b,
                        relationship_type=AgreementRelationship.RelationshipType.REPLACES,
                    )
                ])

    def test_reverse_direction_is_a_distinct_relationship(self, jurisdiction, vendor):
        a = Agreement.objects.create(
            jurisdiction=jurisdiction, vendor=vendor,
            agreement_type=Agreement.AgreementType.STANDALONE_AGREEMENT,
        )
        b = Agreement.objects.create(
            jurisdiction=jurisdiction, vendor=vendor,
            agreement_type=Agreement.AgreementType.STANDALONE_AGREEMENT,
        )
        AgreementRelationship.objects.create(
            from_agreement=a, to_agreement=b,
            relationship_type=AgreementRelationship.RelationshipType.REPLACES,
        )
        # Not a duplicate -- opposite direction, same pair.
        AgreementRelationship.objects.create(
            from_agreement=b, to_agreement=a,
            relationship_type=AgreementRelationship.RelationshipType.REPLACES,
        )
        assert AgreementRelationship.objects.count() == 2

    def test_different_relationship_type_same_pair_is_allowed(self, jurisdiction, vendor):
        a = Agreement.objects.create(
            jurisdiction=jurisdiction, vendor=vendor,
            agreement_type=Agreement.AgreementType.STANDALONE_AGREEMENT,
        )
        b = Agreement.objects.create(
            jurisdiction=jurisdiction, vendor=vendor,
            agreement_type=Agreement.AgreementType.STANDALONE_AGREEMENT,
        )
        AgreementRelationship.objects.create(
            from_agreement=a, to_agreement=b,
            relationship_type=AgreementRelationship.RelationshipType.REPLACES,
        )
        AgreementRelationship.objects.create(
            from_agreement=a, to_agreement=b,
            relationship_type=AgreementRelationship.RelationshipType.CONSOLIDATED_FROM,
        )
        assert AgreementRelationship.objects.count() == 2
