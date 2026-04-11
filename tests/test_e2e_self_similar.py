"""E2E tests proving the self-similar commerce chain pattern.

Tests use real AuthorityNostrSigner + real ToolPricing.
certify_credits is now a paid_tool — the runtime handles billing.
We mock the runtime's internals and call the unwrapped function.
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
    resolver = AsyncMock()
    resolver.get_tool_pricing = AsyncMock(
        return_value=ToolPricing(rate_percent=2.0, rate_param="amount_sats", min_cost=10)
    )
    return resolver


@pytest.fixture(autouse=True)
def _clean_state():
    reset_jti_store()
    yield
    reset_jti_store()


async def _call_certify_credits(npub: str, amount_sats: int) -> dict:
    """Call certify_credits directly, bypassing paid_tool wrapper.

    The paid_tool wrapper handles billing via debit_or_deny.
    This test calls the unwrapped __wrapped__ function to test the
    certification logic itself (signing, fees, upstream).
    """
    import tollbooth_authority.server as srv

    fn = srv.certify_credits
    # The paid_tool decorator wraps the function. Access the inner function.
    while hasattr(fn, "__wrapped__"):
        fn = fn.__wrapped__
    return await fn(npub=npub, amount_sats=amount_sats)


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
        cache = MagicMock(spec=LedgerCache)
        cache.flush_user = AsyncMock(return_value=True)
        replay = ReplayTracker(ttl_seconds=600)

        with (
            patch.object(srv, "_get_settings", return_value=settings),
            patch.object(srv, "_get_nostr_signer", return_value=nostr_signer),
            patch.object(srv, "_get_replay_tracker", return_value=replay),
            patch.object(srv.runtime, "pricing_resolver", new_callable=AsyncMock, return_value=_mock_pricing_resolver()),
            patch.object(srv.runtime, "ledger_cache", new_callable=AsyncMock, return_value=cache),
        ):
            result = await _call_certify_credits("op-1", 1000)

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
        cache = MagicMock(spec=LedgerCache)
        cache.flush_user = AsyncMock(return_value=True)
        replay = ReplayTracker(ttl_seconds=600)

        with (
            patch.object(srv, "_get_settings", return_value=settings),
            patch.object(srv, "_get_nostr_signer", return_value=nostr_signer),
            patch.object(srv, "_get_replay_tracker", return_value=replay),
            patch.object(srv.runtime, "pricing_resolver", new_callable=AsyncMock, return_value=_mock_pricing_resolver()),
            patch.object(srv.runtime, "ledger_cache", new_callable=AsyncMock, return_value=cache),
        ):
            result = await _call_certify_credits("op-1", 1000)

        assert "fee_sats" in result
        assert "tax_paid_sats" not in result

    @pytest.mark.asyncio
    async def test_net_sats_equals_amount_minus_fee(self):
        """net_sats = amount_sats - fee_sats."""
        import tollbooth_authority.server as srv

        nostr_signer = _make_nostr_signer()
        settings = _make_settings()
        cache = MagicMock(spec=LedgerCache)
        cache.flush_user = AsyncMock(return_value=True)
        replay = ReplayTracker(ttl_seconds=600)

        for amount in [100, 500, 1000, 5000]:
            with (
                patch.object(srv, "_get_settings", return_value=settings),
                patch.object(srv, "_get_nostr_signer", return_value=nostr_signer),
                patch.object(srv, "_get_replay_tracker", return_value=replay),
                patch.object(srv.runtime, "pricing_resolver", new_callable=AsyncMock, return_value=_mock_pricing_resolver()),
                patch.object(srv.runtime, "ledger_cache", new_callable=AsyncMock, return_value=cache),
            ):
                result = await _call_certify_credits("op-1", amount)
            assert result["net_sats"] == amount - result["fee_sats"]

    @pytest.mark.asyncio
    async def test_valid_schnorr_certificate_kind_30079(self):
        """Certificate is a valid Schnorr-signed kind 30079 Nostr event."""
        import tollbooth_authority.server as srv

        nostr_signer = _make_nostr_signer()
        settings = _make_settings()
        cache = MagicMock(spec=LedgerCache)
        cache.flush_user = AsyncMock(return_value=True)
        replay = ReplayTracker(ttl_seconds=600)

        with (
            patch.object(srv, "_get_settings", return_value=settings),
            patch.object(srv, "_get_nostr_signer", return_value=nostr_signer),
            patch.object(srv, "_get_replay_tracker", return_value=replay),
            patch.object(srv.runtime, "pricing_resolver", new_callable=AsyncMock, return_value=_mock_pricing_resolver()),
            patch.object(srv.runtime, "ledger_cache", new_callable=AsyncMock, return_value=cache),
        ):
            result = await _call_certify_credits("op-1", 1000)

        event_dict = json.loads(result["certificate"])
        assert event_dict["kind"] == NOSTR_CERT_KIND

        event = Event.from_dict(event_dict)
        assert event.verify() is True

        content = json.loads(event_dict["content"])
        assert "fee_sats" in content
        assert "tax_paid_sats" not in content
        assert content["dpyc_protocol"] == "dpyp-01-base-certificate"

        claims = verify_certificate_auto(
            result["certificate"], authority_npub=nostr_signer.npub
        )
        assert claims["fee_sats"] == result["fee_sats"]
        assert claims["net_sats"] == result["net_sats"]

    @pytest.mark.asyncio
    async def test_non_prime_upstream_auto_certify(self):
        """Non-Prime path: upstream AuthorityCertifier is called; failure raises."""
        import tollbooth_authority.server as srv
        from tollbooth.authority_client import AuthorityCertifyError

        nostr_signer = _make_nostr_signer()
        settings = _make_settings()
        settings.upstream_authority_address = "https://upstream.example.com"
        cache = MagicMock(spec=LedgerCache)
        cache.flush_user = AsyncMock(return_value=True)
        replay = ReplayTracker(ttl_seconds=600)

        with (
            patch.object(srv, "_get_settings", return_value=settings),
            patch.object(srv, "_get_nostr_signer", return_value=nostr_signer),
            patch.object(srv, "_get_replay_tracker", return_value=replay),
            patch.object(srv.runtime, "pricing_resolver", new_callable=AsyncMock, return_value=_mock_pricing_resolver()),
            patch.object(srv.runtime, "ledger_cache", new_callable=AsyncMock, return_value=cache),
            patch.object(srv, "_get_authority_npub", new_callable=AsyncMock, return_value=nostr_signer.npub),
            patch("tollbooth.authority_client.AuthorityCertifier") as MockCertifierClass,
        ):
            MockCertifierClass.return_value.certify_credits = AsyncMock(
                side_effect=AuthorityCertifyError("insufficient balance")
            )
            # certify_credits now raises RuntimeError on upstream failure
            # (paid_tool wrapper catches and rolls back)
            with pytest.raises(RuntimeError, match="Upstream certification failed"):
                await _call_certify_credits("op-1", 1000)

    @pytest.mark.asyncio
    async def test_anti_replay_jti(self):
        """Each certification gets a unique JTI."""
        import tollbooth_authority.server as srv

        nostr_signer = _make_nostr_signer()
        settings = _make_settings()
        cache = MagicMock(spec=LedgerCache)
        cache.flush_user = AsyncMock(return_value=True)
        replay = ReplayTracker(ttl_seconds=600)

        with (
            patch.object(srv, "_get_settings", return_value=settings),
            patch.object(srv, "_get_nostr_signer", return_value=nostr_signer),
            patch.object(srv, "_get_replay_tracker", return_value=replay),
            patch.object(srv.runtime, "pricing_resolver", new_callable=AsyncMock, return_value=_mock_pricing_resolver()),
            patch.object(srv.runtime, "ledger_cache", new_callable=AsyncMock, return_value=cache),
        ):
            r1 = await _call_certify_credits("op-1", 500)
            r2 = await _call_certify_credits("op-1", 500)

        assert r1["jti"] != r2["jti"]

        c1 = verify_certificate_auto(r1["certificate"], authority_npub=nostr_signer.npub)
        c2 = verify_certificate_auto(r2["certificate"], authority_npub=nostr_signer.npub)
        assert c1["jti"] != c2["jti"]
