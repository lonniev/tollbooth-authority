"""Tests for the SVG operator account statement infographic renderer."""

from __future__ import annotations

import re

import pytest

from tollbooth_authority.infographic import render_operator_infographic


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _sample_data(**overrides) -> dict:
    """Build a sample account_statement response."""
    data = {
        "success": True,
        "generated_at": "2026-03-01T12:00:00.000000+00:00",
        "account_summary": {
            "balance_sats": 5000,
            "total_deposited_sats": 10000,
            "total_fees_paid_sats": 200,
            "total_certified_sats": 8000,
        },
        "active_tranches": [
            {
                "granted_at": "2026-02-28T10:00:00+00:00",
                "original_sats": 10000,
                "remaining_sats": 5000,
                "invoice_id": "seed_balance_v1",
            }
        ],
        "fee_schedule": "Set via pricing model",
    }
    data.update(overrides)
    return data


def _height(svg: str) -> int:
    m = re.search(r'viewBox="0 0 \d+ (\d+)"', svg)
    assert m, "viewBox not found"
    return int(m.group(1))


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestRenderOperatorInfographic:
    def test_returns_valid_svg(self) -> None:
        """Output is a well-formed SVG string."""
        svg = render_operator_infographic(_sample_data())
        assert svg.startswith("<svg")
        assert svg.endswith("</svg>")
        assert 'xmlns="http://www.w3.org/2000/svg"' in svg

    def test_header_branding(self) -> None:
        """Header shows Authority branding."""
        svg = render_operator_infographic(_sample_data())
        assert "Tollbooth Authority" in svg
        assert "Operator Account" in svg

    def test_balance_displayed(self) -> None:
        """Hero balance appears in the output."""
        svg = render_operator_infographic(_sample_data())
        assert "5,000" in svg

    def test_metrics_displayed(self) -> None:
        """Metric cards show deposited, fees paid, certified."""
        svg = render_operator_infographic(_sample_data())
        assert "10,000" in svg     # deposited
        assert "DEPOSITED" in svg
        assert "FEES PAID" in svg
        assert "CERTIFIED" in svg

    def test_fee_schedule_displayed(self) -> None:
        """Fee schedule card shows the fee schedule string."""
        svg = render_operator_infographic(_sample_data())
        assert "FEE SCHEDULE" in svg
        assert "Set via pricing model" in svg

    def test_tranche_rendered(self) -> None:
        """Active tranche row appears."""
        svg = render_operator_infographic(_sample_data())
        assert "Seed (v1)" in svg
        assert "2026-02-28" in svg

    def test_footer_branding(self) -> None:
        """Footer includes DPYC branding."""
        svg = render_operator_infographic(_sample_data())
        assert "DPYC" in svg
        assert "Tollbooth Protocol" in svg

    def test_zero_balance(self) -> None:
        """Renders without error when balance is zero."""
        data = _sample_data()
        data["account_summary"]["balance_sats"] = 0
        svg = render_operator_infographic(data)
        assert svg.startswith("<svg")

    def test_empty_tranches(self) -> None:
        """Handles no active tranches gracefully."""
        svg = render_operator_infographic(_sample_data(active_tranches=[]))
        assert "No active tranches" in svg

    def test_multiple_tranches(self) -> None:
        """Multiple tranche rows render correctly."""
        tranches = [
            {
                "granted_at": "2026-02-20T10:00:00+00:00",
                "original_sats": 5000,
                "remaining_sats": 2000,
                "invoice_id": "inv-abc",
            },
            {
                "granted_at": "2026-02-28T10:00:00+00:00",
                "original_sats": 10000,
                "remaining_sats": 9000,
                "invoice_id": "seed_balance_v1",
            },
        ]
        svg = render_operator_infographic(_sample_data(active_tranches=tranches))
        assert "inv-abc" in svg
        assert "Seed (v1)" in svg

    def test_xml_escaping(self) -> None:
        """Special characters in invoice IDs are XML-escaped."""
        tranches = [{
            "granted_at": "2026-03-01T00:00:00+00:00",
            "original_sats": 100,
            "remaining_sats": 100,
            "invoice_id": "inv<test>",
        }]
        svg = render_operator_infographic(_sample_data(active_tranches=tranches))
        assert "&lt;" in svg
        assert "&gt;" in svg

    def test_dynamic_height(self) -> None:
        """SVG height adjusts with more tranche rows."""
        svg_short = render_operator_infographic(_sample_data())
        many_tranches = [
            {
                "granted_at": f"2026-02-{20+i}T10:00:00+00:00",
                "original_sats": 1000,
                "remaining_sats": 500,
                "invoice_id": f"inv-{i:03d}",
            }
            for i in range(10)
        ]
        svg_tall = render_operator_infographic(
            _sample_data(active_tranches=many_tranches)
        )
        assert _height(svg_tall) > _height(svg_short)

    def test_custom_fee_schedule(self) -> None:
        """Custom fee schedule string is displayed."""
        svg = render_operator_infographic(
            _sample_data(fee_schedule="Custom pricing: 5% rate, 25 sat floor")
        )
        assert "Custom pricing: 5% rate, 25 sat floor" in svg

    def test_timestamp_in_header(self) -> None:
        """Timestamp from generated_at appears in header."""
        svg = render_operator_infographic(_sample_data())
        assert "2026-03-01 12:00:00 UTC" in svg
