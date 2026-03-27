# domain_search

CLI tool that checks domain availability by querying RDAP and WHOIS servers directly. Generates name suggestions, monitors expiring domains, and registers via Porkbun's API.

No web scraping. No third-party availability APIs. Queries go to authoritative registry servers over HTTPS (RDAP) or raw TCP port 43 (WHOIS).

## Why not just use a website?

Domain availability sites log your searches. Some [front-run registrations](https://en.wikipedia.org/wiki/Domain_name_front_running). Most return cached data. WHOIS libraries on PyPI often proxy through commercial web services instead of querying port 43 — you can't inspect where your queries go.

This tool has a transparent data path: your machine talks directly to the registry. The query protocol (RDAP or WHOIS), server hostname, and raw response are all visible with `--verbose`.

## Install

```
git clone https://github.com/jimmc414/domain_search.git
cd domain_search
pip install -r requirements.txt
```

Python 3.11+. Dependencies: `aiohttp`, `rich`, `tldextract`.

## Quick reference

```bash
python cli.py example.com                                          # check one domain
python cli.py example.com example.org foo.co.uk                    # check several
python cli.py --file domains.txt                                   # bulk from file
python cli.py --file domains.txt --format json                     # JSON output
python cli.py --file domains.txt --format csv                      # CSV output
python cli.py example.com --verbose                                # show raw RDAP/WHOIS response
python cli.py --suggest cloud                                      # generate + check name variants
python cli.py --suggest cloud --tlds com,io,dev --available-only   # limit TLDs, filter to available
python cli.py expiring.com --watch 300                             # poll every 5m, alert on drop
python cli.py expiring.com --watch 300 --register --auto-register  # poll + auto-buy on drop
python cli.py coolname.dev --register                              # check + register if available
python cli.py coolname.dev --register --max-price 50               # raise premium price cap
```

## How it works

**RDAP first, WHOIS fallback.** The tool downloads the [IANA RDAP bootstrap file](https://data.iana.org/rdap/dns.json) (cached 24h) to resolve TLDs to their authoritative RDAP servers. RDAP returns structured JSON over HTTPS. For TLDs without RDAP, it falls back to raw WHOIS queries on TCP port 43 with heuristic response parsing.

**Second-level TLDs** (`.co.uk`, `.com.au`, `.pvt.k12.ma.us`) are handled via the [Public Suffix List](https://publicsuffix.org/) through `tldextract`. Input normalization accepts bare domains, full URLs, subdomains, and IDN domains (auto-converted to Punycode).

**Per-server rate limiting** via async token bucket. Default: 1 req/sec/server. Querying Verisign and Nominet in parallel is fine; flooding one server is not. Configurable with `--rate`.

## Output

```
$ python cli.py google.com notregistered12345.com example.co.uk

 Domain                  Available  Owner   Registrar       Expires     Est. Release  Status                       Via
 google.com              No         hidden  MarkMonitor     2028-09-14                client delete prohibited...  rdap
 notregistered12345.com  Yes                                                                                       rdap
 example.co.uk           No         PRIVATE Nominet UK                                server delete prohibited...  rdap

Status legend:
  clientdeleteprohibited — Registrar lock: cannot be deleted
  ...
```

For registered domains: registrant/owner (when visible), registrar, creation/expiry dates (YYYY-MM-DD), and EPP status codes.

**Owner column:** Shows actual registrant name when the registry exposes it. Most gTLDs post-GDPR show `PRIVATE` (privacy proxy) or `hidden` (thin registry — Verisign doesn't include registrant entities for `.com`/`.net`). Some ccTLDs (`.us`, `.uk`, `.au`) still expose registrant info.

**Est. Release column:** For domains in transitional states:

| Status | Meaning | Window |
|--------|---------|--------|
| `pendingDelete` | Registry deleting, will release | ~5 days |
| `redemptionPeriod` | Owner can reclaim for a fee | ~30 days |
| `autoRenewPeriod` | Registrar grace period after expiry | ~0-45 days |

## Watch mode

Poll a domain and get notified when it drops:

```
$ python cli.py getcounted.us --watch 300

Watching getcounted.us every 5m. Ctrl+C to stop.

  #1  2026-03-27 01:55:22  not available  pendingdelete
  #2  2026-03-27 02:00:22  not available  pendingdelete
  #3  2026-03-27 02:05:22  AVAILABLE — getcounted.us is ready to register!
```

Sends a Windows toast (WSL2) or `notify-send` (Linux) notification on availability. Combine with `--register --auto-register` to buy automatically when the domain drops.

## Suggest mode

Generate candidate domain names from a keyword and check availability in bulk:

```
$ python cli.py --suggest cloud --tlds com,io,dev --available-only

Generating domains for cloud across com,io,dev (96 candidates)...

Available (10):
  cloud.dev        rdap
  cloudgo.dev      rdap
  oncloud.dev      rdap
  ...

10 of 96 candidates available
```

Tries the bare keyword across specified TLDs, then common prefix/suffix patterns (`get-`, `try-`, `-app`, `-hq`, `-lab`, etc.). All generation is local — keywords never leave your machine. Pipeable: `--format json 2>/dev/null | jq '.[].domain'`.

## Registration (Porkbun)

Register domains from the CLI via [Porkbun's API](https://porkbun.com/api/json/v3/documentation). Porkbun charges near-wholesale prices with free WHOIS privacy.

### Setup

```bash
# 1. Create account + add credit at porkbun.com
# 2. Register one domain manually (API prerequisite)
# 3. Get keys at porkbun.com/account/api
# 4. Set credentials:
export PORKBUN_API_KEY="pk1_..."
export PORKBUN_SECRET_KEY="sk1_..."
```

Or `~/.config/domain_search/config.json` (chmod 600):
```json
{"porkbun_api_key": "pk1_...", "porkbun_secret_key": "sk1_..."}
```

### Flags

| Flag | Default | Description |
|------|---------|-------------|
| `--register` | off | Enable registration for available domains |
| `--auto-register` | off | Skip confirmation (for unattended `--watch`) |
| `--max-price N` | 20.00 | Refuse domains priced above N USD |

### Safety model

Registration always runs a price check first. If the price exceeds `--max-price` (default $20) or the pricing API call fails, registration is refused. `--auto-register` is a separate opt-in flag — you can't accidentally auto-purchase without explicitly requesting it. Credentials are never included in log output or `--verbose` responses.

### Examples

```bash
# Interactive: check, show price, confirm y/N
python cli.py coolname.dev --register

# Unattended: watch + buy when available, cap at $15
python cli.py expiring.com --watch 300 --register --auto-register --max-price 15

# Suggest + select which to register from numbered list
python cli.py --suggest cloud --tlds dev,io --register
```

## Limitations

- **Premium domains** may appear available via RDAP but cost significantly more than standard registration. `--max-price` guards against this, but there's no universal protocol signal for premium pricing.
- **Rate limiting is real.** 500 domains at 1 req/sec/server = ~8 minutes. Registries will block aggressive clients.
- **Some WHOIS servers reject cloud/VPS IPs.** Works from residential connections; may get refused from AWS/GCP/etc. for certain registries.
- **WHOIS parsing is heuristic.** Response formats vary per registry. Common formats are handled; exotic TLDs may return partial data.
- **Porkbun API requires one prior manual registration** before programmatic access is granted.

## Architecture

```
cli.py              argparse, output formatting, mode orchestration
checker.py          async orchestrator: RDAP-first, WHOIS fallback, bulk checks
rdap.py             RDAP client + IANA bootstrap caching
whois_client.py     raw TCP WHOIS (RFC 3912), heuristic parsing
registrar.py        Porkbun API client (pricing + registration)
config.py           credential loading (env vars / config file)
suggest.py          local domain name generation (keyword -> candidates)
domain_parser.py    tldextract wrapper, IDN/punycode, input validation
rate_limiter.py     per-server async token bucket
models.py           DomainResult, PricingResult, RegistrationResult
constants.py        RDAP fallback map, WHOIS servers, detection patterns
```

## License

MIT
