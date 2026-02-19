# Tollbooth Authority

Certified Purchase Order Service for the Tollbooth ecosystem.

A [FastMCP](https://fastmcp.cloud) service deployed on Horizon that enforces tax collection via **EdDSA-signed JWT certificates**.

## How It Works

1. **Operators register** via Horizon OAuth identity
2. **Pre-fund tax balance** by paying Lightning invoices on Authority's BTCPay
3. **Certify purchase orders**: Authority deducts tax, returns an EdDSA-signed JWT
4. **tollbooth-dpyc verifies** the JWT with a hardcoded public key before creating user invoices

## MCP Tools

| Tool | Purpose |
|---|---|
| `register_operator` | Register via Horizon OAuth identity |
| `purchase_tax_credits` | Create Lightning invoice to pre-fund tax balance |
| `check_tax_payment` | Verify invoice settlement, credit tax balance |
| `tax_balance` | Check current tax balance and usage |
| `operator_status` | Registration status, public key info |
| `certify_purchase` | Deduct tax, return signed JWT certificate |
| `refresh_config` | Hot-reload env vars without redeploy |

## Development

```bash
python -m venv venv
source venv/bin/activate
pip install -e ".[dev]"
pytest tests/ -q
```

## Key Generation

```bash
python scripts/generate_keypair.py
```

Outputs the base64-encoded private key (for `AUTHORITY_SIGNING_KEY` env var) and the PEM public key (for hardcoding in tollbooth-dpyc).

## License

MIT
