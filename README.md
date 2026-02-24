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
- It **stamps purchase orders** — EdDSA-signed JWT certificates that prove an operator has paid their fee before collecting a fare.
- It **never touches the fare itself**. The operator collects from the user directly.

The Authority's signature is the proof that the turnpike is legitimate. Without the stamp, the toll booth doesn't open.

## How It Works

1. **Register.** An operator connects to the Authority via [Horizon MCP](https://www.fastmcp.cloud/) and calls `register_operator`. The Authority creates a ledger entry — the operator now exists on the turnpike.

2. **Fund.** The operator calls `purchase_credits` with the number of sats they want to pre-fund. The Authority returns a Lightning invoice from its own BTCPay Server. The operator pays. After settlement, `check_payment` credits the balance.

3. **Certify.** When a user wants to buy credits from an operator, the operator's server calls `certify_credits`. The Authority deducts the 2% fee, signs an EdDSA JWT certificate, and returns it. This is the stamp on the purchase order.

4. **Verify.** The operator's [tollbooth-dpyc](https://github.com/lonniev/tollbooth-dpyc) library verifies the JWT using the Authority's hardcoded public key. Only if the stamp is valid does the operator create a Lightning invoice for the user. No stamp, no fare.

## Architecture

The Tollbooth ecosystem is a three-party protocol spanning three repositories:

| Repo | Role |
|------|------|
| **tollbooth-authority** (this repo) | The institution — fee collection, EdDSA signing, purchase order certification |
| [tollbooth-dpyc](https://github.com/lonniev/tollbooth-dpyc) | The booth — operator-side credit ledger, BTCPay client, tool gating |
| [thebrain-mcp](https://github.com/lonniev/thebrain-mcp) | The first city — reference MCP server powered by Tollbooth |

![Three-Party Protocol](docs/diagrams/tollbooth-three-party-protocol.svg)

## Getting Started

### Connecting via Horizon MCP

The Authority runs on [FastMCP Cloud](https://www.fastmcp.cloud/). Any MCP client (Claude Desktop, Cursor, your own agent) can connect via Horizon:

```
https://www.fastmcp.cloud/server/lonniev/tollbooth-authority
```

Authentication is automatic — Horizon OAuth identifies you as an operator. No API keys to manage.

### First Connection Walkthrough

Once connected, walk through the bootstrap in order:

1. **`register_operator`** — Creates your ledger entry. You'll get back your operator ID and a zero balance.
2. **`purchase_credits(amount_sats=1000)`** — Returns a Lightning invoice. Pay it with any Lightning wallet.
3. **`check_payment(invoice_id="...")`** — Pass the invoice ID from step 2. Confirms settlement and credits your balance.
4. **`check_balance`** — Verify your balance is funded.
5. **`operator_status`** — See your registration, balance, and the Authority's public key (you'll hardcode this in your tollbooth-dpyc integration).

You're now ready to certify purchase orders. When your MCP server needs to gate a user credit purchase, it calls `certify_credits` with the operator ID and amount.

### Self-Hosting

To run your own Authority instance, set these environment variables:

| Variable | Purpose | Example |
|----------|---------|---------|
| `AUTHORITY_SIGNING_KEY` | Base64-encoded Ed25519 private key for signing JWTs | Output of `scripts/generate_keypair.py` |
| `BTCPAY_HOST` | Authority's BTCPay Server URL for fee collection | `https://btcpay.example.com` |
| `BTCPAY_STORE_ID` | BTCPay store ID for the Authority's fee store | `AbCdEfGh1234` |
| `BTCPAY_API_KEY` | BTCPay API key with invoice + payout permissions | `your-btcpay-api-key` |
| `THEBRAIN_API_KEY` | TheBrain API key for operator ledger persistence | `your-thebrain-key` |
| `THEBRAIN_VAULT_BRAIN_ID` | Brain ID used as the operator credential vault | `uuid-of-vault-brain` |
| `THEBRAIN_VAULT_HOME_ID` | Home thought ID in the vault brain | `uuid-of-home-thought` |
| `TAX_RATE_PERCENT` | Fee rate as a percentage of each certified purchase | `2.0` (default) |
| `TAX_MIN_SATS` | Minimum fee per certification in satoshis | `10` (default) |
| `CERTIFICATE_TTL_SECONDS` | How long a signed certificate remains valid | `600` (default, 10 minutes) |

## MCP Tools

| Tool | Purpose |
|------|---------|
| `register_operator` | Register as an operator on the turnpike. Creates your ledger entry so you can fund and certify. |
| `purchase_credits` | Create a Lightning invoice to pre-fund your credit balance with the Authority. |
| `check_payment` | Verify that a Lightning invoice has settled and credit the payment to your balance. |
| `check_balance` | Check your current credit balance, total deposited, total consumed, and pending invoices. |
| `operator_status` | View your registration status, balance summary, and the Authority's Ed25519 public key. |
| `certify_credits` | The core machine-to-machine tool. Deducts fee and returns an EdDSA-signed JWT certificate. |

### Deprecated Tools (v0.1.x names)

The following tool names from v0.1.x are deprecated. They remain registered as shims for one release cycle:

| Old Name | New Name | Behavior |
|----------|----------|----------|
| `purchase_tax_credits` | `purchase_credits` | Returns error with migration guidance |
| `check_tax_payment` | `check_payment` | Returns error with migration guidance |
| `tax_balance` | `check_balance` | Returns error with migration guidance |
| `certify_purchase` | `certify_credits` | Pass-through (delegates to `certify_credits`) |

## Development

```bash
python -m venv venv
source venv/bin/activate
pip install -e ".[dev]"
pytest tests/ -q
```

## Key Generation

### EdDSA Signing Key (JWT certificates)

```bash
python scripts/generate_keypair.py
```

Outputs the base64-encoded private key (for `AUTHORITY_SIGNING_KEY` env var) and the PEM public key (for hardcoding in tollbooth-dpyc).

### DPYC Identity (Nostr npub)

Each Authority has a Nostr keypair that identifies it on the DPYC Honor Chain. Generate one using the script in [tollbooth-dpyc](https://github.com/lonniev/tollbooth-dpyc):

```bash
pip install nostr-sdk
python -c "from nostr_sdk import Keys; k = Keys.generate(); print(f'DPYC_AUTHORITY_NPUB={k.public_key().to_bech32()}'); print(f'nsec (back up!): {k.secret_key().to_bech32()}')"
```

Or clone tollbooth-dpyc and run `scripts/generate_nostr_keypair.py` for full output.

Add to your `.env`:

```
DPYC_AUTHORITY_NPUB=npub1...
DPYC_UPSTREAM_AUTHORITY_NPUB=       # empty for Prime Authority
```

## Further Reading

[The Phantom Tollbooth on the Lightning Turnpike](https://stablecoin.myshopify.com/blogs/our-value/the-phantom-tollbooth-on-the-lightning-turnpike) — the full story of how we're monetizing the monetization of AI APIs, and then fading to the background.

## License

Apache License 2.0 — see [LICENSE](LICENSE) and [NOTICE](NOTICE) for details.

---

*Because every turnpike needs an authority. Not to control the road — just to make sure the stamps are real and the fares are fair.*
