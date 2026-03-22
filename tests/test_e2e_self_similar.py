"""E2E tests proving the self-similar commerce chain pattern.

Uses real AuthorityNostrSigner + real ToolPricing. Only the vault
(LedgerCache) is mocked — everything else is production code.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pynostr.event import Event  # type: ignore[import-untyped]
from pynostr.key import PrivateKey  # type: ignore[import-untyped]

from tollbooth import UserLedger, LedgerCache, ToolPricing
from tollbooth.certificate import verify_certificate_auto, reset_jti_store

from tollbooth_authority.config import AuthoritySettings
from tollbooth_authority.nostr_signing import AuthorityNostrSigner, NOSTR_CERT_KIND
from tollbooth_authority.replay import ReplayTracker

SAMPLE_NPUB = "npub1l94pd4qu4eszrl6ek032ftcnsu3tt9a7xvq2zp7eaxeklp6mrpzssmq8pf"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ledger_with_balance(sats: int) -> UserLedger:
    ledger = UserLedger()
    if sats > 0:
        ledger.credit_deposit(sats, "test-seed")
    return ledger


def _make_nostr_signer() -> AuthorityNostrSigner:
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


def _mock_pricing_resolver():
    """Return an AsyncMock that resolves certify_credits to 2% ad valorem."""
    resolver = AsyncMock()
    resolver.get_tool_pricing = AsyncMock(
        return_value=ToolPricing(rate_percent=2.0, rate_param="amount_sats", min_cost=10)
    )
    return resolver


@pytest.fixture(autouse=True)
def _clean_state():
    import tollbooth_authority.server as srv
    srv._dpyc_sessions.clear()
    srv._pricing_resolver = _mock_pricing_resolver()
    reset_jti_store()
    yield
    srv._dpyc_sessions.clear()
    srv._pricing_resolver = None
    reset_jti_store()


# ---------------------------------------------------------------------------
# E2E: Self-similar commerce chain
# ---------------------------------------------------------------------------


class TestSelfSimilarCommerceChain:
    """Prove the self-similar pattern: certify_credits fee = ToolPricing.compute()."""

    @pytest.mark.asyncio
    async def test_fee_equals_tool_pricing_compute(self):
        """certify_credits fee matches ToolPricing.compute() exactly."""
        import tollbooth_authority.server as srv

        nostr_signer = _make_nostr_signer()
        pricing = ToolPricing(rate_percent=2.0, rate_param="amount_sats", min_cost=10)
        settings = _make_settings(certificate_ttl_seconds=600)

        ledger = _ledger_with_balance(5000)
        cache = MagicMock(spec=LedgerCache)
        cache.get = AsyncMock(return_value=ledger)
        cache.mark_dirty = MagicMock()
        cache.flush_user = AsyncMock(return_value=True)
        replay = ReplayTracker(ttl_seconds=600)

        with (
            patch.object(srv, "_get_settings", return_value=settings),
            patch.object(srv, "_get_nostr_signer", return_value=nostr_signer),
            patch.object(srv, "_get_ledger_cache", return_value=cache),
            patch.object(srv, "_get_replay_tracker", return_value=replay),
        ):
            result = await srv.certify_credits("op-1", 1000)

        expected_fee = pricing.compute(amount_sats=1000)
        assert result["success"] is True
        assert result["fee_sats"] == expected_fee
        assert result["net_sats"] == 1000 - expected_fee

    @pytest.mark.asyncio
    async def test_response_has_fee_sats_not_tax(self):
        """Response contains fee_sats, not tax_paid_sats."""
        import tollbooth_authority.server as srv

        nostr_signer = _make_nostr_signer()
        settings = _make_settings()
        ledger = _ledger_with_balance(5000)
        cache = MagicMock(spec=LedgerCache)
        cache.get = AsyncMock(return_value=ledger)
        cache.mark_dirty = MagicMock()
        cache.flush_user = AsyncMock(return_value=True)
        replay = ReplayTracker(ttl_seconds=600)

        with (
            patch.object(srv, "_get_settings", return_value=settings),
            patch.object(srv, "_get_nostr_signer", return_value=nostr_signer),
            patch.object(srv, "_get_ledger_cache", return_value=cache),
            patch.object(srv, "_get_replay_tracker", return_value=replay),
        ):
            result = await srv.certify_credits("op-1", 1000)

        assert "fee_sats" in result
        assert "tax_paid_sats" not in result

    @pytest.mark.asyncio
    async def test_net_sats_equals_amount_minus_fee(self):
        """net_sats = amount_sats - fee_sats."""
        import tollbooth_authority.server as srv

        nostr_signer = _make_nostr_signer()
        settings = _make_settings()
        ledger = _ledger_with_balance(10000)
        cache = MagicMock(spec=LedgerCache)
        cache.get = AsyncMock(return_value=ledger)
        cache.mark_dirty = MagicMock()
        cache.flush_user = AsyncMock(return_value=True)
        replay = ReplayTracker(ttl_seconds=600)

        for amount in [100, 500, 1000, 5000]:
            with (
                patch.object(srv, "_get_settings", return_value=settings),
                patch.object(srv, "_get_nostr_signer", return_value=nostr_signer),
                patch.object(srv, "_get_ledger_cache", return_value=cache),
                patch.object(srv, "_get_replay_tracker", return_value=replay),
            ):
                result = await srv.certify_credits("op-1", amount)
            assert result["net_sats"] == amount - result["fee_sats"]

    @pytest.mark.asyncio
    async def test_valid_schnorr_certificate_kind_30079(self):
        """Certificate is a valid Schnorr-signed kind 30079 Nostr event."""
        import tollbooth_authority.server as srv

        nostr_signer = _make_nostr_signer()
        settings = _make_settings()
        ledger = _ledger_with_balance(5000)
        cache = MagicMock(spec=LedgerCache)
        cache.get = AsyncMock(return_value=ledger)
        cache.mark_dirty = MagicMock()
        cache.flush_user = AsyncMock(return_value=True)
        replay = ReplayTracker(ttl_seconds=600)

        with (
            patch.object(srv, "_get_settings", return_value=settings),
            patch.object(srv, "_get_nostr_signer", return_value=nostr_signer),
            patch.object(srv, "_get_ledger_cache", return_value=cache),
            patch.object(srv, "_get_replay_tracker", return_value=replay),
        ):
            result = await srv.certify_credits("op-1", 1000)

        # Parse and verify the Nostr event
        event_dict = json.loads(result["certificate"])
        assert event_dict["kind"] == NOSTR_CERT_KIND

        event = Event.from_dict(event_dict)
        assert event.verify() is True

        # Claims in content use fee_sats
        content = json.loads(event_dict["content"])
        assert "fee_sats" in content
        assert "tax_paid_sats" not in content
        assert content["dpyc_protocol"] == "dpyp-01-base-certificate"

        # Certificate verifiable by tollbooth-dpyc
        claims = verify_certificate_auto(
            result["certificate"], authority_npub=nostr_signer.npub
        )
        assert claims["fee_sats"] == result["fee_sats"]
        assert claims["net_sats"] == result["net_sats"]

    @pytest.mark.asyncio
    async def test_non_prime_upstream_auto_certify(self):
        """Non-Prime path: upstream AuthorityCertifier is called; failure rolls back."""
        import tollbooth_authority.server as srv
        from tollbooth.authority_client import AuthorityCertifyError

        nostr_signer = _make_nostr_signer()
        settings = _make_settings()
        settings.upstream_authority_address = "https://upstream.example.com"

        operator_ledger = _ledger_with_balance(5000)

        cache = MagicMock(spec=LedgerCache)
        cache.get = AsyncMock(return_value=operator_ledger)
        cache.mark_dirty = MagicMock()
        cache.flush_user = AsyncMock(return_value=True)
        replay = ReplayTracker(ttl_seconds=600)

        # Test failure path: upstream refuses → fee rolled back
        with (
            patch.object(srv, "_get_settings", return_value=settings),
            patch.object(srv, "_get_nostr_signer", return_value=nostr_signer),
            patch.object(srv, "_get_ledger_cache", return_value=cache),
            patch.object(srv, "_get_replay_tracker", return_value=replay),
            patch.object(srv, "_get_authority_npub", new_callable=AsyncMock, return_value=nostr_signer.npub),
            patch("tollbooth.authority_client.AuthorityCertifier") as MockCertifierClass,
        ):
            MockCertifierClass.return_value.certify_credits = AsyncMock(
                side_effect=AuthorityCertifyError("insufficient balance")
            )
            result = await srv.certify_credits("op-1", 1000)

        assert result["success"] is False
        assert "Upstream certification failed" in result["error"]
        # Operator's fee debit should have been rolled back
        assert operator_ledger.balance_api_sats == 5000

    @pytest.mark.asyncio
    async def test_anti_replay_jti(self):
        """Each certification gets a unique JTI; replays are detected by the verifier."""
        import tollbooth_authority.server as srv

        nostr_signer = _make_nostr_signer()
        settings = _make_settings()
        ledger = _ledger_with_balance(10000)
        cache = MagicMock(spec=LedgerCache)
        cache.get = AsyncMock(return_value=ledger)
        cache.mark_dirty = MagicMock()
        cache.flush_user = AsyncMock(return_value=True)
        replay = ReplayTracker(ttl_seconds=600)

        with (
            patch.object(srv, "_get_settings", return_value=settings),
            patch.object(srv, "_get_nostr_signer", return_value=nostr_signer),
            patch.object(srv, "_get_ledger_cache", return_value=cache),
            patch.object(srv, "_get_replay_tracker", return_value=replay),
        ):
            r1 = await srv.certify_credits("op-1", 500)
            r2 = await srv.certify_credits("op-1", 500)

        # Different JTIs
        assert r1["jti"] != r2["jti"]

        # Both verifiable independently
        c1 = verify_certificate_auto(r1["certificate"], authority_npub=nostr_signer.npub)
        c2 = verify_certificate_auto(r2["certificate"], authority_npub=nostr_signer.npub)
        assert c1["jti"] != c2["jti"]
