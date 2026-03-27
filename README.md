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
```

## Example output

```
$ python cli.py google.com notregistered12345.com example.co.uk

┏━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━┳━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━┓
┃ Domain                 ┃ Available ┃ Registrar      ┃ Expires    ┃ Status                         ┃ Via  ┃
┡━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━╇━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━┩
│ google.com             │ No        │ MarkMonitor    │ 2028-09-14 │ client delete prohibited,      │ rdap │
│                        │           │ Inc.           │            │ client transfer prohibited,    │      │
│                        │           │                │            │ client update prohibited       │      │
│ notregistered12345.com │ Yes       │                │            │                                │ rdap │
│ example.co.uk          │ No        │ Nominet UK     │            │ server delete prohibited,      │ rdap │
│                        │           │                │            │ server update prohibited,      │      │
│                        │           │                │            │ server transfer prohibited     │      │
└────────────────────────┴───────────┴────────────────┴────────────┴────────────────────────────────┴──────┘
```

## How it works

1. **RDAP first.** Downloads the [IANA RDAP bootstrap file](https://data.iana.org/rdap/dns.json) to map TLDs to their authoritative RDAP servers. Caches locally for 24 hours. Queries return structured JSON with standardized status codes over HTTPS.

2. **WHOIS fallback.** For TLDs without RDAP support, opens a raw TCP connection to the WHOIS server on port 43. Parses the unstructured text response with heuristics. No external WHOIS libraries.

3. **Second-level TLDs.** Handles `.co.uk`, `.com.au`, `.pvt.k12.ma.us`, etc. correctly via the [Public Suffix List](https://publicsuffix.org/). `foo.co.uk` is parsed as domain `foo` under TLD `co.uk`, not domain `co` under TLD `uk`.

4. **Per-server rate limiting.** Token bucket per registry server. Querying Verisign and Nominet simultaneously is fine; hammering one server is not. Default: 1 req/sec/server.

5. **Input normalization.** Accepts bare domains, URLs (`https://www.example.com/page`), subdomains (`mail.example.com`), and IDN domains (converted to Punycode).

## What it reports

For registered domains: registrar, creation date, expiry date, and EPP status codes (e.g., `clientTransferProhibited`, `redemptionPeriod`, `pendingDelete`).

Domains in `redemptionPeriod` or `pendingDelete` are flagged — they're registered but may become available soon.

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
domain_parser.py    tldextract integration, IDN/punycode, input validation
rate_limiter.py     Per-server async token bucket
models.py           DomainResult dataclass
constants.py        RDAP fallback map, WHOIS server directory, patterns
```

## License

MIT
