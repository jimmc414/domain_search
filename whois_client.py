from __future__ import annotations

import asyncio
import logging
import re

from constants import (
    AVAILABLE_PATTERNS,
    CONNECT_TIMEOUT,
    RATE_LIMIT_PATTERNS,
    READ_TIMEOUT,
    WHOIS_SERVERS,
)
from models import DomainResult
from rate_limiter import RateLimiter

logger = logging.getLogger(__name__)

# Cache for IANA WHOIS server discovery
_iana_cache: dict[str, str | None] = {}


class WHOISClient:
    """Raw async TCP WHOIS client (RFC 3912)."""

    def __init__(self, rate_limiter: RateLimiter):
        self.rate_limiter = rate_limiter

    async def query(self, domain: str, effective_tld: str) -> DomainResult:
        """Query WHOIS for a domain."""
        # Find the WHOIS server
        server, query_fmt = await self._resolve_server(effective_tld)
        if not server:
            return DomainResult(
                domain=domain,
                protocol_used="whois",
                error=f"No WHOIS server found for TLD: {effective_tld}",
            )

        # Rate limit
        waited = await self.rate_limiter.acquire(server)
        if waited > 0:
            logger.debug("Rate limited %.2fs for %s", waited, server)

        # Build query string
        query_str = query_fmt.format(domain=domain)

        # Execute WHOIS query
        try:
            raw = await self._raw_query(server, query_str)
        except asyncio.TimeoutError:
            return DomainResult(
                domain=domain,
                protocol_used="whois",
                error=f"WHOIS timeout connecting to {server}",
            )
        except OSError as e:
            return DomainResult(
                domain=domain,
                protocol_used="whois",
                error=f"WHOIS connection error ({server}): {e}",
            )

        if not raw or not raw.strip():
            return DomainResult(
                domain=domain,
                protocol_used="whois",
                error=f"Empty response from {server}",
            )

        # Check for rate limiting
        raw_lower = raw.lower()
        for pattern in RATE_LIMIT_PATTERNS:
            if pattern in raw_lower:
                return DomainResult(
                    domain=domain,
                    protocol_used="whois",
                    error=f"WHOIS rate limited by {server}",
                    raw_response=raw[:2000],
                )

        # Check availability
        if self._is_available(raw_lower):
            return DomainResult(
                domain=domain,
                available=True,
                protocol_used="whois",
                raw_response=raw[:2000],
            )

        # Domain is registered — parse details
        return self._parse_registration(domain, raw)

    async def _resolve_server(
        self, effective_tld: str
    ) -> tuple[str | None, str]:
        """Find WHOIS server for a TLD.

        Checks hardcoded map first, then queries whois.iana.org.
        """
        # Check hardcoded map — try full TLD first, then parent levels
        parts = effective_tld.split(".")
        for i in range(len(parts)):
            tld = ".".join(parts[i:])
            if tld in WHOIS_SERVERS:
                server, fmt = WHOIS_SERVERS[tld]
                return server, fmt

        # Query IANA for the base TLD
        base_tld = parts[-1]
        if base_tld in _iana_cache:
            server = _iana_cache[base_tld]
            if server:
                return server, "{domain}"
            return None, "{domain}"

        try:
            raw = await self._raw_query("whois.iana.org", base_tld)
            server = self._parse_iana_refer(raw)
            _iana_cache[base_tld] = server
            if server:
                return server, "{domain}"
        except Exception as e:
            logger.warning("IANA WHOIS lookup failed for %s: %s", base_tld, e)
            _iana_cache[base_tld] = None

        return None, "{domain}"

    async def _raw_query(self, server: str, query: str) -> str:
        """Send a raw WHOIS query over TCP port 43."""
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(server, 43),
            timeout=CONNECT_TIMEOUT,
        )
        try:
            writer.write(f"{query}\r\n".encode())
            await writer.drain()

            data = await asyncio.wait_for(
                reader.read(65536),
                timeout=READ_TIMEOUT,
            )
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass

        # Try UTF-8, fall back to Latin-1
        try:
            return data.decode("utf-8")
        except UnicodeDecodeError:
            return data.decode("latin-1", errors="replace")

    def _parse_iana_refer(self, raw: str) -> str | None:
        """Extract 'refer:' field from IANA WHOIS response."""
        for line in raw.splitlines():
            if line.lower().startswith("refer:"):
                server = line.split(":", 1)[1].strip()
                if server:
                    return server
        return None

    def _is_available(self, raw_lower: str) -> bool:
        """Check WHOIS response against availability patterns."""
        for pattern in AVAILABLE_PATTERNS:
            if pattern in raw_lower:
                return True
        return False

    def _parse_registration(self, domain: str, raw: str) -> DomainResult:
        """Parse registration details from WHOIS response."""
        registrar = self._extract_field(
            raw, [r"Registrar:\s*(.+)", r"registrar name:\s*(.+)"]
        )
        creation_date = self._extract_field(
            raw,
            [
                r"Creation Date:\s*(.+)",
                r"Created:\s*(.+)",
                r"Registration Date:\s*(.+)",
                r"created:\s*(.+)",
                r"Registered on:\s*(.+)",
            ],
        )
        expiry_date = self._extract_field(
            raw,
            [
                r"Registry Expiry Date:\s*(.+)",
                r"Expir(?:y|ation) Date:\s*(.+)",
                r"Expiry date:\s*(.+)",
                r"paid-till:\s*(.+)",
                r"Renewal date:\s*(.+)",
            ],
        )

        statuses: list[str] = []
        for match in re.finditer(
            r"Domain Status:\s*(\S+)", raw, re.IGNORECASE
        ):
            statuses.append(match.group(1).lower())

        return DomainResult(
            domain=domain,
            available=False,
            registrar=registrar,
            creation_date=creation_date,
            expiry_date=expiry_date,
            statuses=statuses,
            protocol_used="whois",
            raw_response=raw[:5000],
        )

    def _extract_field(
        self, raw: str, patterns: list[str]
    ) -> str | None:
        """Try multiple regex patterns against the WHOIS response."""
        for pattern in patterns:
            match = re.search(pattern, raw, re.IGNORECASE)
            if match:
                value = match.group(1).strip()
                if value:
                    return value
        return None
