"""Tests for anti-replay tracker."""

from __future__ import annotations

import time
from unittest.mock import patch

from tollbooth_authority.replay import ReplayTracker


def test_accept_new_jti():
    tracker = ReplayTracker(ttl_seconds=60)
    assert tracker.check_and_record("jti-1") is True
    assert tracker.size == 1


def test_reject_duplicate_jti():
    tracker = ReplayTracker(ttl_seconds=60)
    assert tracker.check_and_record("jti-1") is True
    assert tracker.check_and_record("jti-1") is False
    assert tracker.size == 1


def test_expire_old_entries():
    tracker = ReplayTracker(ttl_seconds=1)
    tracker.check_and_record("jti-1")

    # Simulate time passing beyond TTL
    with patch("tollbooth_authority.replay.time.monotonic", return_value=time.monotonic() + 2):
        assert tracker.check_and_record("jti-2") is True
        # jti-1 should have been pruned
        assert tracker.size == 1
        # jti-1 should now be accepted again (expired from tracker)
        assert tracker.check_and_record("jti-1") is True


def test_multiple_distinct_jtis():
    tracker = ReplayTracker(ttl_seconds=60)
    for i in range(100):
        assert tracker.check_and_record(f"jti-{i}") is True
    assert tracker.size == 100


def test_empty_tracker_size():
    tracker = ReplayTracker()
    assert tracker.size == 0
