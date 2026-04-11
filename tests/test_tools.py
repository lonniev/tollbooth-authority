"""Tests for server tools with mocked dependencies."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pynostr.key import PrivateKey  # type: ignore[import-untyped]

from tollbooth import UserLedger, LedgerCache

from tollbooth_authority.config import AuthoritySettings
from tollbooth_authority.nostr_signing import AuthorityNostrSigner, NOSTR_CERT_KIND
from tollbooth_authority.registry import DPYCRegistry, RegistryError
from tollbooth_authority.replay import ReplayTracker

SAMPLE_NPUB = "npub1l94pd4qu4eszrl6ek032ftcnsu3tt9a7xvq2zp7eaxeklp6mrpzssmq8pf"


def _ledger_with_balance(sats: int, **kwargs) -> UserLedger:
    """Create a UserLedger with the given balance via a tranche deposit."""
    ledger = UserLedger(**kwargs)
    if sats > 0:
        ledger.credit_deposit(sats, "test-seed")
    return ledger


def _mock_pricing_resolver():
    """Return an AsyncMock pricing resolver with 2% ad valorem certify_credits."""
    from tollbooth import ToolPricing
    resolver = AsyncMock()
    resolver.get_tool_pricing = AsyncMock(
        return_value=ToolPricing(rate_percent=2.0, rate_param="amount_sats", min_cost=10)
    )
    return resolver


@pytest.fixture(autouse=True)
def _mock_runtime():
    """Mock OperatorRuntime methods so tests don't need Neon/bootstrap."""
    import tollbooth_authority.server as srv
    with (
        patch.object(srv.runtime, "debit_or_deny", new_callable=AsyncMock, return_value=None),
        patch.object(srv.runtime, "pricing_resolver", new_callable=AsyncMock, return_value=_mock_pricing_resolver()),
        patch.object(srv.runtime, "rollback_debit", new_callable=AsyncMock),
        patch.object(srv.runtime, "fire_and_forget_demand_increment"),
        patch.object(srv.runtime, "mcp_name_for", return_value="authority_certify_credits"),
        patch.object(srv.runtime, "inject_low_balance_warning", new_callable=AsyncMock, side_effect=lambda r, n: r),
    ):
        yield


def _make_nostr_signer() -> AuthorityNostrSigner:
    """Create an AuthorityNostrSigner from a fresh nsec."""
    return AuthorityNostrSigner(PrivateKey().bech32())


def _make_settings(**overrides) -> AuthoritySettings:
    defaults = {
        "btcpay_host": "",
        "btcpay_store_id": "",
        "btcpay_api_key": "",
        "thebrain_api_key": "",
        "thebrain_vault_brain_id": "",
        "thebrain_vault_home_id": "",
        "certificate_ttl_seconds": 600,
    }
    defaults.update(overrides)
    return AuthoritySettings(**defaults)


# ---------------------------------------------------------------------------
# certify_credits logic tests (isolated from FastMCP)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_certify_credits_success():
    """Successful certification deducts fee and returns Nostr event certificate."""
    import tollbooth_authority.server as srv

    nostr_signer = _make_nostr_signer()
    settings = _make_settings(certificate_ttl_seconds=600)

    # Mock ledger with sufficient balance
    ledger = _ledger_with_balance(1000)
    cache = MagicMock(spec=LedgerCache)
    cache.get = AsyncMock(return_value=ledger)
    cache.mark_dirty = MagicMock()
    cache.flush_user = AsyncMock(return_value=True)

    replay = ReplayTracker(ttl_seconds=600)

    with (
        patch.object(srv, "_get_settings", return_value=settings),
        patch.object(srv, "_get_nostr_signer", return_value=nostr_signer),
        patch.object(srv.runtime, "ledger_cache", new_callable=AsyncMock, return_value=cache),
        patch.object(srv, "_get_replay_tracker", return_value=replay),
    ):
        result = await srv.certify_credits(npub="op-1", amount_sats= 1000)

    assert result["success"] is True
    assert "certificate" in result
    assert result["amount_sats"] == 1000
    # Fee: max(10, ceil(1000 * 2.0 / 100)) = max(10, 20) = 20
    assert result["fee_sats"] == 20
    assert result["net_sats"] == 980
    # Billing (debit/mark_dirty) is handled by the paid_tool wrapper.
    # The function body calls flush_user for credit-critical persistence.
    cache.flush_user.assert_called_once_with("op-1")
    # Verify certificate is a valid Nostr event with correct claims
    event_dict = json.loads(result["certificate"])
    assert event_dict["kind"] == NOSTR_CERT_KIND
    content = json.loads(event_dict["content"])
    assert content["dpyc_protocol"] == "dpyp-01-base-certificate"


@pytest.mark.asyncio
async def test_certify_credits_returns_fee_sats():
    """certify_credits returns fee_sats."""
    import tollbooth_authority.server as srv

    nostr_signer = _make_nostr_signer()
    settings = _make_settings(certificate_ttl_seconds=600)

    ledger = _ledger_with_balance(1000)
    cache = MagicMock(spec=LedgerCache)
    cache.get = AsyncMock(return_value=ledger)
    cache.mark_dirty = MagicMock()
    cache.flush_user = AsyncMock(return_value=True)

    replay = ReplayTracker(ttl_seconds=600)

    with (
        patch.object(srv, "_get_settings", return_value=settings),
        patch.object(srv, "_get_nostr_signer", return_value=nostr_signer),
        patch.object(srv.runtime, "ledger_cache", new_callable=AsyncMock, return_value=cache),
        patch.object(srv, "_get_replay_tracker", return_value=replay),
    ):
        result = await srv.certify_credits(npub="op-1", amount_sats= 1000)

    assert result["success"] is True
    assert "fee_sats" in result
    assert "tax_paid_sats" not in result


# test_certify_credits_insufficient_balance removed —
# balance checking is now handled by paid_tool (debit_or_deny) in the wheel.
# Tested in tollbooth-dpyc/tests/test_runtime_onboarding.py.


@pytest.mark.asyncio
async def test_certify_credits_negative_amount():
    """Negative amount is rejected."""
    import tollbooth_authority.server as srv

    result = await srv.certify_credits(npub="op-1", amount_sats= -100)
    assert result["success"] is False
    assert "positive" in result["error"]


@pytest.mark.asyncio
async def test_certify_credits_zero_amount():
    """Zero amount is rejected."""
    import tollbooth_authority.server as srv

    result = await srv.certify_credits(npub="op-1", amount_sats= 0)
    assert result["success"] is False


@pytest.mark.asyncio
async def test_certify_credits_applies_minimum_fee():
    """Minimum fee from pricing model is enforced."""
    import tollbooth_authority.server as srv

    nostr_signer = _make_nostr_signer()
    settings = _make_settings(certificate_ttl_seconds=600)

    # Fee comes from pricing model now
    ledger = _ledger_with_balance(500)
    cache = MagicMock(spec=LedgerCache)
    cache.get = AsyncMock(return_value=ledger)
    cache.mark_dirty = MagicMock()
    cache.flush_user = AsyncMock(return_value=True)

    replay = ReplayTracker(ttl_seconds=600)

    with (
        patch.object(srv, "_get_settings", return_value=settings),
        patch.object(srv, "_get_nostr_signer", return_value=nostr_signer),
        patch.object(srv.runtime, "ledger_cache", new_callable=AsyncMock, return_value=cache),
        patch.object(srv, "_get_replay_tracker", return_value=replay),
    ):
        result = await srv.certify_credits(npub="op-1", amount_sats= 100)

    assert result["success"] is True
    assert result["fee_sats"] == 10  # min_sats, not 2%
    assert result["net_sats"] == 90


# ---------------------------------------------------------------------------
# register_operator
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_register_operator():
    import tollbooth_authority.server as srv

    ledger = UserLedger()
    cache = MagicMock(spec=LedgerCache)
    cache.get = AsyncMock(return_value=ledger)
    cache.mark_dirty = MagicMock()
    cache.flush_user = AsyncMock(return_value=True)

    with patch.object(srv.runtime, "ledger_cache", new_callable=AsyncMock, return_value=cache):
        result = await srv.register_operator(npub=SAMPLE_NPUB)

    assert result["success"] is True
    assert result["npub"] == SAMPLE_NPUB
    assert result["dpyc_npub"] == SAMPLE_NPUB
    cache.get.assert_called_once_with(SAMPLE_NPUB)
    cache.mark_dirty.assert_called_once_with(SAMPLE_NPUB)


# ---------------------------------------------------------------------------
# operator_status
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_operator_status():
    import tollbooth_authority.server as srv

    nostr_signer = _make_nostr_signer()
    settings = _make_settings()
    ledger = UserLedger()
    ledger.credit_deposit(1000, "test-seed")
    ledger.debit("spend", 500)
    cache = MagicMock(spec=LedgerCache)
    cache.get = AsyncMock(return_value=ledger)
    cache.health = MagicMock(return_value={"status": "ok"})

    with (
        patch.object(srv, "_get_settings", return_value=settings),
        patch.object(srv, "_get_nostr_signer", return_value=nostr_signer),
        patch.object(srv.runtime, "ledger_cache", new_callable=AsyncMock, return_value=cache),
    ):
        result = await srv.operator_status(npub=SAMPLE_NPUB)

    assert result["npub"] == SAMPLE_NPUB
    assert result["dpyc_npub"] == SAMPLE_NPUB
    assert result["registered"] is True
    assert result["balance_sats"] == 500
    assert result["authority_npub"] == nostr_signer.npub
    assert result["nostr_certificate_enabled"] is True
    # No authority_public_key field anymore
    assert "authority_public_key" not in result
    # Prime Authority — no upstream config surfaced
    assert "upstream_authority_address" not in result


@pytest.mark.asyncio
async def test_operator_status_shows_upstream():
    """operator_status surfaces upstream chain config when configured."""
    import tollbooth_authority.server as srv

    nostr_signer = _make_nostr_signer()
    settings = _make_settings(
        upstream_authority_address="upstream@btcpay.example.com",
    )
    ledger = UserLedger()
    ledger.credit_deposit(1000, "test-seed")
    ledger.debit("spend", 500)
    cache = MagicMock(spec=LedgerCache)
    cache.get = AsyncMock(return_value=ledger)
    cache.health = MagicMock(return_value={"status": "ok"})

    with (
        patch.object(srv, "_get_settings", return_value=settings),
        patch.object(srv, "_get_nostr_signer", return_value=nostr_signer),
        patch.object(srv.runtime, "ledger_cache", new_callable=AsyncMock, return_value=cache),
    ):
        result = await srv.operator_status(npub=SAMPLE_NPUB)

    assert result["upstream_authority_address"] == "upstream@btcpay.example.com"
    assert "upstream_authority_address" in result


# ---------------------------------------------------------------------------
# Upstream auto-certification
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_certify_credits_calls_upstream_certifier():
    """Non-Prime Authority: certify_credits calls AuthorityCertifier.certify()."""
    import tollbooth_authority.server as srv

    nostr_signer = _make_nostr_signer()
    settings = _make_settings(
        certificate_ttl_seconds=600,
        upstream_authority_address="https://upstream.example.com",
    )

    operator_ledger = _ledger_with_balance(1000)

    cache = MagicMock(spec=LedgerCache)
    cache.get = AsyncMock(return_value=operator_ledger)
    cache.mark_dirty = MagicMock()
    cache.flush_user = AsyncMock(return_value=True)

    replay = ReplayTracker(ttl_seconds=600)

    mock_upstream_result = {
        "success": True,
        "certificate": '{"kind":30079}',
        "jti": "upstream-jti-123",
        "amount_sats": 1000,
        "fee_sats": 20,
        "net_sats": 980,
    }
    mock_certifier = AsyncMock(return_value=mock_upstream_result)

    with (
        patch.object(srv, "_get_settings", return_value=settings),
        patch.object(srv, "_get_nostr_signer", return_value=nostr_signer),
        patch.object(srv.runtime, "ledger_cache", new_callable=AsyncMock, return_value=cache),
        patch.object(srv, "_get_replay_tracker", return_value=replay),
        patch.object(srv, "_get_authority_npub", new_callable=AsyncMock, return_value=nostr_signer.npub),
        patch("tollbooth.authority_client.AuthorityCertifier") as MockCertifierClass,
    ):
        MockCertifierClass.return_value.certify_credits = mock_certifier
        result = await srv.certify_credits(npub="op-1", amount_sats= 1000)

    assert result["success"] is True
    mock_certifier.assert_called_once_with(1000)
    assert result["upstream_certificate"] == '{"kind":30079}'
    assert result["upstream_jti"] == "upstream-jti-123"


@pytest.mark.asyncio
async def test_certify_credits_upstream_failure_rollback():
    """Non-Prime Authority: upstream failure rolls back operator fee debit."""
    import tollbooth_authority.server as srv
    from tollbooth.authority_client import AuthorityCertifyError

    nostr_signer = _make_nostr_signer()
    settings = _make_settings(
        certificate_ttl_seconds=600,
        upstream_authority_address="https://upstream.example.com",
    )

    operator_ledger = _ledger_with_balance(1000)

    cache = MagicMock(spec=LedgerCache)
    cache.get = AsyncMock(return_value=operator_ledger)
    cache.mark_dirty = MagicMock()
    cache.flush_user = AsyncMock(return_value=True)

    replay = ReplayTracker(ttl_seconds=600)

    with (
        patch.object(srv, "_get_settings", return_value=settings),
        patch.object(srv, "_get_nostr_signer", return_value=nostr_signer),
        patch.object(srv.runtime, "ledger_cache", new_callable=AsyncMock, return_value=cache),
        patch.object(srv, "_get_replay_tracker", return_value=replay),
        patch.object(srv, "_get_authority_npub", new_callable=AsyncMock, return_value=nostr_signer.npub),
        patch("tollbooth.authority_client.AuthorityCertifier") as MockCertifierClass,
    ):
        MockCertifierClass.return_value.certify_credits = AsyncMock(
            side_effect=AuthorityCertifyError("connection refused")
        )
        result = await srv.certify_credits(npub="op-1", amount_sats= 1000)

    assert result["success"] is False
    assert "Upstream certification failed" in result["error"]
    # Operator balance should be rolled back
    assert operator_ledger.balance_api_sats == 1000


@pytest.mark.asyncio
async def test_certify_credits_prime_skips_upstream():
    """Prime Authority (no upstream): AuthorityCertifier never instantiated."""
    import tollbooth_authority.server as srv

    nostr_signer = _make_nostr_signer()
    settings = _make_settings(
        certificate_ttl_seconds=600,
        upstream_authority_address="",  # Prime
    )

    operator_ledger = _ledger_with_balance(1000)

    cache = MagicMock(spec=LedgerCache)
    cache.get = AsyncMock(return_value=operator_ledger)
    cache.mark_dirty = MagicMock()
    cache.flush_user = AsyncMock(return_value=True)

    replay = ReplayTracker(ttl_seconds=600)

    with (
        patch.object(srv, "_get_settings", return_value=settings),
        patch.object(srv, "_get_nostr_signer", return_value=nostr_signer),
        patch.object(srv.runtime, "ledger_cache", new_callable=AsyncMock, return_value=cache),
        patch.object(srv, "_get_replay_tracker", return_value=replay),
        patch("tollbooth.authority_client.AuthorityCertifier") as MockCertifierClass,
    ):
        result = await srv.certify_credits(npub="op-1", amount_sats= 1000)

    assert result["success"] is True
    # AuthorityCertifier should never be instantiated for Prime
    MockCertifierClass.assert_not_called()
    # No upstream fields in response
    assert "upstream_certificate" not in result
    assert "upstream_jti" not in result


# test_report_upstream_purchase_deprecated removed — tool deleted.


@pytest.mark.asyncio
async def test_operator_status_shows_upstream_auto_mode():
    """Non-Prime Authority: operator_status shows upstream_supply_mode=auto."""
    import tollbooth_authority.server as srv

    nostr_signer = _make_nostr_signer()
    settings = _make_settings(
        upstream_authority_address="upstream@example.com",
    )

    operator_ledger = UserLedger()
    operator_ledger.credit_deposit(1000, "test-seed")

    cache = MagicMock(spec=LedgerCache)
    cache.get = AsyncMock(return_value=operator_ledger)
    cache.health = MagicMock(return_value={"status": "ok"})

    with (
        patch.object(srv, "_get_settings", return_value=settings),
        patch.object(srv, "_get_nostr_signer", return_value=nostr_signer),
        patch.object(srv.runtime, "ledger_cache", new_callable=AsyncMock, return_value=cache),
    ):
        result = await srv.operator_status(npub=SAMPLE_NPUB)

    assert "upstream_authority_address" in result
    # Old supply fields should not be present
    assert "upstream_supply_sats" not in result
    assert "upstream_supply_consumed_sats" not in result


# ---------------------------------------------------------------------------
# DPYC Identity Tools
# ---------------------------------------------------------------------------


# test_activate_dpyc tests removed — tool deleted.


@pytest.mark.asyncio
async def test_register_operator_invalid_npub():
    """register_operator rejects invalid npub format."""
    import tollbooth_authority.server as srv

    result = await srv.register_operator(npub="not-an-npub")

    assert result["success"] is False
    assert "Invalid npub" in result["error"]


@pytest.mark.asyncio
async def test_register_operator_provisions_npub():
    """register_operator provisions the given npub as operator identity."""
    import tollbooth_authority.server as srv

    ledger = UserLedger()
    cache = MagicMock(spec=LedgerCache)
    cache.get = AsyncMock(return_value=ledger)
    cache.mark_dirty = MagicMock()
    cache.flush_user = AsyncMock(return_value=True)

    with patch.object(srv.runtime, "ledger_cache", new_callable=AsyncMock, return_value=cache):
        result = await srv.register_operator(npub=SAMPLE_NPUB)

    assert result["success"] is True
    assert result["npub"] == SAMPLE_NPUB
    cache.get.assert_called_once_with(SAMPLE_NPUB)


@pytest.mark.asyncio
async def test_register_operator_uses_npub_for_ledger():
    """register_operator uses the provided npub as ledger key."""
    import tollbooth_authority.server as srv

    ledger = _ledger_with_balance(42)
    cache = MagicMock(spec=LedgerCache)
    cache.get = AsyncMock(return_value=ledger)
    cache.mark_dirty = MagicMock()
    cache.flush_user = AsyncMock(return_value=True)

    with patch.object(srv.runtime, "ledger_cache", new_callable=AsyncMock, return_value=cache):
        result = await srv.register_operator(npub=SAMPLE_NPUB)

    assert result["npub"] == SAMPLE_NPUB
    assert result["balance_sats"] == 42
    cache.get.assert_called_once_with(SAMPLE_NPUB)
    cache.mark_dirty.assert_called_once_with(SAMPLE_NPUB)
    cache.flush_user.assert_called_once_with(SAMPLE_NPUB)


# test_purchase_credits_no_npub_returns_error removed — purchase_credits is now in the wheel.


# ---------------------------------------------------------------------------
# DPYC Registry enforcement in certify_credits
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_certify_credits_registry_active_member():
    """Registry enforcement: active member succeeds."""
    import tollbooth_authority.server as srv

    nostr_signer = _make_nostr_signer()
    settings = _make_settings(
        certificate_ttl_seconds=600,
        dpyc_enforce_membership=True,
    )

    ledger = _ledger_with_balance(1000)
    cache = MagicMock(spec=LedgerCache)
    cache.get = AsyncMock(return_value=ledger)
    cache.mark_dirty = MagicMock()
    cache.flush_user = AsyncMock(return_value=True)

    replay = ReplayTracker(ttl_seconds=600)

    mock_registry = MagicMock(spec=DPYCRegistry)
    mock_registry.check_membership = AsyncMock(return_value={"npub": "op-1", "status": "active"})

    with (
        patch.object(srv, "_get_settings", return_value=settings),
        patch.object(srv, "_get_nostr_signer", return_value=nostr_signer),
        patch.object(srv.runtime, "ledger_cache", new_callable=AsyncMock, return_value=cache),
        patch.object(srv, "_get_replay_tracker", return_value=replay),
        patch.object(srv, "_get_dpyc_registry", return_value=mock_registry),
    ):
        result = await srv.certify_credits(npub="op-1", amount_sats= 1000)

    assert result["success"] is True
    mock_registry.check_membership.assert_called_once_with("op-1")


@pytest.mark.asyncio
async def test_certify_credits_registry_non_member_rejected():
    """Registry enforcement: non-member rejected with fee rollback."""
    import tollbooth_authority.server as srv

    nostr_signer = _make_nostr_signer()
    settings = _make_settings(
        certificate_ttl_seconds=600,
        dpyc_enforce_membership=True,
    )

    ledger = _ledger_with_balance(1000)
    cache = MagicMock(spec=LedgerCache)
    cache.get = AsyncMock(return_value=ledger)
    cache.mark_dirty = MagicMock()

    replay = ReplayTracker(ttl_seconds=600)

    mock_registry = MagicMock(spec=DPYCRegistry)
    mock_registry.check_membership = AsyncMock(side_effect=RegistryError("not found"))

    with (
        patch.object(srv, "_get_settings", return_value=settings),
        patch.object(srv, "_get_nostr_signer", return_value=nostr_signer),
        patch.object(srv.runtime, "ledger_cache", new_callable=AsyncMock, return_value=cache),
        patch.object(srv, "_get_replay_tracker", return_value=replay),
        patch.object(srv, "_get_dpyc_registry", return_value=mock_registry),
    ):
        result = await srv.certify_credits(npub="op-1", amount_sats= 1000)

    assert result["success"] is False
    assert "DPYC membership check failed" in result["error"]
    # Fee should be rolled back
    assert ledger.balance_api_sats == 1000


@pytest.mark.asyncio
async def test_certify_credits_registry_unreachable_fails_closed():
    """Registry unreachable: fails closed with rollback."""
    import tollbooth_authority.server as srv

    nostr_signer = _make_nostr_signer()
    settings = _make_settings(
        certificate_ttl_seconds=600,
        dpyc_enforce_membership=True,
    )

    ledger = _ledger_with_balance(1000)
    cache = MagicMock(spec=LedgerCache)
    cache.get = AsyncMock(return_value=ledger)
    cache.mark_dirty = MagicMock()

    replay = ReplayTracker(ttl_seconds=600)

    mock_registry = MagicMock(spec=DPYCRegistry)
    mock_registry.check_membership = AsyncMock(side_effect=RegistryError("fetch failed"))

    with (
        patch.object(srv, "_get_settings", return_value=settings),
        patch.object(srv, "_get_nostr_signer", return_value=nostr_signer),
        patch.object(srv.runtime, "ledger_cache", new_callable=AsyncMock, return_value=cache),
        patch.object(srv, "_get_replay_tracker", return_value=replay),
        patch.object(srv, "_get_dpyc_registry", return_value=mock_registry),
    ):
        result = await srv.certify_credits(npub="op-1", amount_sats= 1000)

    assert result["success"] is False
    assert "fetch failed" in result["error"]
    assert ledger.balance_api_sats == 1000


@pytest.mark.asyncio
async def test_certify_credits_enforcement_disabled_no_check():
    """Enforcement disabled: no registry check, certification proceeds."""
    import tollbooth_authority.server as srv

    nostr_signer = _make_nostr_signer()
    settings = _make_settings(
        certificate_ttl_seconds=600,
        dpyc_enforce_membership=False,
    )

    ledger = _ledger_with_balance(1000)
    cache = MagicMock(spec=LedgerCache)
    cache.get = AsyncMock(return_value=ledger)
    cache.mark_dirty = MagicMock()
    cache.flush_user = AsyncMock(return_value=True)

    replay = ReplayTracker(ttl_seconds=600)

    with (
        patch.object(srv, "_get_settings", return_value=settings),
        patch.object(srv, "_get_nostr_signer", return_value=nostr_signer),
        patch.object(srv.runtime, "ledger_cache", new_callable=AsyncMock, return_value=cache),
        patch.object(srv, "_get_replay_tracker", return_value=replay),
        patch.object(srv, "_get_dpyc_registry", return_value=None),
    ):
        result = await srv.certify_credits(npub="op-1", amount_sats= 1000)

    assert result["success"] is True


# ---------------------------------------------------------------------------
# Nostr event certificate content verification
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_certify_credits_certificate_is_valid_nostr_event():
    """certify_credits returns a valid signed Nostr event as the certificate."""
    import tollbooth_authority.server as srv

    nostr_signer = _make_nostr_signer()
    settings = _make_settings(certificate_ttl_seconds=600)

    ledger = _ledger_with_balance(1000)
    cache = MagicMock(spec=LedgerCache)
    cache.get = AsyncMock(return_value=ledger)
    cache.mark_dirty = MagicMock()
    cache.flush_user = AsyncMock(return_value=True)

    replay = ReplayTracker(ttl_seconds=600)

    with (
        patch.object(srv, "_get_settings", return_value=settings),
        patch.object(srv, "_get_nostr_signer", return_value=nostr_signer),
        patch.object(srv.runtime, "ledger_cache", new_callable=AsyncMock, return_value=cache),
        patch.object(srv, "_get_replay_tracker", return_value=replay),
    ):
        result = await srv.certify_credits(npub="op-1", amount_sats= 1000)

    assert result["success"] is True
    assert "certificate" in result
    # No separate nostr_event field — certificate IS the Nostr event
    assert "nostr_event" not in result

    # Verify the certificate is valid JSON with correct kind
    event_dict = json.loads(result["certificate"])
    assert event_dict["kind"] == NOSTR_CERT_KIND
    assert "sig" in event_dict
    assert event_dict["pubkey"] == nostr_signer.pubkey_hex


@pytest.mark.asyncio
async def test_certify_credits_nostr_event_verifiable():
    """The Nostr event returned by certify_credits passes Schnorr verification."""
    from pynostr.event import Event  # type: ignore[import-untyped]

    import tollbooth_authority.server as srv

    nostr_signer = _make_nostr_signer()
    settings = _make_settings(certificate_ttl_seconds=600)

    ledger = _ledger_with_balance(1000)
    cache = MagicMock(spec=LedgerCache)
    cache.get = AsyncMock(return_value=ledger)
    cache.mark_dirty = MagicMock()
    cache.flush_user = AsyncMock(return_value=True)

    replay = ReplayTracker(ttl_seconds=600)

    with (
        patch.object(srv, "_get_settings", return_value=settings),
        patch.object(srv, "_get_nostr_signer", return_value=nostr_signer),
        patch.object(srv.runtime, "ledger_cache", new_callable=AsyncMock, return_value=cache),
        patch.object(srv, "_get_replay_tracker", return_value=replay),
    ):
        result = await srv.certify_credits(npub="op-1", amount_sats= 1000)

    event_dict = json.loads(result["certificate"])
    event = Event.from_dict(event_dict)
    assert event.verify() is True


@pytest.mark.asyncio
async def test_certify_credits_nostr_event_claims_match():
    """The Nostr event content matches the certificate claims (amount, fee, net)."""
    import tollbooth_authority.server as srv

    nostr_signer = _make_nostr_signer()
    settings = _make_settings(certificate_ttl_seconds=600)

    ledger = _ledger_with_balance(1000)
    cache = MagicMock(spec=LedgerCache)
    cache.get = AsyncMock(return_value=ledger)
    cache.mark_dirty = MagicMock()
    cache.flush_user = AsyncMock(return_value=True)

    replay = ReplayTracker(ttl_seconds=600)

    with (
        patch.object(srv, "_get_settings", return_value=settings),
        patch.object(srv, "_get_nostr_signer", return_value=nostr_signer),
        patch.object(srv.runtime, "ledger_cache", new_callable=AsyncMock, return_value=cache),
        patch.object(srv, "_get_replay_tracker", return_value=replay),
    ):
        result = await srv.certify_credits(npub="op-1", amount_sats= 1000)

    event_dict = json.loads(result["certificate"])
    content = json.loads(event_dict["content"])

    assert content["amount_sats"] == 1000
    assert content["fee_sats"] == 20
    assert content["net_sats"] == 980
    assert content["dpyc_protocol"] == "dpyp-01-base-certificate"


@pytest.mark.asyncio
async def test_check_dpyc_membership_found():
    """check_dpyc_membership returns member record when found."""
    import tollbooth_authority.server as srv

    settings = _make_settings()
    mock_instance = MagicMock(spec=DPYCRegistry)
    mock_instance.check_membership = AsyncMock(return_value={"npub": "npub1test", "status": "active"})
    mock_instance.close = AsyncMock()

    with (
        patch.object(srv, "_get_settings", return_value=settings),
        patch("tollbooth_authority.server.DPYCRegistry", return_value=mock_instance),
    ):
        result = await srv.check_dpyc_membership("npub1test")

    assert result["success"] is True
    assert result["member"]["status"] == "active"


@pytest.mark.asyncio
async def test_operator_status_shows_dpyc_info():
    """operator_status surfaces DPYC info when configured."""
    import tollbooth_authority.server as srv

    nostr_signer = _make_nostr_signer()
    settings = _make_settings(
        dpyc_authority_npub="npub1authority_test",
        dpyc_enforce_membership=True,
    )
    ledger = UserLedger()
    ledger.credit_deposit(1000, "test-seed")
    ledger.debit("spend", 500)
    cache = MagicMock(spec=LedgerCache)
    cache.get = AsyncMock(return_value=ledger)
    cache.health = MagicMock(return_value={"status": "ok"})

    with (
        patch.object(srv, "_get_settings", return_value=settings),
        patch.object(srv, "_get_nostr_signer", return_value=nostr_signer),
        patch.object(srv.runtime, "ledger_cache", new_callable=AsyncMock, return_value=cache),
    ):
        result = await srv.operator_status(npub=SAMPLE_NPUB)

    assert result["authority_npub"] == nostr_signer.npub
    assert result["dpyc_registry_enforcement"] is True


# ---------------------------------------------------------------------------
# service_status
# ---------------------------------------------------------------------------


# test_service_status removed — service_status is now in the wheel.
# test_report_upstream_purchase_always_deprecated removed — tool deleted.


# ---------------------------------------------------------------------------
# Authority onboarding tools
# ---------------------------------------------------------------------------

ONBOARDING_NPUB = "npub1l94pd4qu4eszrl6ek032ftcnsu3tt9a7xvq2zp7eaxeklp6mrpzssmq8pf"


@pytest.fixture(autouse=True)
def _reset_onboarding_state():
    """Reset onboarding state before each test."""
    import tollbooth_authority.server as srv
    srv._onboarding.complete()
    srv._cached_authority_npub = None
    yield
    srv._onboarding.complete()
    srv._cached_authority_npub = None


@pytest.mark.asyncio
async def test_register_authority_npub_sends_dm():
    """register_authority_npub sends a DM via exchange.open_channel."""
    import tollbooth_authority.server as srv

    mock_exchange = MagicMock()
    mock_exchange.open_channel = AsyncMock(return_value={
        "success": True,
        "message": "DM sent.",
    })

    with (
        patch.object(srv, "_get_authority_npub", new_callable=AsyncMock, return_value=None),
        patch.object(srv, "_get_nostr_exchange", return_value=mock_exchange),
    ):
        result = await srv.register_authority_npub(ONBOARDING_NPUB)

    assert result["success"] is True
    assert result["phase"] == "claim"
    mock_exchange.open_channel.assert_called_once()
    call_kwargs = mock_exchange.open_channel.call_args
    assert call_kwargs[0][0] == "authority_claim"
    assert call_kwargs[1]["recipient_npub"] == ONBOARDING_NPUB


@pytest.mark.asyncio
async def test_register_authority_npub_rejects_existing_curator():
    """register_authority_npub rejects if Authority already has a curator."""
    import tollbooth_authority.server as srv

    with patch.object(
        srv, "_get_authority_npub", new_callable=AsyncMock,
        return_value="npub1existing...",
    ):
        result = await srv.register_authority_npub(ONBOARDING_NPUB)

    assert result["success"] is False
    assert "already has a curator" in result["error"]


@pytest.mark.asyncio
async def test_register_authority_npub_rejects_invalid_npub():
    import tollbooth_authority.server as srv

    with patch.object(
        srv, "_get_authority_npub", new_callable=AsyncMock, return_value=None,
    ):
        result = await srv.register_authority_npub("not-an-npub")

    assert result["success"] is False
    assert "Invalid npub" in result["error"]


@pytest.mark.asyncio
async def test_confirm_authority_claim_success():
    """confirm_authority_claim verifies DM and sends approval to Prime."""
    import tollbooth_authority.server as srv

    # Pre-set onboarding state to "claim" phase
    srv._onboarding.start_claim(ONBOARDING_NPUB)

    mock_exchange = MagicMock()
    mock_exchange.receive = AsyncMock(return_value={
        "success": True,
        "credentials": {"claim": "yes"},
    })
    mock_exchange.open_channel = AsyncMock(return_value={
        "success": True,
        "message": "Approval DM sent.",
    })

    prime = "npub1primeauthority" + "x" * 47
    signer = _make_nostr_signer()

    with (
        patch.object(srv, "_get_nostr_exchange", return_value=mock_exchange),
        patch.object(srv, "_resolve_prime_npub", new_callable=AsyncMock, return_value=prime),
        patch.object(srv, "_get_nostr_signer", return_value=signer),
    ):
        result = await srv.confirm_authority_claim(ONBOARDING_NPUB)

    assert result["success"] is True
    assert result["phase"] == "approval"
    assert result["prime_npub"] == prime

    # Verify receive was called for the claim
    mock_exchange.receive.assert_called_once()
    # Verify approval DM was sent to Prime
    mock_exchange.open_channel.assert_called_once()
    approval_call = mock_exchange.open_channel.call_args
    assert approval_call[0][0] == "authority_approval"
    assert approval_call[1]["recipient_npub"] == prime


@pytest.mark.asyncio
async def test_confirm_authority_claim_no_dm():
    """confirm_authority_claim fails when no DM received."""
    import tollbooth_authority.server as srv

    srv._onboarding.start_claim(ONBOARDING_NPUB)

    mock_exchange = MagicMock()
    mock_exchange.receive = AsyncMock(
        side_effect=Exception("CourierTimeout: no matching DM found"),
    )

    with patch.object(srv, "_get_nostr_exchange", return_value=mock_exchange):
        result = await srv.confirm_authority_claim(ONBOARDING_NPUB)

    assert result["success"] is False
    assert "No valid claim DM" in result["error"]


@pytest.mark.asyncio
async def test_check_authority_approval_success():
    """check_authority_approval persists npub on Prime approval."""
    import tollbooth_authority.server as srv

    prime = "npub1primeauthority" + "x" * 47
    srv._onboarding.start_claim(ONBOARDING_NPUB)
    srv._onboarding.promote_to_approval(prime)

    mock_exchange = MagicMock()
    mock_exchange.receive = AsyncMock(return_value={
        "success": True,
        "credentials": {"approval": "yes"},
    })

    signer = _make_nostr_signer()

    with (
        patch.object(srv, "_get_nostr_exchange", return_value=mock_exchange),
        patch.object(srv, "_set_authority_npub", new_callable=AsyncMock) as mock_set,
        patch.object(srv, "_get_nostr_signer", return_value=signer),
        patch.object(srv, "_resolve_own_service_url", new_callable=AsyncMock, return_value="https://example.com/mcp"),
        patch.object(srv, "_register_via_oracle", new_callable=AsyncMock, return_value="https://github.com/commit/abc"),
    ):
        result = await srv.check_authority_approval(ONBOARDING_NPUB)

    assert result["success"] is True
    assert result["activated"] is True
    mock_set.assert_called_once_with(ONBOARDING_NPUB)
    # Onboarding state should be cleared
    assert srv._onboarding.get() is None


@pytest.mark.asyncio
async def test_check_authority_approval_no_response():
    """check_authority_approval fails when Prime hasn't responded."""
    import tollbooth_authority.server as srv

    prime = "npub1primeauthority" + "x" * 47
    srv._onboarding.start_claim(ONBOARDING_NPUB)
    srv._onboarding.promote_to_approval(prime)

    mock_exchange = MagicMock()
    mock_exchange.receive = AsyncMock(
        side_effect=Exception("CourierTimeout: no matching DM found"),
    )

    with patch.object(srv, "_get_nostr_exchange", return_value=mock_exchange):
        result = await srv.check_authority_approval(ONBOARDING_NPUB)

    assert result["success"] is False
    assert "No approval received" in result["error"]


