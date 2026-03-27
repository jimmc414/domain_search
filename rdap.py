from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

import aiohttp

from constants import (
    BOOTSTRAP_CACHE_TTL_HOURS,
    BOOTSTRAP_URL,
    CONNECT_TIMEOUT,
    PRIVACY_PATTERNS,
    RDAP_FALLBACK,
    READ_TIMEOUT,
)
from domain_parser import tld_variants
from models import DomainResult
from rate_limiter import RateLimiter

logger = logging.getLogger(__name__)

CACHE_DIR = Path(__file__).parent / "cache"
BOOTSTRAP_CACHE = CACHE_DIR / "rdap_bootstrap.json"


def _is_redacted(value: str) -> bool:
    """Check if a value appears to be a privacy redaction placeholder."""
    val = value.strip().lower()
    if not val:
        return True
    for pattern in PRIVACY_PATTERNS:
        if pattern in val:
            return True
    return False


def _normalize_status(status: str) -> str:
    """Normalize RDAP status values.

    Handles both "client delete prohibited" and "clientDeleteProhibited".
    Returns the spaced form for consistency.
    """
    if " " not in status and any(c.isupper() for c in status[1:]):
        parts = re.findall(r"[a-z]+|[A-Z][a-z]*", status)
        return " ".join(p.lower() for p in parts)
    return status.lower()


def _extract_host(url: str) -> str:
    """Extract hostname from a URL for rate limiting."""
    parsed = urlparse(url)
    return parsed.hostname or url


class RDAPClient:
    """RDAP client with IANA bootstrap loading and caching."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        rate_limiter: RateLimiter,
    ):
        self.session = session
        self.rate_limiter = rate_limiter
        self._bootstrap: dict[str, list[str]] = {}
        self._bootstrap_loaded = False

    async def load_bootstrap(self) -> None:
        """Load the IANA RDAP bootstrap file, using cache if fresh."""
        if self._bootstrap_loaded:
            return

        # Try cache first
        if BOOTSTRAP_CACHE.exists():
            try:
                data = json.loads(BOOTSTRAP_CACHE.read_text())
                cached_at = data.get("_cached_at", 0)
                age_hours = (time.time() - cached_at) / 3600
                if age_hours < BOOTSTRAP_CACHE_TTL_HOURS:
                    self._bootstrap = data.get("mapping", {})
                    self._bootstrap_loaded = True
                    logger.debug(
                        "Loaded RDAP bootstrap from cache (%.1fh old)", age_hours
                    )
                    return
            except (json.JSONDecodeError, KeyError):
                pass

        # Fetch fresh
        try:
            timeout = aiohttp.ClientTimeout(
                sock_connect=CONNECT_TIMEOUT, sock_read=READ_TIMEOUT
            )
            async with self.session.get(
                BOOTSTRAP_URL, timeout=timeout
            ) as resp:
                if resp.status == 200:
                    raw = await resp.json()
                    self._bootstrap = self._parse_bootstrap(raw)
                    self._save_cache()
                    self._bootstrap_loaded = True
                    logger.debug(
                        "Fetched fresh RDAP bootstrap (%d TLDs)",
                        len(self._bootstrap),
                    )
                else:
                    logger.warning(
                        "Failed to fetch RDAP bootstrap: HTTP %d", resp.status
                    )
        except Exception as e:
            logger.warning("Failed to fetch RDAP bootstrap: %s", e)

        # Fall back to cache even if stale
        if not self._bootstrap_loaded and BOOTSTRAP_CACHE.exists():
            try:
                data = json.loads(BOOTSTRAP_CACHE.read_text())
                self._bootstrap = data.get("mapping", {})
                self._bootstrap_loaded = True
                logger.debug("Using stale RDAP bootstrap cache as fallback")
            except (json.JSONDecodeError, KeyError):
                pass

        self._bootstrap_loaded = True  # Don't retry on every call

    def _parse_bootstrap(self, raw: dict[str, Any]) -> dict[str, list[str]]:
        """Parse IANA bootstrap JSON into {tld: [urls]} mapping."""
        mapping: dict[str, list[str]] = {}
        for service in raw.get("services", []):
            if len(service) < 2:
                continue
            tlds, urls = service[0], service[1]
            for tld in tlds:
                mapping[tld.lower()] = urls
        return mapping

    def _save_cache(self) -> None:
        """Save bootstrap mapping to cache file."""
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        data = {"_cached_at": time.time(), "mapping": self._bootstrap}
        BOOTSTRAP_CACHE.write_text(json.dumps(data))

    def get_endpoint(self, effective_tld: str) -> str | None:
        """Find RDAP endpoint URL for a TLD.

        Tries the full effective TLD first (e.g. "co.uk"), then parent
        levels ("uk"), then the fallback map.
        """
        for variant in tld_variants(effective_tld):
            if variant in self._bootstrap:
                urls = self._bootstrap[variant]
                if urls:
                    return urls[0]

        # Check fallback map
        for variant in tld_variants(effective_tld):
            if variant in RDAP_FALLBACK:
                return RDAP_FALLBACK[variant]

        return None

    async def query(self, domain: str, effective_tld: str) -> DomainResult:
        """Query RDAP for a domain.

        Returns DomainResult. On failure, sets error field so the caller
        can decide whether to fall back to WHOIS.
        """
        await self.load_bootstrap()

        endpoint = self.get_endpoint(effective_tld)
        if not endpoint:
            return DomainResult(
                domain=domain,
                protocol_used="rdap",
                error=f"No RDAP endpoint for TLD: {effective_tld}",
            )

        # Build URL
        url = urljoin(endpoint.rstrip("/") + "/", f"domain/{domain}")

        # Rate limit
        server = _extract_host(endpoint)
        waited = await self.rate_limiter.acquire(server)
        if waited > 0:
            logger.debug("Rate limited %.2fs for %s", waited, server)

        # Query with retry on 429
        for attempt in range(2):
            try:
                timeout = aiohttp.ClientTimeout(
                    sock_connect=CONNECT_TIMEOUT, sock_read=READ_TIMEOUT
                )
                async with self.session.get(url, timeout=timeout) as resp:
                    if resp.status == 404:
                        return DomainResult(
                            domain=domain,
                            available=True,
                            protocol_used="rdap",
                        )

                    if resp.status == 429:
                        if attempt == 0:
                            wait = 2 ** (attempt + 1)
                            logger.debug(
                                "RDAP 429 for %s, retrying in %ds",
                                domain,
                                wait,
                            )
                            await self.rate_limiter.acquire(server)
                            continue
                        return DomainResult(
                            domain=domain,
                            protocol_used="rdap",
                            error="RDAP rate limited (429)",
                        )

                    if resp.status == 200:
                        body = await resp.text()
                        try:
                            data = json.loads(body)
                        except json.JSONDecodeError:
                            return DomainResult(
                                domain=domain,
                                protocol_used="rdap",
                                error="RDAP returned invalid JSON",
                                raw_response=body[:2000],
                            )

                        # Some registries return 200 with errorCode 404
                        if data.get("errorCode") == 404:
                            return DomainResult(
                                domain=domain,
                                available=True,
                                protocol_used="rdap",
                            )

                        return self._parse_response(domain, data, body)

                    # Other error status
                    return DomainResult(
                        domain=domain,
                        protocol_used="rdap",
                        error=f"RDAP HTTP {resp.status}",
                    )

            except asyncio.TimeoutError:
                return DomainResult(
                    domain=domain,
                    protocol_used="rdap",
                    error="RDAP timeout",
                )
            except aiohttp.ClientError as e:
                return DomainResult(
                    domain=domain,
                    protocol_used="rdap",
                    error=f"RDAP connection error: {e}",
                )

        return DomainResult(
            domain=domain,
            protocol_used="rdap",
            error="RDAP query failed after retries",
        )

    def _parse_response(
        self, domain: str, data: dict[str, Any], raw: str
    ) -> DomainResult:
        """Parse a successful RDAP JSON response."""
        statuses = [
            _normalize_status(s) for s in data.get("status", [])
        ]

        registrar = self._extract_entity_name(data, "registrar")
        registrant_name = self._extract_entity_name(data, "registrant")
        registrant_org = self._extract_entity_org(data, "registrant")

        # Detect privacy protection
        privacy = self._detect_privacy(data, registrant_name, registrant_org)

        creation_date = None
        expiry_date = None

        for event in data.get("events", []):
            action = event.get("eventAction", "")
            date = event.get("eventDate", "")
            if action == "registration":
                creation_date = date
            elif action == "expiration":
                expiry_date = date

        return DomainResult(
            domain=domain,
            available=False,
            registrar=registrar,
            registrant_name=registrant_name,
            registrant_org=registrant_org,
            privacy_protected=privacy,
            creation_date=creation_date,
            expiry_date=expiry_date,
            statuses=statuses,
            protocol_used="rdap",
            raw_response=raw[:5000],
        )

    def _extract_entity_name(
        self, data: dict[str, Any], role: str
    ) -> str | None:
        """Extract entity name (fn) from RDAP entities by role."""
        for entity in data.get("entities", []):
            roles = entity.get("roles", [])
            if role in roles:
                vcard = entity.get("vcardArray")
                if vcard and len(vcard) > 1:
                    for field in vcard[1]:
                        if field[0] == "fn":
                            val = field[3]
                            if val and not _is_redacted(val):
                                return val
                handle = entity.get("handle")
                if handle and not _is_redacted(handle):
                    return handle
        return None

    def _extract_entity_org(
        self, data: dict[str, Any], role: str
    ) -> str | None:
        """Extract entity org from RDAP entities by role."""
        for entity in data.get("entities", []):
            roles = entity.get("roles", [])
            if role in roles:
                vcard = entity.get("vcardArray")
                if vcard and len(vcard) > 1:
                    for field in vcard[1]:
                        if field[0] == "org":
                            val = field[3]
                            if val and not _is_redacted(val):
                                return val
        return None

    def _detect_privacy(
        self,
        data: dict[str, Any],
        registrant_name: str | None,
        registrant_org: str | None,
    ) -> bool | None:
        """Detect if domain registration uses privacy protection.

        Returns True if privacy proxy detected, False if registrant data
        is clearly visible, None if can't determine.
        """
        # Check if any entity with registrant role has redacted remarks
        for entity in data.get("entities", []):
            roles = entity.get("roles", [])
            if "registrant" in roles:
                # Check remarks for redaction notices
                for remark in entity.get("remarks", []):
                    title = (remark.get("title") or "").lower()
                    desc = " ".join(remark.get("description", [])).lower()
                    if "redact" in title or "privacy" in title:
                        return True
                    if "redact" in desc or "privacy" in desc:
                        return True

                # Check if vcard data is redacted
                vcard = entity.get("vcardArray")
                if vcard and len(vcard) > 1:
                    for field in vcard[1]:
                        if field[0] == "fn":
                            if _is_redacted(str(field[3])):
                                return True

                # If we found a registrant entity with real data, not private
                if registrant_name or registrant_org:
                    return False

                # Registrant entity exists but no usable data
                return True

        # No registrant entity at all — most thick registries include one,
        # so absence likely means redacted at the registry level
        return None


