import pytest

from registry.services.deadlines import ActionOpportunity, DeadlineType, compute_action_opportunities


def test_app_is_installed():
    from django.apps import apps

    assert apps.is_installed("registry")


def test_action_opportunity_is_constructible():
    opp = ActionOpportunity(deadline_type=DeadlineType.UNKNOWN)
    assert opp.is_primary is False
    assert opp.confidence.value == "none"


def test_compute_action_opportunities_not_yet_implemented():
    # Scaffolding checkpoint: the frozen interface exists and is callable;
    # the body is filled in during actual implementation, not scaffolding.
    with pytest.raises(NotImplementedError):
        compute_action_opportunities(agreement=None)
