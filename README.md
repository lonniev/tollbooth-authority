# Tollbooth Authority

<p align="center">
  <img src="https://raw.githubusercontent.com/lonniev/tollbooth-dpyc/main/docs/tollbooth-hero.png" alt="Milo drives the Lightning Turnpike — Don't Pester Your Customer" width="800">
</p>

**The institution that built the infrastructure.**

> *The metaphors in this project are drawn with admiration from* The Phantom Tollbooth *by Norton Juster, illustrated by Jules Feiffer (1961). Milo, Tock, the Tollbooth, Dictionopolis, and Digitopolis are creations of Mr. Juster's extraordinary imagination. We just built the payment infrastructure.*

---

## The Turnpike Authority

Every turnpike has an authority. Not the operators who run the booths, and not the drivers who pay the fares — but the institution that poured the concrete, erected the signs, and stamped the purchase orders.

The Tollbooth Authority is the Massachusetts Turnpike Authority of the Lightning economy. It doesn't operate any toll booths. It doesn't touch operator BTCPay stores. It never sees user payment data. What it does is simpler and more essential:

- It **registers operators** who want to run toll booths on the turnpike.
- It **collects a modest certification fee** — 2% of every fare, minimum 10 sats — paid in advance via Lightning.
- It **stamps purchase orders** — Schnorr-signed Nostr event certificates (kind 30079) that prove an operator has paid their fee before collecting a fare.
- It **never touches the fare itself**. The operator collects from the user directly.

The Authority's signature is the proof that the turnpike is legitimate. Without the stamp, the toll booth doesn't open.

## Architecture

Since v0.5.0, the Authority is built on `OperatorRuntime` from [tollbooth-dpyc](https://github.com/lonniev/tollbooth-dpyc) with `purchase_mode="direct"`. Architecturally it is a standard operator — a trust root that reads `NEON_DATABASE_URL` from the environment and does not require an upstream certificate. Standard tools (credits, payments, balance, statements, pricing, notarization) are delegated to the wheel's `register_standard_tools()`, leaving the Authority's `server.py` to define only its domain-specific tools.

This refactor reduced the server module from approximately 1,900 lines to approximately 970 lines.

### Three-Party Protocol

The Tollbooth ecosystem is a three-party protocol spanning three repositories:

| Repo | Role |
|------|------|
| **tollbooth-authority** (this repo) | The institution — fee collection, Schnorr signing, purchase order certification |
| [tollbooth-dpyc](https://github.com/lonniev/tollbooth-dpyc) | The booth — operator-side credit ledger, BTCPay client, tool gating |
| [thebrain-mcp](https://github.com/lonniev/thebrain-mcp) | The first city — reference MCP server powered by Tollbooth |

### How It Works

1. **Register.** An operator connects to the Authority via [Horizon MCP](https://www.fastmcp.cloud/) and calls `register_operator(npub=...)`. The Authority creates a ledger entry and provisions an isolated Neon schema for the operator.

2. **Fund.** The operator calls `purchase_credits` with the number of sats they want to pre-fund. The Authority returns a Lightning invoice from its own BTCPay Server. The operator pays. After settlement, `check_payment` credits the balance.

3. **Certify.** When a user wants to buy credits from an operator, the operator's server calls `certify_credits`. The Authority deducts the 2% ad valorem fee (via the `@runtime.paid_tool` decorator), signs a Schnorr-based Nostr event certificate, and returns it.

4. **Verify.** The operator's [tollbooth-dpyc](https://github.com/lonniev/tollbooth-dpyc) library verifies the certificate using the Authority's Nostr npub. Only if the stamp is valid does the operator create a Lightning invoice for the user. No stamp, no fare.

### Nostr Certificate Format (kind 30079)

Certificates are Schnorr-signed Nostr events (NIP-33 parameterized replaceable events) rather than Ed25519 JWTs. Each certificate contains the operator npub in a `p` tag, the certified amount and protocol in `t`/`L` tags, an `expiration` tag, and the content field holds the structured claim data. Verification uses BIP-340 Schnorr signatures against the Authority's Nostr npub.

### DPYC Registry Enforcement

The Authority checks the [dpyc-community `members.json`](https://github.com/lonniev/dpyc-community/blob/main/members.json) registry at certification time. Operators must have `"status": "active"` in the registry. The registry is HTTP-cached with a configurable TTL. Design is fail-closed: if the registry is unreachable, certification is denied.

### Automatic Upstream Certification

Non-Prime Authorities automatically certify upstream in real-time. When `certify_credits` is called and `upstream_authority_address` is configured, the Authority uses `AuthorityCertifier` (from tollbooth-dpyc) to obtain a signed certificate from its upstream Authority before issuing its own. The Prime Authority (root of the chain, `upstream_authority_address` empty) self-signs and skips the upstream call.

A startup validator rejects any non-Prime configuration where `TAX_RATE_PERCENT < UPSTREAM_TAX_PERCENT` — a fee rate below the upstream rate would lose money on every certification.

### Ad Valorem Pricing

`certify_credits` is registered as a `@runtime.paid_tool` with 2% ad valorem pricing on the `amount_sats` parameter (minimum 10 sats). The fee is debited by the decorator, and the cost is read from `runtime._last_debit_cost` — no double computation.

### OTS Notarization

OpenTimestamps notarization is enabled (`ots_enabled=True` on the runtime). Ledger state can be notarized and verified through the standard `notarize_ledger` and `get_notarization_proof` tools provided by the wheel.

### Anti-Replay (ReplayTracker)

Every certificate includes a unique JTI (JWT ID). The Authority tracks seen JTIs in an in-memory ordered dict with TTL-based pruning. This prevents certificate replay attacks even if a certificate is intercepted before expiration.

## MCP Tools

### Domain Tools (defined in server.py)

| Tool | Purpose |
|------|---------|
| `register_operator` | Provision an operator in the Authority ledger. Creates a ledger entry, provisions an isolated Neon schema, and registers in the community registry via the Oracle. Idempotent. |
| `update_operator` | Update an operator's community registry entry (service URL, display name). |
| `deregister_operator` | Remove an operator from the DPYC community registry. |
| `get_operator_config` | Retrieve operator bootstrap configuration (Neon URL, schema). Gated by Schnorr proof of npub ownership. |
| `operator_status` | View registration status, balance summary, vault backend, and the Authority's Nostr npub. |
| `certify_credits` | The core machine-to-machine tool. Deducts the 2% ad valorem fee and returns a Schnorr-signed Nostr event certificate (kind 30079). |
| `check_dpyc_membership` | Free diagnostic. Looks up an npub in the DPYC community registry. |
| `check_balance` | Check an operator's credit balance (overrides the standard tool to fall back to the operator's own npub). |
| `register_authority_npub` | Step 1/3 of Authority onboarding — send a Nostr DM challenge to a curator candidate. |
| `confirm_authority_claim` | Step 2/3 of Authority onboarding — verify candidate DM reply and escalate to Prime. |
| `check_authority_approval` | Step 3/3 of Authority onboarding — check Prime approval and activate the Authority. |

### Standard Tools (from tollbooth-dpyc wheel)

The following tools are registered by `register_standard_tools()` and are not defined in Authority code:

| Tool | Purpose |
|------|---------|
| `purchase_credits` | Create a Lightning invoice to pre-fund credit balance. |
| `check_payment` | Verify that a Lightning invoice has settled and credit the balance. |
| `service_status` | Software versions for tollbooth-authority, tollbooth-dpyc, fastmcp, and Python. |
| `account_statement` | Detailed ledger history. |
| `account_statement_infographic` | Visual ledger summary. |
| `get_pricing_model` | View the current pricing model. |
| `set_pricing_model` | Update the pricing model. |
| `list_constraint_types` | List available constraint types for tool pricing. |
| `notarize_ledger` | Create an OpenTimestamps notarization of ledger state. |
| `get_notarization_proof` | Retrieve a notarization proof. |

All tools that accept an `npub` parameter also accept a `proof: str` parameter for Schnorr signature verification of identity.

### Deprecated Tools

| Old Name | Replacement | Status |
|----------|-------------|--------|
| `activate_dpyc` | `register_operator(npub=...)` | Deprecated. Returns error directing callers to the new tool. |
| `report_upstream_purchase` | *(automatic)* | Deprecated. Upstream certification is automatic via `AuthorityCertifier` inside `certify_credits`. |

## Getting Started

### Connecting via Horizon MCP

The Authority runs on [FastMCP Cloud](https://www.fastmcp.cloud/). Any MCP client (Claude Desktop, Cursor, your own agent) can connect via Horizon:

```
https://www.fastmcp.cloud/server/lonniev/tollbooth-authority
```

Authentication is automatic — Horizon OAuth identifies you as an operator. No API keys to manage.

### First Connection Walkthrough

Once connected, walk through the bootstrap in order:

1. **`register_operator(npub="npub1...")`** — Creates your ledger entry and provisions a Neon schema. Returns your npub, balance, and Neon URL.
2. **`purchase_credits(amount_sats=1000)`** — Returns a Lightning invoice. Pay it with any Lightning wallet.
3. **`check_payment(invoice_id="...")`** — Pass the invoice ID from step 2. Confirms settlement and credits your balance.
4. **`check_balance`** — Verify your balance is funded.
5. **`operator_status`** — See your registration, balance, vault backend, and the Authority's public key (you'll hardcode this in your tollbooth-dpyc integration).

### Self-Hosting

To run your own Authority instance, set these environment variables:

| Variable | Purpose | Example |
|----------|---------|---------|
| `NEON_DATABASE_URL` | Neon Postgres URL for persistent operator ledgers (required) | `postgresql://...` |
| `TOLLBOOTH_NOSTR_OPERATOR_NSEC` | Nostr secret key (nsec) for Schnorr certificate signing | `nsec1...` |
| `BTCPAY_HOST` | Authority's BTCPay Server URL for fee collection | `https://btcpay.example.com` |
| `BTCPAY_STORE_ID` | BTCPay store ID for the Authority's fee store | `AbCdEfGh1234` |
| `BTCPAY_API_KEY` | BTCPay API key with invoice + payout permissions | `your-btcpay-api-key` |
| `DPYC_COMMUNITY_REGISTRY_URL` | URL to `members.json` for membership enforcement | `https://raw.githubusercontent.com/...` |
| `DPYC_ENFORCE_MEMBERSHIP` | Enable registry enforcement at certification time | `true` |
| `TAX_RATE_PERCENT` | Fee rate as a percentage of each certified purchase | `2.0` (default) |
| `TAX_MIN_SATS` | Minimum fee per certification in satoshis | `10` (default) |
| `CERTIFICATE_TTL_SECONDS` | How long a signed certificate remains valid | `600` (default, 10 minutes) |
| `UPSTREAM_AUTHORITY_ADDRESS` | Upstream Authority MCP URL (empty for Prime Authority) | `https://...` |

## Development

```bash
python -m venv venv
source venv/bin/activate
pip install -e ".[dev]"
pytest tests/ -q
```

## Key Generation

### Nostr Signing Key (Schnorr certificates)

The Authority signs certificates with a Nostr nsec/npub keypair. Generate one using any Nostr key generator (e.g., `nak key generate`). The nsec goes in `TOLLBOOTH_NOSTR_OPERATOR_NSEC`; the npub is surfaced via `operator_status` for tollbooth-dpyc verification.

### DPYC Identity (Nostr npub)

Each Authority has a Nostr keypair that identifies it on the DPYC Honor Chain:

```bash
pip install nostr-sdk
python -c "from nostr_sdk import Keys; k = Keys.generate(); print(f'npub: {k.public_key().to_bech32()}'); print(f'nsec (back up!): {k.secret_key().to_bech32()}')"
```

## Further Reading

[The Phantom Tollbooth on the Lightning Turnpike](https://stablecoin.myshopify.com/blogs/our-value/the-phantom-tollbooth-on-the-lightning-turnpike) — the full story of how we're monetizing the monetization of AI APIs, and then fading to the background.

## License

Apache License 2.0 — see [LICENSE](LICENSE) and [NOTICE](NOTICE) for details.

---

*Because every turnpike needs an authority. Not to control the road — just to make sure the stamps are real and the fares are fair.*
