"""Tests for Nostr event certificate signing (Schnorr/BIP-340)."""

from __future__ import annotations

import json
import time

import pytest
from pynostr.event import Event  # type: ignore[import-untyped]
from pynostr.key import PrivateKey  # type: ignore[import-untyped]

from tollbooth_authority.nostr_signing import AuthorityNostrSigner, NOSTR_CERT_KIND


@pytest.fixture
def nsec() -> str:
    """Generate a fresh Nostr private key and return its nsec."""
    return PrivateKey().bech32()


@pytest.fixture
def nostr_signer(nsec: str) -> AuthorityNostrSigner:
    return AuthorityNostrSigner(nsec)


def test_signer_initialization(nostr_signer: AuthorityNostrSigner):
    """Signer loads nsec and exposes npub and hex pubkey."""
    assert nostr_signer.npub.startswith("npub1")
    assert len(nostr_signer.pubkey_hex) == 64  # 32 bytes hex


def test_sign_certificate_event_produces_valid_json(nostr_signer: AuthorityNostrSigner):
    """sign_certificate_event returns valid JSON that parses as a Nostr event."""
    claims = {
        "sub": "npub1operator",
        "amount_sats": 1000,
        "tax_paid_sats": 20,
        "net_sats": 980,
        "dpyc_protocol": "dpyp-01-base-certificate",
    }
    event_json = nostr_signer.sign_certificate_event(
        claims=claims,
        jti="test-jti-1",
        operator_npub="npub1operator",
        expiration=int(time.time()) + 600,
    )
    event_dict = json.loads(event_json)
    assert "id" in event_dict
    assert "sig" in event_dict
    assert "pubkey" in event_dict
    assert event_dict["kind"] == NOSTR_CERT_KIND


def test_event_has_correct_tags(nostr_signer: AuthorityNostrSigner):
    """Signed event includes all required tags."""
    claims = {
        "sub": "npub1operator",
        "amount_sats": 500,
        "tax_paid_sats": 10,
        "net_sats": 490,
        "dpyc_protocol": "dpyp-01-base-certificate",
    }
    expiration = int(time.time()) + 600
    event_json = nostr_signer.sign_certificate_event(
        claims=claims,
        jti="test-jti-tags",
        operator_npub="npub1operator",
        expiration=expiration,
    )
    event_dict = json.loads(event_json)
    tags = event_dict["tags"]

    # Find tag values by key
    tag_map: dict[str, str] = {}
    for tag in tags:
        if len(tag) >= 2:
            tag_map[tag[0]] = tag[1]

    assert tag_map["d"] == "test-jti-tags"
    assert tag_map["t"] == "tollbooth-cert"
    assert tag_map["L"] == "dpyc.tollbooth"
    assert tag_map["expiration"] == str(expiration)
    assert "p" in tag_map  # operator pubkey


def test_event_content_contains_claims(nostr_signer: AuthorityNostrSigner):
    """Event content is JSON with the certificate claims."""
    claims = {
        "sub": "npub1operator",
        "amount_sats": 1000,
        "tax_paid_sats": 20,
        "net_sats": 980,
        "dpyc_protocol": "dpyp-01-base-certificate",
    }
    event_json = nostr_signer.sign_certificate_event(
        claims=claims,
        jti="test-jti-content",
        operator_npub="npub1operator",
        expiration=int(time.time()) + 600,
    )
    event_dict = json.loads(event_json)
    content = json.loads(event_dict["content"])
    assert content["sub"] == "npub1operator"
    assert content["amount_sats"] == 1000
    assert content["net_sats"] == 980
    assert content["dpyc_protocol"] == "dpyp-01-base-certificate"


def test_event_verifies_with_pynostr(nostr_signer: AuthorityNostrSigner):
    """The signed event passes pynostr's Schnorr verification."""
    claims = {
        "sub": "npub1operator",
        "amount_sats": 1000,
        "tax_paid_sats": 20,
        "net_sats": 980,
        "dpyc_protocol": "dpyp-01-base-certificate",
    }
    event_json = nostr_signer.sign_certificate_event(
        claims=claims,
        jti="test-jti-verify",
        operator_npub="npub1operator",
        expiration=int(time.time()) + 600,
    )
    event_dict = json.loads(event_json)
    event = Event.from_dict(event_dict)
    assert event.verify() is True


def test_event_pubkey_matches_signer(nostr_signer: AuthorityNostrSigner):
    """Event pubkey matches the signer's public key."""
    claims = {
        "sub": "op-1",
        "amount_sats": 100,
        "tax_paid_sats": 2,
        "net_sats": 98,
        "dpyc_protocol": "dpyp-01-base-certificate",
    }
    event_json = nostr_signer.sign_certificate_event(
        claims=claims,
        jti="test-jti-pubkey",
        operator_npub="npub1operator",
        expiration=int(time.time()) + 600,
    )
    event_dict = json.loads(event_json)
    assert event_dict["pubkey"] == nostr_signer.pubkey_hex


def test_different_jti_produces_different_events(nostr_signer: AuthorityNostrSigner):
    """Two calls with different JTIs produce different event IDs."""
    claims = {
        "sub": "op-1",
        "amount_sats": 100,
        "tax_paid_sats": 2,
        "net_sats": 98,
        "dpyc_protocol": "dpyp-01-base-certificate",
    }
    e1 = json.loads(nostr_signer.sign_certificate_event(
        claims=claims, jti="jti-a",
        operator_npub="op-1", expiration=int(time.time()) + 600,
    ))
    e2 = json.loads(nostr_signer.sign_certificate_event(
        claims=claims, jti="jti-b",
        operator_npub="op-1", expiration=int(time.time()) + 600,
    ))
    assert e1["id"] != e2["id"]
