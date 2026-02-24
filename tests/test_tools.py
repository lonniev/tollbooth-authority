"""Tests for server tools with mocked dependencies."""

from __future__ import annotations

import base64
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization

from tollbooth import UserLedger, LedgerCache

from tollbooth_authority.config import AuthoritySettings
from tollbooth_authority.registry import DPYCRegistry, RegistryError
from tollbooth_authority.replay import ReplayTracker
from tollbooth_authority.signing import AuthoritySigner

SAMPLE_NPUB = "npub1l94pd4qu4eszrl6ek032ftcnsu3tt9a7xvq2zp7eaxeklp6mrpzssmq8pf"


def _ledger_with_balance(sats: int, **kwargs) -> UserLedger:
    """Create a UserLedger with the given balance via a tranche deposit."""
    ledger = UserLedger(**kwargs)
    if sats > 0:
        ledger.credit_deposit(sats, "test-seed")
    return ledger


@pytest.fixture(autouse=True)
def _clean_dpyc_sessions():
    """Ensure DPYC sessions are clean before and after each test."""
    import tollbooth_authority.server as srv
    srv._dpyc_sessions.clear()
    yield
    srv._dpyc_sessions.clear()


def _make_signer() -> AuthoritySigner:
    key = Ed25519PrivateKey.generate()
    pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    return AuthoritySigner(base64.b64encode(pem).decode())


def _make_settings(**overrides) -> AuthoritySettings:
    defaults = {
        "authority_signing_key": "",
        "btcpay_host": "",
        "btcpay_store_id": "",
        "btcpay_api_key": "",
        "thebrain_api_key": "",
        "thebrain_vault_brain_id": "",
        "thebrain_vault_home_id": "",
        "tax_rate_percent": 2.0,
        "tax_min_sats": 10,
        "certificate_ttl_seconds": 600,
    }
    defaults.update(overrides)
    return AuthoritySettings(**defaults)


# ---------------------------------------------------------------------------
# certify_credits logic tests (isolated from FastMCP)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_certify_credits_success():
    """Successful certification deducts fee and returns JWT."""
    import tollbooth_authority.server as srv

    signer = _make_signer()
    settings = _make_settings(tax_rate_percent=2.0, tax_min_sats=10, certificate_ttl_seconds=600)

    # Mock ledger with sufficient balance
    ledger = _ledger_with_balance(1000)
    cache = MagicMock(spec=LedgerCache)
    cache.get = AsyncMock(return_value=ledger)
    cache.mark_dirty = MagicMock()
    cache.flush_user = AsyncMock(return_value=True)

    replay = ReplayTracker(ttl_seconds=600)

    with (
        patch.object(srv, "_get_settings", return_value=settings),
        patch.object(srv, "_get_signer", return_value=signer),
        patch.object(srv, "_get_ledger_cache", return_value=cache),
        patch.object(srv, "_get_replay_tracker", return_value=replay),
    ):
        result = await srv.certify_credits("op-1", 1000)

    assert result["success"] is True
    assert "certificate" in result
    assert result["amount_sats"] == 1000
    # Fee: max(10, ceil(1000 * 2.0 / 100)) = max(10, 20) = 20
    assert result["tax_paid_sats"] == 20
    assert result["net_sats"] == 980
    cache.mark_dirty.assert_called_once_with("op-1")
    cache.flush_user.assert_called_once_with("op-1")
    # Verify dpyc_protocol claim in JWT
    import jwt
    claims = jwt.decode(result["certificate"], options={"verify_signature": False})
    assert claims["dpyc_protocol"] == "dpyp-01-base-certificate"


@pytest.mark.asyncio
async def test_certify_credits_returns_fee_sats():
    """certify_credits returns both fee_sats and tax_paid_sats (backward compat)."""
    import tollbooth_authority.server as srv

    signer = _make_signer()
    settings = _make_settings(tax_rate_percent=2.0, tax_min_sats=10, certificate_ttl_seconds=600)

    ledger = _ledger_with_balance(1000)
    cache = MagicMock(spec=LedgerCache)
    cache.get = AsyncMock(return_value=ledger)
    cache.mark_dirty = MagicMock()
    cache.flush_user = AsyncMock(return_value=True)

    replay = ReplayTracker(ttl_seconds=600)

    with (
        patch.object(srv, "_get_settings", return_value=settings),
        patch.object(srv, "_get_signer", return_value=signer),
        patch.object(srv, "_get_ledger_cache", return_value=cache),
        patch.object(srv, "_get_replay_tracker", return_value=replay),
    ):
        result = await srv.certify_credits("op-1", 1000)

    assert result["success"] is True
    assert result["fee_sats"] == 20
    assert result["tax_paid_sats"] == 20  # backward compat
    assert result["fee_sats"] == result["tax_paid_sats"]


@pytest.mark.asyncio
async def test_certify_credits_insufficient_balance():
    """Certification fails when operator balance is too low."""
    import tollbooth_authority.server as srv

    signer = _make_signer()
    settings = _make_settings(tax_rate_percent=2.0, tax_min_sats=10, certificate_ttl_seconds=600)

    # Mock ledger with zero balance
    ledger = UserLedger()
    cache = MagicMock(spec=LedgerCache)
    cache.get = AsyncMock(return_value=ledger)

    replay = ReplayTracker(ttl_seconds=600)

    with (
        patch.object(srv, "_get_settings", return_value=settings),
        patch.object(srv, "_get_signer", return_value=signer),
        patch.object(srv, "_get_ledger_cache", return_value=cache),
        patch.object(srv, "_get_replay_tracker", return_value=replay),
    ):
        result = await srv.certify_credits("op-1", 1000)

    assert result["success"] is False
    assert "Insufficient credit balance" in result["error"]


@pytest.mark.asyncio
async def test_certify_credits_negative_amount():
    """Negative amount is rejected."""
    import tollbooth_authority.server as srv

    result = await srv.certify_credits("op-1", -100)
    assert result["success"] is False
    assert "positive" in result["error"]


@pytest.mark.asyncio
async def test_certify_credits_zero_amount():
    """Zero amount is rejected."""
    import tollbooth_authority.server as srv

    result = await srv.certify_credits("op-1", 0)
    assert result["success"] is False


@pytest.mark.asyncio
async def test_certify_credits_applies_minimum_fee():
    """Fee floor (tax_min_sats) is enforced."""
    import tollbooth_authority.server as srv

    signer = _make_signer()
    settings = _make_settings(tax_rate_percent=2.0, tax_min_sats=10, certificate_ttl_seconds=600)

    # For amount=100: ceil(100 * 2.0 / 100) = 2 < min_sats=10, so fee=10
    ledger = _ledger_with_balance(500)
    cache = MagicMock(spec=LedgerCache)
    cache.get = AsyncMock(return_value=ledger)
    cache.mark_dirty = MagicMock()
    cache.flush_user = AsyncMock(return_value=True)

    replay = ReplayTracker(ttl_seconds=600)

    with (
        patch.object(srv, "_get_settings", return_value=settings),
        patch.object(srv, "_get_signer", return_value=signer),
        patch.object(srv, "_get_ledger_cache", return_value=cache),
        patch.object(srv, "_get_replay_tracker", return_value=replay),
    ):
        result = await srv.certify_credits("op-1", 100)

    assert result["success"] is True
    assert result["tax_paid_sats"] == 10  # min_sats, not 2%
    assert result["fee_sats"] == 10
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

    with (
        patch.object(srv, "_require_user_id", return_value="op-1"),
        patch.object(srv, "_get_ledger_cache", return_value=cache),
    ):
        result = await srv.register_operator(npub=SAMPLE_NPUB)

    assert result["success"] is True
    assert result["operator_id"] == SAMPLE_NPUB
    assert result["dpyc_npub"] == SAMPLE_NPUB
    cache.get.assert_called_once_with(SAMPLE_NPUB)
    cache.mark_dirty.assert_called_once_with(SAMPLE_NPUB)


# ---------------------------------------------------------------------------
# operator_status
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_operator_status():
    import tollbooth_authority.server as srv

    signer = _make_signer()
    settings = _make_settings()
    ledger = UserLedger()
    ledger.credit_deposit(1000, "test-seed")
    ledger.debit("spend", 500)
    cache = MagicMock(spec=LedgerCache)
    cache.get = AsyncMock(return_value=ledger)

    srv._dpyc_sessions["op-1"] = SAMPLE_NPUB

    with (
        patch.object(srv, "_require_user_id", return_value="op-1"),
        patch.object(srv, "_get_settings", return_value=settings),
        patch.object(srv, "_get_signer", return_value=signer),
        patch.object(srv, "_get_ledger_cache", return_value=cache),
    ):
        result = await srv.operator_status()

    assert result["operator_id"] == SAMPLE_NPUB
    assert result["dpyc_npub"] == SAMPLE_NPUB
    assert result["registered"] is True
    assert result["balance_sats"] == 500
    assert "BEGIN PUBLIC KEY" in result["authority_public_key"]
    # Prime Authority — no upstream config surfaced
    assert "upstream_authority_address" not in result
    # Certification fee info
    assert result["certification_fee"]["rate_percent"] == 2.0
    assert result["certification_fee"]["min_sats"] == 10


@pytest.mark.asyncio
async def test_operator_status_shows_upstream():
    """operator_status surfaces upstream chain config when configured."""
    import tollbooth_authority.server as srv

    signer = _make_signer()
    settings = _make_settings(
        upstream_authority_address="upstream@btcpay.example.com",
        upstream_tax_percent=3.0,
    )
    ledger = UserLedger()
    ledger.credit_deposit(1000, "test-seed")
    ledger.debit("spend", 500)
    cache = MagicMock(spec=LedgerCache)
    cache.get = AsyncMock(return_value=ledger)

    srv._dpyc_sessions["op-1"] = SAMPLE_NPUB

    with (
        patch.object(srv, "_require_user_id", return_value="op-1"),
        patch.object(srv, "_get_settings", return_value=settings),
        patch.object(srv, "_get_signer", return_value=signer),
        patch.object(srv, "_get_ledger_cache", return_value=cache),
    ):
        result = await srv.operator_status()

    assert result["upstream_authority_address"] == "upstream@btcpay.example.com"
    assert result["upstream_tax_percent"] == 3.0


# ---------------------------------------------------------------------------
# check_payment — upstream payout
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_check_payment_fires_upstream_payout():
    """When upstream is configured, check_payment passes royalty params."""
    import tollbooth_authority.server as srv

    settings = _make_settings(
        upstream_authority_address="upstream@btcpay.example.com",
        upstream_tax_percent=2.0,
        upstream_tax_min_sats=10,
    )

    mock_btcpay = MagicMock()
    cache = MagicMock(spec=LedgerCache)

    mock_result = {"success": True, "status": "Settled", "balance_api_sats": 1000}

    srv._dpyc_sessions["op-1"] = SAMPLE_NPUB

    with (
        patch.object(srv, "_require_user_id", return_value="op-1"),
        patch.object(srv, "_get_settings", return_value=settings),
        patch.object(srv, "_get_btcpay", return_value=mock_btcpay),
        patch.object(srv, "_get_ledger_cache", return_value=cache),
        patch("tollbooth_authority.server.check_payment_tool", new_callable=AsyncMock, return_value=mock_result) as mock_cpt,
    ):
        result = await srv.check_payment("inv-123")

    assert result["success"] is True
    # Verify upstream royalty params were passed
    mock_cpt.assert_called_once()
    call_kwargs = mock_cpt.call_args
    assert call_kwargs.kwargs["royalty_address"] == "upstream@btcpay.example.com"
    assert call_kwargs.kwargs["royalty_percent"] == 0.02  # 2.0 / 100
    assert call_kwargs.kwargs["royalty_min_sats"] == 10


@pytest.mark.asyncio
async def test_check_payment_no_upstream_for_prime():
    """Prime Authority (no upstream) passes None for royalty_address."""
    import tollbooth_authority.server as srv

    settings = _make_settings(upstream_authority_address="")

    mock_btcpay = MagicMock()
    cache = MagicMock(spec=LedgerCache)

    mock_result = {"success": True, "status": "Settled", "balance_api_sats": 1000}

    srv._dpyc_sessions["op-1"] = SAMPLE_NPUB

    with (
        patch.object(srv, "_require_user_id", return_value="op-1"),
        patch.object(srv, "_get_settings", return_value=settings),
        patch.object(srv, "_get_btcpay", return_value=mock_btcpay),
        patch.object(srv, "_get_ledger_cache", return_value=cache),
        patch("tollbooth_authority.server.check_payment_tool", new_callable=AsyncMock, return_value=mock_result) as mock_cpt,
    ):
        result = await srv.check_payment("inv-123")

    call_kwargs = mock_cpt.call_args
    assert call_kwargs.kwargs["royalty_address"] is None


# ---------------------------------------------------------------------------
# Upstream supply constraint
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_certify_credits_deducts_supply():
    """Non-Prime Authority: certify_credits debits amount_sats from supply ledger."""
    import tollbooth_authority.server as srv

    signer = _make_signer()
    settings = _make_settings(
        tax_rate_percent=2.0, tax_min_sats=10, certificate_ttl_seconds=600,
        upstream_authority_address="upstream@example.com",
    )

    operator_ledger = _ledger_with_balance(1000)
    supply_ledger = _ledger_with_balance(5000)

    async def fake_get(user_id: str) -> UserLedger:
        if user_id == srv.SUPPLY_USER_ID:
            return supply_ledger
        return operator_ledger

    cache = MagicMock(spec=LedgerCache)
    cache.get = AsyncMock(side_effect=fake_get)
    cache.mark_dirty = MagicMock()
    cache.flush_user = AsyncMock(return_value=True)

    replay = ReplayTracker(ttl_seconds=600)

    with (
        patch.object(srv, "_get_settings", return_value=settings),
        patch.object(srv, "_get_signer", return_value=signer),
        patch.object(srv, "_get_ledger_cache", return_value=cache),
        patch.object(srv, "_get_replay_tracker", return_value=replay),
    ):
        result = await srv.certify_credits("op-1", 1000)

    assert result["success"] is True
    # Supply should be debited by amount_sats (1000), not fee_sats
    assert supply_ledger.balance_api_sats == 4000
    # Verify supply was marked dirty
    cache.mark_dirty.assert_any_call(srv.SUPPLY_USER_ID)


@pytest.mark.asyncio
async def test_certify_credits_insufficient_supply():
    """Non-Prime Authority: fails and rolls back fee when supply is too low."""
    import tollbooth_authority.server as srv

    signer = _make_signer()
    settings = _make_settings(
        tax_rate_percent=2.0, tax_min_sats=10, certificate_ttl_seconds=600,
        upstream_authority_address="upstream@example.com",
    )

    operator_ledger = _ledger_with_balance(1000)
    supply_ledger = _ledger_with_balance(500)  # Not enough for 1000

    async def fake_get(user_id: str) -> UserLedger:
        if user_id == srv.SUPPLY_USER_ID:
            return supply_ledger
        return operator_ledger

    cache = MagicMock(spec=LedgerCache)
    cache.get = AsyncMock(side_effect=fake_get)
    cache.mark_dirty = MagicMock()
    cache.flush_user = AsyncMock(return_value=True)

    replay = ReplayTracker(ttl_seconds=600)

    with (
        patch.object(srv, "_get_settings", return_value=settings),
        patch.object(srv, "_get_signer", return_value=signer),
        patch.object(srv, "_get_ledger_cache", return_value=cache),
        patch.object(srv, "_get_replay_tracker", return_value=replay),
    ):
        result = await srv.certify_credits("op-1", 1000)

    assert result["success"] is False
    assert "Insufficient upstream supply" in result["error"]
    # Operator balance should be rolled back (fee_sats=20 was debited then restored)
    assert operator_ledger.balance_api_sats == 1000
    # Supply should be unchanged
    assert supply_ledger.balance_api_sats == 500


@pytest.mark.asyncio
async def test_certify_credits_prime_skips_supply():
    """Prime Authority (no upstream): no supply check at all."""
    import tollbooth_authority.server as srv

    signer = _make_signer()
    settings = _make_settings(
        tax_rate_percent=2.0, tax_min_sats=10, certificate_ttl_seconds=600,
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
        patch.object(srv, "_get_signer", return_value=signer),
        patch.object(srv, "_get_ledger_cache", return_value=cache),
        patch.object(srv, "_get_replay_tracker", return_value=replay),
    ):
        result = await srv.certify_credits("op-1", 1000)

    assert result["success"] is True
    # cache.get should only be called once (for operator), not for supply
    cache.get.assert_called_once_with("op-1")


@pytest.mark.asyncio
async def test_report_upstream_purchase_credits_supply():
    """report_upstream_purchase credits the supply ledger and returns new balance."""
    import tollbooth_authority.server as srv

    supply_ledger = _ledger_with_balance(500)
    settings = _make_settings(dpyc_authority_npub=SAMPLE_NPUB)

    cache = MagicMock(spec=LedgerCache)
    cache.get = AsyncMock(return_value=supply_ledger)
    cache.mark_dirty = MagicMock()
    cache.flush_user = AsyncMock(return_value=True)

    srv._dpyc_sessions["admin-1"] = SAMPLE_NPUB

    with (
        patch.object(srv, "_require_user_id", return_value="admin-1"),
        patch.object(srv, "_get_settings", return_value=settings),
        patch.object(srv, "_get_ledger_cache", return_value=cache),
    ):
        result = await srv.report_upstream_purchase(1000)

    assert result["success"] is True
    assert result["supply_balance_sats"] == 1500
    assert result["credited_sats"] == 1000
    cache.mark_dirty.assert_called_once_with(srv.SUPPLY_USER_ID)
    cache.flush_user.assert_called_once_with(srv.SUPPLY_USER_ID)


@pytest.mark.asyncio
async def test_report_upstream_purchase_negative_rejected():
    """report_upstream_purchase rejects non-positive amounts."""
    import tollbooth_authority.server as srv

    result = await srv.report_upstream_purchase(-100)
    assert result["success"] is False
    assert "positive" in result["error"]

    result = await srv.report_upstream_purchase(0)
    assert result["success"] is False


@pytest.mark.asyncio
async def test_operator_status_shows_supply():
    """Non-Prime Authority: operator_status includes upstream supply fields."""
    import tollbooth_authority.server as srv

    signer = _make_signer()
    settings = _make_settings(
        upstream_authority_address="upstream@example.com",
        upstream_tax_percent=3.0,
    )

    operator_ledger = UserLedger()
    operator_ledger.credit_deposit(1000, "test-seed")
    operator_ledger.debit("spend", 500)
    supply_ledger = UserLedger()
    supply_ledger.credit_deposit(5000, "test-seed")
    supply_ledger.debit("spend", 2000)

    async def fake_get(user_id: str) -> UserLedger:
        if user_id == srv.SUPPLY_USER_ID:
            return supply_ledger
        return operator_ledger

    cache = MagicMock(spec=LedgerCache)
    cache.get = AsyncMock(side_effect=fake_get)

    srv._dpyc_sessions["op-1"] = SAMPLE_NPUB

    with (
        patch.object(srv, "_require_user_id", return_value="op-1"),
        patch.object(srv, "_get_settings", return_value=settings),
        patch.object(srv, "_get_signer", return_value=signer),
        patch.object(srv, "_get_ledger_cache", return_value=cache),
    ):
        result = await srv.operator_status()

    assert result["upstream_supply_sats"] == 3000
    assert result["upstream_supply_consumed_sats"] == 2000


# ---------------------------------------------------------------------------
# DPYC Identity Tools
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_activate_dpyc_deprecated():
    """activate_dpyc returns deprecation message regardless of input."""
    import tollbooth_authority.server as srv

    result = await srv.activate_dpyc(SAMPLE_NPUB)

    assert result["success"] is False
    assert "deprecated" in result["error"].lower()
    assert "register_operator" in result["error"]


@pytest.mark.asyncio
async def test_activate_dpyc_deprecated_invalid_also():
    """activate_dpyc returns same deprecation message even for invalid npub."""
    import tollbooth_authority.server as srv

    result = await srv.activate_dpyc("not-an-npub")

    assert result["success"] is False
    assert "deprecated" in result["error"].lower()
    assert "register_operator" in result["error"]


@pytest.mark.asyncio
async def test_register_operator_invalid_npub():
    """register_operator rejects invalid npub format."""
    import tollbooth_authority.server as srv

    with patch.object(srv, "_require_user_id", return_value="horizon-1"):
        result = await srv.register_operator(npub="not-an-npub")

    assert result["success"] is False
    assert "Invalid npub" in result["error"]
    assert "dpyc-oracle" in result["error"]


@pytest.mark.asyncio
async def test_register_operator_auto_activates_dpyc():
    """register_operator auto-activates DPYC session for the Horizon user."""
    import tollbooth_authority.server as srv

    ledger = UserLedger()
    cache = MagicMock(spec=LedgerCache)
    cache.get = AsyncMock(return_value=ledger)
    cache.mark_dirty = MagicMock()
    cache.flush_user = AsyncMock(return_value=True)

    with (
        patch.object(srv, "_require_user_id", return_value="horizon-1"),
        patch.object(srv, "_get_ledger_cache", return_value=cache),
    ):
        result = await srv.register_operator(npub=SAMPLE_NPUB)

    assert result["success"] is True
    assert result["operator_id"] == SAMPLE_NPUB
    # DPYC session should be auto-activated
    assert srv._dpyc_sessions.get("horizon-1") == SAMPLE_NPUB
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

    with (
        patch.object(srv, "_require_user_id", return_value="horizon-1"),
        patch.object(srv, "_get_ledger_cache", return_value=cache),
    ):
        result = await srv.register_operator(npub=SAMPLE_NPUB)

    assert result["operator_id"] == SAMPLE_NPUB
    assert result["balance_sats"] == 42
    cache.get.assert_called_once_with(SAMPLE_NPUB)
    cache.mark_dirty.assert_called_once_with(SAMPLE_NPUB)
    cache.flush_user.assert_called_once_with(SAMPLE_NPUB)


@pytest.mark.asyncio
async def test_purchase_credits_no_dpyc_returns_error():
    """purchase_credits without DPYC session returns helpful error."""
    import tollbooth_authority.server as srv

    with patch.object(srv, "_require_user_id", return_value="op-1"):
        result = await srv.purchase_credits(1000)

    assert result["success"] is False
    assert "No DPYC identity active" in result["error"]
    assert "register_operator" in result["error"]


# ---------------------------------------------------------------------------
# DPYC Registry enforcement in certify_credits
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_certify_credits_registry_active_member():
    """Registry enforcement: active member succeeds."""
    import tollbooth_authority.server as srv

    signer = _make_signer()
    settings = _make_settings(
        tax_rate_percent=2.0, tax_min_sats=10, certificate_ttl_seconds=600,
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
        patch.object(srv, "_get_signer", return_value=signer),
        patch.object(srv, "_get_ledger_cache", return_value=cache),
        patch.object(srv, "_get_replay_tracker", return_value=replay),
        patch.object(srv, "_get_dpyc_registry", return_value=mock_registry),
    ):
        result = await srv.certify_credits("op-1", 1000)

    assert result["success"] is True
    mock_registry.check_membership.assert_called_once_with("op-1")


@pytest.mark.asyncio
async def test_certify_credits_registry_non_member_rejected():
    """Registry enforcement: non-member rejected with fee rollback."""
    import tollbooth_authority.server as srv

    signer = _make_signer()
    settings = _make_settings(
        tax_rate_percent=2.0, tax_min_sats=10, certificate_ttl_seconds=600,
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
        patch.object(srv, "_get_signer", return_value=signer),
        patch.object(srv, "_get_ledger_cache", return_value=cache),
        patch.object(srv, "_get_replay_tracker", return_value=replay),
        patch.object(srv, "_get_dpyc_registry", return_value=mock_registry),
    ):
        result = await srv.certify_credits("op-1", 1000)

    assert result["success"] is False
    assert "DPYC membership check failed" in result["error"]
    # Fee should be rolled back
    assert ledger.balance_api_sats == 1000


@pytest.mark.asyncio
async def test_certify_credits_registry_unreachable_fails_closed():
    """Registry unreachable: fails closed with rollback."""
    import tollbooth_authority.server as srv

    signer = _make_signer()
    settings = _make_settings(
        tax_rate_percent=2.0, tax_min_sats=10, certificate_ttl_seconds=600,
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
        patch.object(srv, "_get_signer", return_value=signer),
        patch.object(srv, "_get_ledger_cache", return_value=cache),
        patch.object(srv, "_get_replay_tracker", return_value=replay),
        patch.object(srv, "_get_dpyc_registry", return_value=mock_registry),
    ):
        result = await srv.certify_credits("op-1", 1000)

    assert result["success"] is False
    assert "fetch failed" in result["error"]
    assert ledger.balance_api_sats == 1000


@pytest.mark.asyncio
async def test_certify_credits_enforcement_disabled_no_check():
    """Enforcement disabled: no registry check, certification proceeds."""
    import tollbooth_authority.server as srv

    signer = _make_signer()
    settings = _make_settings(
        tax_rate_percent=2.0, tax_min_sats=10, certificate_ttl_seconds=600,
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
        patch.object(srv, "_get_signer", return_value=signer),
        patch.object(srv, "_get_ledger_cache", return_value=cache),
        patch.object(srv, "_get_replay_tracker", return_value=replay),
        patch.object(srv, "_get_dpyc_registry", return_value=None),
    ):
        result = await srv.certify_credits("op-1", 1000)

    assert result["success"] is True


# ---------------------------------------------------------------------------
# authority_npub in JWT claims
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_certify_credits_includes_authority_npub():
    """JWT includes authority_npub when configured."""
    import tollbooth_authority.server as srv

    signer = _make_signer()
    settings = _make_settings(
        tax_rate_percent=2.0, tax_min_sats=10, certificate_ttl_seconds=600,
        dpyc_authority_npub="npub1authority_test",
    )

    ledger = _ledger_with_balance(1000)
    cache = MagicMock(spec=LedgerCache)
    cache.get = AsyncMock(return_value=ledger)
    cache.mark_dirty = MagicMock()
    cache.flush_user = AsyncMock(return_value=True)

    replay = ReplayTracker(ttl_seconds=600)

    with (
        patch.object(srv, "_get_settings", return_value=settings),
        patch.object(srv, "_get_signer", return_value=signer),
        patch.object(srv, "_get_ledger_cache", return_value=cache),
        patch.object(srv, "_get_replay_tracker", return_value=replay),
        patch.object(srv, "_get_dpyc_registry", return_value=None),
    ):
        result = await srv.certify_credits("op-1", 1000)

    assert result["success"] is True
    # Decode the JWT to verify authority_npub claim
    import jwt
    token = result["certificate"]
    claims = jwt.decode(token, options={"verify_signature": False})
    assert claims["authority_npub"] == "npub1authority_test"


@pytest.mark.asyncio
async def test_certify_credits_omits_authority_npub_when_empty():
    """JWT omits authority_npub when not configured."""
    import tollbooth_authority.server as srv

    signer = _make_signer()
    settings = _make_settings(
        tax_rate_percent=2.0, tax_min_sats=10, certificate_ttl_seconds=600,
        dpyc_authority_npub="",
    )

    ledger = _ledger_with_balance(1000)
    cache = MagicMock(spec=LedgerCache)
    cache.get = AsyncMock(return_value=ledger)
    cache.mark_dirty = MagicMock()
    cache.flush_user = AsyncMock(return_value=True)

    replay = ReplayTracker(ttl_seconds=600)

    with (
        patch.object(srv, "_get_settings", return_value=settings),
        patch.object(srv, "_get_signer", return_value=signer),
        patch.object(srv, "_get_ledger_cache", return_value=cache),
        patch.object(srv, "_get_replay_tracker", return_value=replay),
        patch.object(srv, "_get_dpyc_registry", return_value=None),
    ):
        result = await srv.certify_credits("op-1", 1000)

    assert result["success"] is True
    import jwt
    token = result["certificate"]
    claims = jwt.decode(token, options={"verify_signature": False})
    assert "authority_npub" not in claims


@pytest.mark.asyncio
async def test_check_dpyc_membership_found():
    """check_dpyc_membership returns member record when found."""
    import tollbooth_authority.server as srv

    settings = _make_settings()
    mock_registry_cls = MagicMock()
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

    signer = _make_signer()
    settings = _make_settings(
        dpyc_authority_npub="npub1authority_test",
        dpyc_enforce_membership=True,
    )
    ledger = UserLedger()
    ledger.credit_deposit(1000, "test-seed")
    ledger.debit("spend", 500)
    cache = MagicMock(spec=LedgerCache)
    cache.get = AsyncMock(return_value=ledger)

    srv._dpyc_sessions["op-1"] = SAMPLE_NPUB

    with (
        patch.object(srv, "_require_user_id", return_value="op-1"),
        patch.object(srv, "_get_settings", return_value=settings),
        patch.object(srv, "_get_signer", return_value=signer),
        patch.object(srv, "_get_ledger_cache", return_value=cache),
    ):
        result = await srv.operator_status()

    assert result["authority_npub"] == "npub1authority_test"
    assert result["dpyc_registry_enforcement"] is True


# ---------------------------------------------------------------------------
# service_status
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_service_status():
    import tollbooth_authority.server as srv

    result = await srv.service_status()
    assert result["service"] == "tollbooth-authority"
    versions = result["versions"]
    assert "tollbooth_authority" in versions
    assert "python" in versions
    assert "tollbooth_dpyc" in versions
    assert "fastmcp" in versions


# ---------------------------------------------------------------------------
# Deprecated tool shims
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_deprecated_purchase_tax_credits():
    """Deprecated purchase_tax_credits returns migration guidance."""
    import tollbooth_authority.server as srv

    result = await srv.purchase_tax_credits(1000)

    assert result["success"] is False
    assert "purchase_credits" in result["error"]
    assert "v0.2.0" in result["error"]


@pytest.mark.asyncio
async def test_deprecated_check_tax_payment():
    """Deprecated check_tax_payment returns migration guidance."""
    import tollbooth_authority.server as srv

    result = await srv.check_tax_payment("inv-123")

    assert result["success"] is False
    assert "check_payment" in result["error"]
    assert "v0.2.0" in result["error"]


@pytest.mark.asyncio
async def test_deprecated_tax_balance():
    """Deprecated tax_balance returns migration guidance."""
    import tollbooth_authority.server as srv

    result = await srv.tax_balance()

    assert result["success"] is False
    assert "check_balance" in result["error"]
    assert "v0.2.0" in result["error"]


@pytest.mark.asyncio
async def test_deprecated_certify_purchase_delegates():
    """Deprecated certify_purchase delegates to certify_credits."""
    import tollbooth_authority.server as srv

    signer = _make_signer()
    settings = _make_settings(tax_rate_percent=2.0, tax_min_sats=10, certificate_ttl_seconds=600)

    ledger = _ledger_with_balance(1000)
    cache = MagicMock(spec=LedgerCache)
    cache.get = AsyncMock(return_value=ledger)
    cache.mark_dirty = MagicMock()
    cache.flush_user = AsyncMock(return_value=True)

    replay = ReplayTracker(ttl_seconds=600)

    with (
        patch.object(srv, "_get_settings", return_value=settings),
        patch.object(srv, "_get_signer", return_value=signer),
        patch.object(srv, "_get_ledger_cache", return_value=cache),
        patch.object(srv, "_get_replay_tracker", return_value=replay),
    ):
        result = await srv.certify_purchase("op-1", 1000)

    assert result["success"] is True
    assert result["fee_sats"] == 20
    assert result["tax_paid_sats"] == 20
    assert result["net_sats"] == 980


# ---------------------------------------------------------------------------
# report_upstream_purchase — admin auth (H-2)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_report_upstream_purchase_non_admin_rejected():
    """report_upstream_purchase rejects non-admin callers."""
    import tollbooth_authority.server as srv

    settings = _make_settings(dpyc_authority_npub=SAMPLE_NPUB)
    non_admin_npub = "npub1qqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqs3mlyh"

    srv._dpyc_sessions["caller-1"] = non_admin_npub

    with (
        patch.object(srv, "_require_user_id", return_value="caller-1"),
        patch.object(srv, "_get_settings", return_value=settings),
    ):
        result = await srv.report_upstream_purchase(1000)

    assert result["success"] is False
    assert "Unauthorized" in result["error"]


@pytest.mark.asyncio
async def test_report_upstream_purchase_no_admin_configured():
    """report_upstream_purchase fails when DPYC_AUTHORITY_NPUB is not set."""
    import tollbooth_authority.server as srv

    settings = _make_settings(dpyc_authority_npub="")

    with patch.object(srv, "_get_settings", return_value=settings):
        result = await srv.report_upstream_purchase(1000)

    assert result["success"] is False
    assert "not configured" in result["error"]


@pytest.mark.asyncio
async def test_report_upstream_purchase_no_dpyc_session():
    """report_upstream_purchase fails when no DPYC session is active."""
    import tollbooth_authority.server as srv

    settings = _make_settings(dpyc_authority_npub=SAMPLE_NPUB)

    with (
        patch.object(srv, "_require_user_id", return_value="caller-no-session"),
        patch.object(srv, "_get_settings", return_value=settings),
    ):
        result = await srv.report_upstream_purchase(1000)

    assert result["success"] is False
    assert "No DPYC identity active" in result["error"]
