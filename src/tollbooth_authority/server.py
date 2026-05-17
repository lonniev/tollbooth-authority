"""FastMCP app — Tollbooth Authority (canonical, Lonnie-Authority).

The Authority code lives in the wheel as of tollbooth-dpyc 0.22.0. This
module supplies only the actor-specific configuration: identity name,
human-readable instructions, OperatorRuntime construction, and the two
standard tool-registration calls.
"""

from __future__ import annotations

import logging

from fastmcp import FastMCP

from tollbooth.authority import (
    AUTHORITY_TOOL_REGISTRY,
    OPERATOR_CREDENTIAL_TEMPLATE,
    register_authority_tools,
)
from tollbooth.runtime import OperatorRuntime, register_standard_tools
from tollbooth.tool_identity import STANDARD_IDENTITIES

from tollbooth_authority import __version__

logger = logging.getLogger(__name__)

# ======================================================================
# FastMCP app
# ======================================================================

mcp = FastMCP(
    "tollbooth-authority",
    instructions=(
        "Tollbooth Authority — Certified Purchase Order Service.\n\n"
        "The Authority is the institutional backbone of the Tollbooth ecosystem. "
        "It registers MCP operators, collects a small certification fee on every "
        "purchase order via Bitcoin Lightning, and issues Schnorr-signed Nostr event "
        "certificates that prove an operator has paid before collecting a fare from "
        "a user.\n\n"
        "## First-Time Bootstrap (follow these steps in order)\n\n"
        "1. Call `register_operator(npub=...)` with your Nostr npub — creates your "
        "ledger entry. Get your npub from the dpyc-oracle's how_to_join() tool. "
        "Returns your npub and a zero balance.\n"
        "2. Call `purchase_credits` with the number of sats to pre-fund "
        "(e.g., 1000). Returns a Lightning invoice with a checkoutLink.\n"
        "3. Pay the invoice using any Lightning wallet.\n"
        "4. Call `check_payment` with the invoice_id from step 2. "
        "On settlement, your credit balance is funded.\n"
        "5. Call `check_balance` or `operator_status` to confirm your funded balance "
        "and retrieve the Authority's Nostr npub for certificate verification.\n\n"
        "## Fee Computation\n\n"
        "Fee per certification = max(TAX_MIN_SATS, ceil(amount_sats * TAX_RATE_PERCENT / 100)). "
        "Defaults: 2% rate, 10 sat minimum. The fee is deducted from the operator's "
        "pre-funded balance when `certify_credits` is called.\n\n"
        "## Tool Overview\n\n"
        "- `register_operator` — First step. Idempotent; safe to call again.\n"
        "- `purchase_credits` — Creates a Lightning invoice. Call whenever balance is low.\n"
        "- `check_payment` — Polls an invoice. Call after payment; safe to call multiple times.\n"
        "- `check_balance` — Read-only balance check. No side effects.\n"
        "- `operator_status` — Registration info + Authority npub for certificate verification.\n"
        "- `certify_credits` — Core machine-to-machine tool. Deducts fee, returns Schnorr-signed "
        "Nostr event certificate.\n"
        "## Low-Balance Recovery\n\n"
        "If `certify_credits` returns 'Insufficient credit balance', the operator must "
        "fund more credits: call `purchase_credits`, pay, then `check_payment`. "
        "The operator's MCP server should surface this to the admin, not the end user.\n\n"
        "## Key Generation\n\n"
        "The Authority signs certificates with a Nostr nsec/npub keypair. Generate one "
        "using any Nostr key generator (e.g., `nak key generate`). The nsec goes in "
        "`TOLLBOOTH_NOSTR_OPERATOR_NSEC`; the npub is surfaced via `operator_status` "
        "for tollbooth-dpyc verification.\n"
    ),
)

# ======================================================================
# OperatorRuntime — Authority is a trust root (purchase_mode=direct)
# ======================================================================

runtime = OperatorRuntime(
    tool_registry={**STANDARD_IDENTITIES, **AUTHORITY_TOOL_REGISTRY},
    purchase_mode="direct",  # Authority is trust root — no upstream cert
    service_name="Tollbooth Authority",
    ots_enabled=True,
    operator_credential_template=OPERATOR_CREDENTIAL_TEMPLATE,
)

# ======================================================================
# Register tools — standard set (wheel) + Authority set (wheel)
# ======================================================================

register_standard_tools(
    mcp,
    "authority",
    runtime,
    service_name="tollbooth-authority",
    service_version=__version__,
)

register_authority_tools(mcp, runtime)
