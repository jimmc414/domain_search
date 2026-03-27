# domain_search — AI Agent Command Reference

This document describes exactly how to use the `domain_search` CLI tool programmatically. All commands are run from the project root directory.

## Entry point

```
python cli.py [domains...] [flags]
```

The tool has four mutually exclusive modes determined by flags:

| Mode | Trigger | Description |
|------|---------|-------------|
| **Check** | `python cli.py <domain>` | Default. Check one or more domains. |
| **Watch** | `--watch SECONDS` | Poll a single domain on an interval until available. |
| **Suggest** | `--suggest KEYWORD` | Generate candidate names from a keyword and check them. |
| **Register** | `--register` (modifier) | Not a standalone mode. Adds registration to any of the above. |

## Flags

| Flag | Short | Type | Default | Description |
|------|-------|------|---------|-------------|
| `domains` | | positional, variadic | | One or more domain names |
| `--file` | `-f` | string | | Path to file with domains, one per line. Lines starting with `#` are skipped. |
| `--format` | | `table\|json\|csv` | `table` | Output format |
| `--verbose` | `-v` | flag | off | Include raw RDAP/WHOIS response text |
| `--rate` | `-r` | float | 1.0 | Max queries per second per server |
| `--timeout` | `-t` | float | 10.0 | Per-query timeout in seconds |
| `--watch` | `-w` | int (seconds) | | Poll interval. Requires exactly one domain. |
| `--suggest` | `-s` | string | | Keyword to generate domain name variants from |
| `--tlds` | | string | | Comma-separated TLD list for `--suggest` (default: com,io,dev,ai,co,app,net,org,sh,so,me,to,is,cc,xyz) |
| `--available-only` | | flag | off | With `--suggest`, suppress taken domains from output |
| `--register` | | flag | off | Enable Porkbun registration for available domains |
| `--auto-register` | | flag | off | Skip interactive confirmation. Requires `--register`. |
| `--max-price` | | float (USD) | 20.00 | Refuse registration if price exceeds this |

## Mode 1: Check

Check availability of one or more domains.

```bash
# Single domain
python cli.py example.com

# Multiple domains
python cli.py example.com example.org foo.co.uk

# From file
python cli.py --file domains.txt
```

**Input normalization applied automatically:**
- URLs stripped: `https://www.example.com/page` -> `example.com`
- Subdomains stripped: `mail.example.com` -> `example.com`
- IDN converted: `munchen.de` -> `xn--mnchen-3ya.de`
- Trailing dots/slashes removed

**Exit code:** 0 on success, 1 on input error.

### JSON output schema

```bash
python cli.py example.com --format json
```

Returns a JSON array. Each element:

```json
{
  "domain": "example.com",
  "available": false,
  "registrant_name": null,
  "registrant_org": null,
  "privacy_protected": null,
  "registrar": "RESERVED-Internet Assigned Numbers Authority",
  "creation_date": "1995-08-14T04:00:00Z",
  "expiry_date": "2026-08-13T04:00:00Z",
  "statuses": ["client delete prohibited", "client transfer prohibited", "client update prohibited"],
  "protocol_used": "rdap",
  "error": null
}
```

Field semantics:
- `available`: `true` (can register), `false` (registered), `null` (lookup failed — see `error`)
- `privacy_protected`: `true` (privacy proxy), `false` (registrant visible), `null` (can't determine)
- `registrant_name`/`registrant_org`: Real owner info when visible, `null` when redacted or unavailable
- `statuses`: Lowercase EPP status codes, space-separated words (e.g. `"client delete prohibited"`)
- `protocol_used`: `"rdap"` or `"whois"`
- `error`: `null` on success, human-readable string on failure
- `creation_date`/`expiry_date`: ISO 8601 strings or `null`

With `--verbose`, adds `"raw_response"` field (truncated to 5000 chars).

### CSV output

```bash
python cli.py example.com --format csv
```

Headers: `domain,available,registrant_name,registrant_org,privacy_protected,registrar,creation_date,expiry_date,statuses,protocol,error`

Statuses are pipe-delimited within the field: `client delete prohibited|client transfer prohibited`.

## Mode 2: Watch

Poll a single domain until it becomes available.

```bash
python cli.py expiring.com --watch 300
```

- Requires exactly one domain. Exits with error if multiple are given.
- Prints a timestamped line on each check.
- When domain becomes available: prints alert, sends desktop notification (Windows toast or notify-send), exits.
- Ctrl+C to stop.

**Stdout output per check (not machine-parseable — use for human monitoring):**
```
  #1  2026-03-27 01:55:22  not available  pendingdelete
  #2  2026-03-27 02:00:22  AVAILABLE — expiring.com is ready to register!
```

### Watch + register

```bash
python cli.py expiring.com --watch 300 --register --auto-register --max-price 15
```

When domain becomes available:
1. Checks price via Porkbun API
2. If price <= `--max-price`: registers automatically
3. If registration fails: sends notification, continues watching
4. If registration succeeds: sends notification, exits

Without `--auto-register`, prompts interactively on availability (user must be present).

## Mode 3: Suggest

Generate domain name candidates from a keyword and check availability.

```bash
python cli.py --suggest cloud
python cli.py --suggest cloud --tlds com,io,dev
python cli.py --suggest cloud --tlds com,io,dev --available-only
python cli.py --suggest cloud --available-only --format json 2>/dev/null
```

**Generation strategy** (in priority order):
1. Bare keyword across all TLDs: `cloud.com`, `cloud.io`, `cloud.dev`, ...
2. Prefix + keyword: `getcloud.com`, `trycloud.com`, `usecloud.com`, ...
3. Keyword + suffix: `cloudapp.com`, `cloudhq.com`, `cloudlab.com`, ...

Prefixes: get, try, use, go, my, the, hey, run, with, join, on
Suffixes: app, hq, hub, lab, labs, ify, ly, io, up, now, go, dev, ops, kit, ai, run, box, base, stack, pad

Default TLDs: com, io, dev, ai, co, app, net, org, sh, so, me, to, is, cc, xyz

**Note:** Progress bar and status messages go to stderr. JSON/CSV output goes to stdout. To pipe JSON cleanly: `--format json 2>/dev/null`.

**Results sorted by:** available first, then shortest domain first, then alphabetical.

### Suggest + register

```bash
python cli.py --suggest cloud --tlds dev --register
```

After showing results, presents a numbered list of available domains and prompts:
```
Enter numbers to register (e.g. 1,3,5) or Enter to skip:
```

With `--auto-register`, registers all available domains under `--max-price` without prompting.

## Registration

Registration uses the [Porkbun API](https://porkbun.com/api/json/v3/documentation). It is a modifier flag (`--register`) that works with all three modes.

### Prerequisites

1. Porkbun account with prepaid credit
2. At least one domain registered manually on Porkbun (API prerequisite)
3. API keys from porkbun.com/account/api

### Credential resolution (checked in order)

1. Environment variables: `PORKBUN_API_KEY`, `PORKBUN_SECRET_KEY`
2. Config file: `~/.config/domain_search/config.json`

Config file format:
```json
{"porkbun_api_key": "pk1_...", "porkbun_secret_key": "sk1_..."}
```

If no credentials are found, the tool prints setup instructions and exits.

### Registration flow

For each available domain when `--register` is active:

1. **Price check**: `GET pricing/get/{domain}` via Porkbun API
2. **Max price guard**: If price > `--max-price` (default $20), domain is skipped with warning
3. **Pricing failure guard**: If pricing API returns an error, registration is refused
4. **Confirmation**: Interactive `y/N` prompt unless `--auto-register` is set
5. **Registration**: `POST domain/register/{domain}` via Porkbun API
6. **Result**: Success message with price, or error message

### Porkbun rate limits

- 1 API request per 10 seconds (enforced by internal rate limiter, independent from RDAP/WHOIS rate limiter)
- Max 10 successful registrations per 24 hours

### Flag validation at startup

| Condition | Result |
|-----------|--------|
| `--auto-register` without `--register` | Error, exits |
| `--register` without credentials | Error with setup instructions, exits |
| `--register` with invalid credentials | Error after ping check, exits |
| `--watch --register` without `--auto-register` | Warning (user must be present), continues |

## Error handling

The tool does not throw exceptions to the caller. Errors are embedded in the output:

- **JSON**: `"error"` field is non-null
- **Table**: Yellow `?` in Available column, red error in Status column
- **CSV**: `error` column populated

Common errors:
- `"RDAP timeout"` — registry server didn't respond within timeout
- `"No RDAP endpoint for TLD: xx"` — RDAP not available, WHOIS used as fallback
- `"No WHOIS server found for TLD: xx"` — neither protocol available for this TLD
- `"WHOIS rate limited by server"` — server returned a rate limit response
- `"RDAP rate limited (429)"` — HTTP 429 from RDAP server after retry

## Interpreting domain statuses

Key EPP status codes and what they mean for availability:

| Status | Interpretation |
|--------|---------------|
| `pendingDelete` | Domain will be released in ~5 days. Use `--watch` to monitor. |
| `redemptionPeriod` | Expired but owner can still reclaim (~30 day window). |
| `autoRenewPeriod` | Recently expired, registrar may still renew (~0-45 days). |
| `clientTransferProhibited` | Normal lock, domain is actively maintained. |
| `serverHold` | Suspended by registry. May be reserved or policy-held. |
| `active` | Registered and resolving normally. |
| `inactive` | Registered but no nameservers configured. |

## Performance characteristics

| Operation | Typical time | Bottleneck |
|-----------|-------------|------------|
| Single domain check | 1-3s | RDAP/WHOIS response time |
| Bulk check (100 domains) | ~100s at 1 req/s | Per-server rate limit |
| Suggest (96 candidates, 3 TLDs) | ~30s | Distributed across servers |
| Porkbun price check | 10s | Porkbun rate limit (1 req/10s) |
| Porkbun registration | 10s | Porkbun rate limit |

Bulk checks run concurrently across different servers. The rate limiter is per-server, so checking domains across 5 different TLD servers runs ~5x faster than checking 5 domains on the same server.

## File structure

```
cli.py              Entry point. All user interaction.
checker.py          Orchestrator. RDAP first, WHOIS fallback.
rdap.py             RDAP client. IANA bootstrap caching.
whois_client.py     Raw TCP WHOIS (port 43). Heuristic parsing.
registrar.py        Porkbun API client. Pricing + registration.
config.py           Credential loading from env/file.
suggest.py          Domain name generation from keyword.
domain_parser.py    Input normalization via tldextract.
rate_limiter.py     Per-server async token bucket.
models.py           DomainResult, PricingResult, RegistrationResult.
constants.py        Server maps, patterns, timeouts, API URLs.
cache/              RDAP bootstrap cache (gitignored).
```
