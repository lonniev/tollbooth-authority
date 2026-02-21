"""Tests for certificate claims builder."""

from __future__ import annotations

import time

from tollbooth_authority.certificate import create_certificate_claims


def test_fields_populated():
    claims = create_certificate_claims("op-1", 1000, 20, ttl_seconds=300)
    assert claims["sub"] == "op-1"
    assert claims["amount_sats"] == 1000
    assert claims["tax_paid_sats"] == 20
    assert claims["net_sats"] == 980
    assert "jti" in claims
    assert "iat" in claims
    assert "exp" in claims
    assert "dpyc_protocol" in claims


def test_dpyc_protocol_value():
    claims = create_certificate_claims("op-1", 1000, 20)
    assert claims["dpyc_protocol"] == "dpyp-01-base-certificate"


def test_unique_jti():
    a = create_certificate_claims("op-1", 100, 2)
    b = create_certificate_claims("op-1", 100, 2)
    assert a["jti"] != b["jti"]


def test_net_sats_math():
    claims = create_certificate_claims("op-1", 5000, 100)
    assert claims["net_sats"] == 4900


def test_expiry_is_in_future():
    claims = create_certificate_claims("op-1", 100, 2, ttl_seconds=600)
    assert claims["exp"] > int(time.time())
    assert claims["exp"] <= int(time.time()) + 601


def test_iat_is_now():
    before = int(time.time())
    claims = create_certificate_claims("op-1", 100, 2)
    after = int(time.time())
    assert before <= claims["iat"] <= after
