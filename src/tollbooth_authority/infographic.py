"""SVG infographic generator for Authority operator account statements.

Produces a dark-themed, Bitcoin-orange-accented SVG infographic from
the structured data returned by ``account_statement``.  Pure Python
— no external dependencies (matplotlib, Pillow, etc.).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from xml.sax.saxutils import escape


# ---------------------------------------------------------------------------
# Colour palette
# ---------------------------------------------------------------------------

BG_DARK = "#0f1419"
BG_CARD = "#1a2332"
BG_CARD_ALT = "#1e2a3a"
ACCENT_ORANGE = "#f7931a"
ACCENT_BLUE = "#4ecdc4"
ACCENT_GREEN = "#2ecc71"
ACCENT_RED = "#e74c3c"
TEXT_WHITE = "#ecf0f1"
TEXT_GRAY = "#8899aa"
TEXT_DIM = "#556677"
BORDER = "#2a3a4a"

# ---------------------------------------------------------------------------
# Layout constants
# ---------------------------------------------------------------------------

WIDTH = 640
CARD_X = 24
CARD_W = WIDTH - 2 * CARD_X
CARD_R = 10  # border-radius


def _card(y: int, h: int, fill: str = BG_CARD) -> str:
    return (
        f'<rect x="{CARD_X}" y="{y}" width="{CARD_W}" height="{h}" '
        f'rx="{CARD_R}" fill="{fill}" stroke="{BORDER}" stroke-width="1"/>'
    )


def _text(
    x: int,
    y: int,
    text: str,
    *,
    size: int = 14,
    fill: str = TEXT_WHITE,
    weight: str = "normal",
    anchor: str = "start",
    family: str = "monospace",
) -> str:
    return (
        f'<text x="{x}" y="{y}" font-size="{size}" fill="{fill}" '
        f'font-weight="{weight}" text-anchor="{anchor}" '
        f'font-family="{family}">{escape(str(text))}</text>'
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def render_operator_infographic(data: dict[str, Any]) -> str:
    """Return SVG markup for a visual operator account statement.

    *data* is the dict returned by ``account_statement``.
    """
    summary = data.get("account_summary", {})
    balance = summary.get("balance_sats", 0)
    deposited = summary.get("total_deposited_sats", 0)
    fees_paid = summary.get("total_fees_paid_sats", 0)
    certified = summary.get("total_certified_sats", 0)
    tranches: list[dict[str, Any]] = data.get("active_tranches", [])
    fee_schedule = data.get("fee_schedule", "See pricing model")
    generated_at = data.get("generated_at", datetime.now(timezone.utc).isoformat())

    parts: list[str] = []
    cy = 16  # current y cursor

    # ── Header ────────────────────────────────────────────────────────
    header_h = 80
    parts.append(_card(cy, header_h))
    parts.append(_text(CARD_X + 48, cy + 38, "Tollbooth Authority",
                       size=22, weight="bold", family="sans-serif"))
    parts.append(_text(CARD_X + 48, cy + 60, "Operator Account",
                       size=14, fill=ACCENT_ORANGE, family="sans-serif"))
    # lightning bolt
    parts.append(_text(CARD_X + 20, cy + 46, "\u26A1", size=28,
                       fill=ACCENT_ORANGE, family="sans-serif"))
    # timestamp
    ts_short = generated_at[:19].replace("T", " ") + " UTC"
    parts.append(_text(WIDTH - CARD_X - 16, cy + 64, ts_short,
                       size=9, fill=TEXT_DIM, anchor="end"))
    cy += header_h + 12

    # ── Hero balance ──────────────────────────────────────────────────
    hero_h = 100
    parts.append(_card(cy, hero_h))
    parts.append(_text(WIDTH // 2, cy + 30,
                       "C R E D I T   B A L A N C E",
                       size=10, fill=TEXT_GRAY, weight="bold",
                       anchor="middle", family="sans-serif"))
    parts.append(_text(WIDTH // 2, cy + 72, f"{balance:,}",
                       size=48, fill=ACCENT_GREEN, weight="bold",
                       anchor="middle"))
    parts.append(_text(WIDTH // 2 + 100, cy + 72, "sats",
                       size=13, fill=TEXT_GRAY, anchor="start",
                       family="sans-serif"))
    cy += hero_h + 12

    # ── Metrics row ───────────────────────────────────────────────────
    metrics = [
        ("\u2B07", "DEPOSITED", deposited, ACCENT_BLUE),
        ("\u2B06", "FEES PAID", fees_paid, ACCENT_ORANGE),
        ("\u2714", "CERTIFIED", certified, ACCENT_GREEN),
    ]
    metric_w = (CARD_W - 24) // 3
    metric_h = 80
    for i, (icon, label, value, colour) in enumerate(metrics):
        mx = CARD_X + 8 + i * (metric_w + 8)
        parts.append(
            f'<rect x="{mx}" y="{cy}" width="{metric_w}" height="{metric_h}" '
            f'rx="8" fill="{BG_CARD_ALT}" stroke="{BORDER}" stroke-width="0.8"/>'
        )
        parts.append(_text(mx + metric_w // 2, cy + 24, icon,
                           size=16, fill=colour, anchor="middle",
                           family="sans-serif"))
        parts.append(_text(mx + metric_w // 2, cy + 50, f"{value:,}",
                           size=20, fill=colour, weight="bold",
                           anchor="middle"))
        parts.append(_text(mx + metric_w // 2, cy + 68, label,
                           size=8, fill=TEXT_GRAY, weight="bold",
                           anchor="middle", family="sans-serif"))
    cy += metric_h + 12

    # ── Fee schedule card ─────────────────────────────────────────────
    fee_h = 70
    parts.append(_card(cy, fee_h))
    parts.append(_text(CARD_X + 16, cy + 24, "FEE SCHEDULE",
                       size=10, fill=TEXT_GRAY, weight="bold",
                       family="sans-serif"))
    fee_label = fee_schedule if isinstance(fee_schedule, str) else (
        f"Rate: {fee_schedule.get('rate_percent', 2.0)}%   |   "
        f"Minimum: {fee_schedule.get('min_sats', 10)} sats"
    )
    parts.append(_text(CARD_X + 16, cy + 50, fee_label,
                       size=14, fill=TEXT_WHITE))
    cy += fee_h + 12

    # ── Active credit tranches ────────────────────────────────────────
    tranche_rows = max(len(tranches), 1)
    tranche_h = 50 + tranche_rows * 24
    parts.append(_card(cy, tranche_h))
    parts.append(_text(CARD_X + 16, cy + 24, "ACTIVE CREDIT TRANCHES",
                       size=10, fill=TEXT_GRAY, weight="bold",
                       family="sans-serif"))

    cols_t = [CARD_X + 16, CARD_X + 160, CARD_X + 310, CARD_X + 440]
    headers_t = ["SOURCE", "GRANTED", "ORIGINAL", "REMAINING"]
    for x, h in zip(cols_t, headers_t):
        parts.append(_text(x, cy + 44, h, size=8, fill=TEXT_DIM, weight="bold"))
    parts.append(
        f'<line x1="{CARD_X + 12}" y1="{cy + 50}" '
        f'x2="{WIDTH - CARD_X - 12}" y2="{cy + 50}" '
        f'stroke="{BORDER}" stroke-width="0.5"/>'
    )

    if tranches:
        for i, t in enumerate(tranches):
            ry = cy + 68 + i * 24
            source = t.get("invoice_id", "unknown")
            if source.startswith("seed"):
                source = "Seed (v1)"
            elif len(source) > 14:
                source = source[:12] + ".."
            granted = str(t.get("granted_at", ""))[:10]
            original = f'{t.get("original_sats", 0):,}'
            remaining = f'{t.get("remaining_sats", 0):,}'
            parts.append(_text(cols_t[0], ry, source, size=10, fill=ACCENT_BLUE))
            parts.append(_text(cols_t[1], ry, granted, size=10))
            parts.append(_text(cols_t[2], ry, original, size=10))
            parts.append(_text(cols_t[3], ry, remaining, size=10,
                               fill=ACCENT_GREEN, weight="bold"))
    else:
        parts.append(_text(cols_t[0], cy + 68, "No active tranches",
                           size=10, fill=TEXT_DIM))
    cy += tranche_h + 12

    # ── Footer ────────────────────────────────────────────────────────
    footer_h = 60
    parts.append(_text(WIDTH // 2, cy + 16,
                       "DPYC \u2022 Don\u2019t Pester Your Customer",
                       size=10, fill=TEXT_DIM, anchor="middle",
                       family="sans-serif"))
    parts.append(_text(WIDTH // 2, cy + 34,
                       "Powered by Bitcoin Lightning \u26A1 \u2022 Tollbooth Protocol",
                       size=9, fill=TEXT_DIM, anchor="middle",
                       family="sans-serif"))
    cy += footer_h

    # ── Assemble SVG ──────────────────────────────────────────────────
    total_h = cy + 8
    body = "\n  ".join(parts)
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'width="{WIDTH}" height="{total_h}" '
        f'viewBox="0 0 {WIDTH} {total_h}">\n'
        f'  <rect width="{WIDTH}" height="{total_h}" fill="{BG_DARK}"/>\n'
        f"  {body}\n"
        f"</svg>"
    )
