"""Tests for AuthorityActor protocol conformance."""

from tollbooth.actor_types import ToolPathInfo
from tollbooth.authority_protocol import AuthorityProtocol

from tollbooth_authority.actor import AuthorityActor


def test_isinstance_conformance():
    """AuthorityActor satisfies AuthorityProtocol at runtime."""
    assert isinstance(AuthorityActor(), AuthorityProtocol)


def test_dict_does_not_satisfy():
    """A plain dict must not satisfy AuthorityProtocol."""
    assert not isinstance({}, AuthorityProtocol)


def test_slug():
    """Slug is 'authority'."""
    assert AuthorityActor().slug == "authority"


def test_tool_catalog_completeness():
    """Catalog has exactly 16 entries matching Protocol method names."""
    catalog = AuthorityActor.tool_catalog()
    assert len(catalog) == 16

    # Every entry is a ToolPathInfo
    for entry in catalog:
        assert isinstance(entry, ToolPathInfo)

    # Tool names match the Protocol's async methods
    expected = {
        "certify_credits",
        "register_operator",
        "operator_status",
        "check_balance",
        "account_statement",
        "account_statement_infographic",
        "service_status",
        "report_upstream_purchase",
        "purchase_credits",
        "check_payment",
        "check_dpyc_membership",
        "get_pricing_model",
        "set_pricing_model",
        "register_authority_npub",
        "confirm_authority_claim",
        "check_authority_approval",
    }
    actual = {e.tool_name for e in catalog}
    assert actual == expected


def test_tool_catalog_returns_copy():
    """tool_catalog() returns a fresh list each time (not the module constant)."""
    a = AuthorityActor.tool_catalog()
    b = AuthorityActor.tool_catalog()
    assert a == b
    assert a is not b
