# domain_search

Check domain availability by querying registry infrastructure directly. No web scraping, no third-party APIs, no WHOIS libraries that silently proxy through commercial services.

Queries go straight to authoritative RDAP servers (the modern, structured replacement for WHOIS) with automatic fallback to raw WHOIS over TCP port 43. Your searches stay between you and the registry.

## Why

Every "domain availability" website logs your searches. Some front-run registrations. Most return stale cached data. WHOIS libraries on PyPI often make HTTP requests to web services rather than querying port 43 directly — you can't tell where your queries end up.

This tool has a transparent data path: RDAP over HTTPS to the authoritative registry, or raw TCP to the WHOIS server. Nothing in between.

## Install

```
git clone https://github.com/jimmc414/domain_search.git
cd domain_search
pip install -r requirements.txt
```

Requires Python 3.11+. Dependencies: `aiohttp`, `rich`, `tldextract`.

## Usage

```bash
# Single domain
python cli.py example.com

# Multiple domains
python cli.py example.com example.org startup.io

# Bulk from file (one domain per line)
python cli.py --file domains.txt

# JSON output
python cli.py example.com --format json

# CSV output
python cli.py example.com --format csv

# Show raw RDAP/WHOIS responses
python cli.py example.com --verbose

# Adjust rate limit (default: 1 query/sec/server)
python cli.py --file domains.txt --rate 2

# Watch a domain until it becomes available (check every 5 minutes)
python cli.py expiring-domain.com --watch 300

# Suggest available domains from a keyword
python cli.py --suggest cloud

# Suggest with specific TLDs, only show available
python cli.py --suggest cloud --tlds com,io,dev --available-only

# Pipe available suggestions as JSON
python cli.py --suggest cloud --available-only --format json 2>/dev/null | jq '.[].domain'
```

## Example output

```
$ python cli.py google.com notregistered12345.com example.co.uk

┏━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━┳━━━━━━━━┳━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━┓
┃ Domain                 ┃ Available ┃ Owner  ┃ Registrar      ┃ Expires (YYYY-MM-DD) ┃ Status                ┃ Via  ┃
┡━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━╇━━━━━━━━╇━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━┩
│ google.com             │ No        │ hidden │ MarkMonitor    │ 2028-09-14           │ client delete         │ rdap │
│                        │           │        │ Inc.           │                      │ prohibited, client    │      │
│                        │           │        │                │                      │ transfer prohibited   │      │
│ notregistered12345.com │ Yes       │        │                │                      │                       │ rdap │
│ example.co.uk          │ No        │ PRIVATE│ Nominet UK     │                      │ server delete         │ rdap │
│                        │           │        │                │                      │ prohibited, server    │      │
│                        │           │        │                │                      │ transfer prohibited   │      │
└────────────────────────┴───────────┴────────┴────────────────┴──────────────────────┴───────────────────────┴──────┘
```

### Watch mode

Monitor a pendingDelete or expiring domain and get notified the moment it drops:

```
$ python cli.py getcounted.us --watch 300

Watching getcounted.us every 5m. Ctrl+C to stop.

  #1  2026-03-27 01:55:22  not available  pendingdelete
  #2  2026-03-27 02:00:22  not available  pendingdelete
  #3  2026-03-27 02:05:22  not available  pendingdelete
  #4  2026-03-27 02:10:22  AVAILABLE — getcounted.us is ready to register!
```

Sends a Windows toast notification (WSL2) or `notify-send` (Linux desktop) when the domain becomes available.

### Suggest mode

Generate and check domain name variations from a keyword. Tries the bare keyword across popular TLDs, then common prefix/suffix patterns (get-, try-, -app, -hq, -lab, etc.), and filters to available domains:

```
$ python cli.py --suggest cloud --tlds com,io,dev --available-only

Generating domains for cloud across com,io,dev (96 candidates)...

Available (10):
  cloud.dev        rdap
  cloudgo.dev      rdap
  oncloud.dev      rdap
  clouddev.dev     rdap
  getcloud.dev     rdap
  heycloud.dev     rdap
  thecloud.dev     rdap
  trycloud.dev     rdap
  joincloud.dev    rdap
  withcloud.dev    rdap

10 of 96 candidates available
```

All generation happens locally — no keyword ever leaves your machine. The candidates are checked through the same RDAP/WHOIS pipeline with per-server rate limiting.

## How it works

1. **RDAP first.** Downloads the [IANA RDAP bootstrap file](https://data.iana.org/rdap/dns.json) to map TLDs to their authoritative RDAP servers. Caches locally for 24 hours. Queries return structured JSON with standardized status codes over HTTPS.

2. **WHOIS fallback.** For TLDs without RDAP support, opens a raw TCP connection to the WHOIS server on port 43. Parses the unstructured text response with heuristics. No external WHOIS libraries.

3. **Second-level TLDs.** Handles `.co.uk`, `.com.au`, `.pvt.k12.ma.us`, etc. correctly via the [Public Suffix List](https://publicsuffix.org/). `foo.co.uk` is parsed as domain `foo` under TLD `co.uk`, not domain `co` under TLD `uk`.

4. **Per-server rate limiting.** Token bucket per registry server. Querying Verisign and Nominet simultaneously is fine; hammering one server is not. Default: 1 req/sec/server.

5. **Input normalization.** Accepts bare domains, URLs (`https://www.example.com/page`), subdomains (`mail.example.com`), and IDN domains (converted to Punycode).

## What it reports

For registered domains: registrant/owner (when visible), registrar, creation date, expiry date (YYYY-MM-DD), and EPP status codes (e.g., `clientTransferProhibited`, `redemptionPeriod`, `pendingDelete`).

The **Owner** column shows the actual registrant name and organization when the registry exposes it. Most gTLD registrations post-GDPR show `PRIVATE` (behind a privacy proxy) or `hidden` (thin registry like Verisign where registrant data isn't in the response at all). Some ccTLDs (`.us`, `.uk`, `.au`) still expose registrant info.

Domains in `redemptionPeriod` or `pendingDelete` are flagged — they're registered but may become available soon. The **Est. Release** column shows an approximate timeline based on the domain lifecycle:

| Status | Meaning | Typical Timeline |
|--------|---------|------------------|
| `autoRenewPeriod` | Grace period after expiry, registrar can still renew | ~0-45 days after expiry |
| `redemptionPeriod` | Owner can reclaim for a fee | ~30 days |
| `pendingDelete` | Registry will delete and release | ~5 days |

A **status legend** is printed below the table explaining every status code that appeared in the results.

Use `--watch` to monitor transitional domains until they drop.

## Limitations

- **Premium/reserved domains** may appear as "available" via RDAP but are only purchasable at inflated registry prices. There is no universal signal for this in the protocol.
- **Rate limiting is real.** Bulk checking 500 domains at 1/sec takes ~8 minutes. This is the cost of querying registries directly without getting blocked.
- **Some WHOIS servers refuse cloud IPs.** The tool works from residential connections but may get connection refused from VPS/cloud providers for certain registries.
- **WHOIS parsing is heuristic.** Every registry formats responses differently. The parser handles the common formats; exotic TLDs may return partial data.

## Architecture

```
cli.py              CLI entry point, output formatting (table/json/csv)
checker.py          Orchestrator: RDAP-first, WHOIS fallback, async bulk
rdap.py             RDAP client, IANA bootstrap loading/caching
whois_client.py     Raw TCP WHOIS (RFC 3912), heuristic response parsing
suggest.py          Domain name generation engine (keyword → candidates)
domain_parser.py    tldextract integration, IDN/punycode, input validation
rate_limiter.py     Per-server async token bucket
models.py           DomainResult dataclass
constants.py        RDAP fallback map, WHOIS server directory, patterns
```

## License

MIT
