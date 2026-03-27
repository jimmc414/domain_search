from __future__ import annotations

from urllib.parse import urlparse

import tldextract


def parse_domain(raw: str) -> tuple[str, str]:
    """Parse raw input into (registrable_domain, effective_tld).

    Handles URLs, subdomains, IDN domains, and second-level TLDs.
    Raises ValueError for invalid input.

    Returns:
        (registrable_domain, effective_tld) e.g. ("example.co.uk", "co.uk")
    """
    raw = raw.strip().lower()

    # Strip protocol if user passed a URL
    if "://" in raw:
        parsed = urlparse(raw)
        raw = parsed.hostname or raw
    elif raw.startswith("//"):
        parsed = urlparse("https:" + raw)
        raw = parsed.hostname or raw

    # Strip trailing dot (FQDN notation)
    raw = raw.rstrip(".")

    # Strip trailing slash
    raw = raw.rstrip("/")

    # Reject IPs
    if _is_ip(raw):
        raise ValueError(f"IP addresses are not domain names: {raw}")

    # Convert IDN to punycode for querying
    try:
        raw = raw.encode("idna").decode("ascii")
    except (UnicodeError, UnicodeDecodeError):
        # Already ASCII or mixed — tldextract will handle it
        pass

    extracted = tldextract.extract(raw)

    if not extracted.domain:
        raise ValueError(f"No registrable domain found in: {raw}")
    if not extracted.suffix:
        raise ValueError(f"No valid TLD found in: {raw}")

    registrable = extracted.registered_domain  # e.g. "example.co.uk"
    effective_tld = extracted.suffix  # e.g. "co.uk"

    return registrable, effective_tld


def tld_variants(effective_tld: str) -> list[str]:
    """Return TLD variants to try for RDAP bootstrap lookup.

    For "co.uk" returns ["co.uk", "uk"].
    For "com" returns ["com"].
    """
    parts = effective_tld.split(".")
    variants = []
    for i in range(len(parts)):
        variants.append(".".join(parts[i:]))
    return variants


def _is_ip(s: str) -> bool:
    """Check if string looks like an IPv4 or IPv6 address."""
    # IPv4
    parts = s.split(".")
    if len(parts) == 4:
        try:
            return all(0 <= int(p) <= 255 for p in parts)
        except ValueError:
            pass
    # IPv6
    if ":" in s and "." not in s:
        return True
    # Bracketed IPv6
    if s.startswith("[") and s.endswith("]"):
        return True
    return False
