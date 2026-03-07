"""Tests for OnboardingState and onboarding templates."""

from __future__ import annotations

import time

import pytest

from tollbooth_authority.onboarding import (
    OnboardingState,
    AUTHORITY_CLAIM_TEMPLATE,
    AUTHORITY_APPROVAL_TEMPLATE,
    ONBOARDING_TEMPLATES,
)

CANDIDATE = "npub1l94pd4qu4eszrl6ek032ftcnsu3tt9a7xvq2zp7eaxeklp6mrpzssmq8pf"


def test_templates_registered():
    """Both templates are in the ONBOARDING_TEMPLATES dict."""
    assert "authority_claim" in ONBOARDING_TEMPLATES
    assert "authority_approval" in ONBOARDING_TEMPLATES
    assert ONBOARDING_TEMPLATES["authority_claim"] is AUTHORITY_CLAIM_TEMPLATE
    assert ONBOARDING_TEMPLATES["authority_approval"] is AUTHORITY_APPROVAL_TEMPLATE


def test_create_claim_challenge():
    state = OnboardingState()
    ch = state.start_claim(CANDIDATE)
    assert ch.candidate_npub == CANDIDATE
    assert ch.phase == "claim"
    assert ch.prime_npub is None
    assert not ch.expired


def test_duplicate_challenge_rejected():
    state = OnboardingState()
    state.start_claim(CANDIDATE)
    with pytest.raises(ValueError, match="already in progress"):
        state.start_claim(CANDIDATE)


def test_expired_challenge_pruned():
    state = OnboardingState(ttl_seconds=0)
    state.start_claim(CANDIDATE)
    time.sleep(0.01)  # ensure expiry
    # After pruning, get() returns None and a new claim can start
    assert state.get() is None
    ch = state.start_claim(CANDIDATE)
    assert ch.candidate_npub == CANDIDATE


def test_promote_to_approval():
    state = OnboardingState()
    state.start_claim(CANDIDATE)
    prime = "npub1primexxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
    ch = state.promote_to_approval(prime)
    assert ch.phase == "approval"
    assert ch.prime_npub == prime
    # TTL reset
    assert not ch.expired


def test_promote_wrong_phase():
    state = OnboardingState()
    state.start_claim(CANDIDATE)
    state.promote_to_approval("npub1prime...")
    with pytest.raises(ValueError, match="expected 'claim'"):
        state.promote_to_approval("npub1prime...")


def test_promote_no_active():
    state = OnboardingState()
    with pytest.raises(ValueError, match="No active"):
        state.promote_to_approval("npub1prime...")


def test_complete_removes_challenge():
    state = OnboardingState()
    state.start_claim(CANDIDATE)
    state.complete()
    assert state.get() is None
    # Can start a new one after complete
    ch = state.start_claim(CANDIDATE)
    assert ch is not None
