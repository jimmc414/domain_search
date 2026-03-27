from __future__ import annotations

import asyncio
import logging
from typing import AsyncIterator

import aiohttp

from domain_parser import parse_domain
from models import DomainResult
from rate_limiter import RateLimiter
from rdap import RDAPClient
from whois_client import WHOISClient

logger = logging.getLogger(__name__)


class DomainChecker:
    """Orchestrates domain availability checks: RDAP first, WHOIS fallback."""

    def __init__(self, rate: float = 1.0):
        self.rate = rate
        self.rate_limiter = RateLimiter(rate=rate)
        self._session: aiohttp.ClientSession | None = None
        self._rdap: RDAPClient | None = None
        self._whois: WHOISClient | None = None

    async def __aenter__(self) -> DomainChecker:
        self._session = aiohttp.ClientSession()
        self._rdap = RDAPClient(self._session, self.rate_limiter)
        self._whois = WHOISClient(self.rate_limiter)
        return self

    async def __aexit__(self, *exc: object) -> None:
        if self._session:
            await self._session.close()

    @property
    def session(self) -> aiohttp.ClientSession:
        """Expose session for registrar client reuse."""
        assert self._session is not None
        return self._session

    async def check(self, raw_domain: str) -> DomainResult:
        """Check a single domain's availability."""
        assert self._rdap is not None and self._whois is not None

        # Parse input
        try:
            domain, effective_tld = parse_domain(raw_domain)
        except ValueError as e:
            return DomainResult(
                domain=raw_domain,
                error=str(e),
            )

        # Try RDAP first
        result = await self._rdap.query(domain, effective_tld)

        # Fall back to WHOIS if RDAP failed (not just "not found")
        if result.error and "No RDAP endpoint" not in result.error:
            logger.debug(
                "RDAP failed for %s (%s), trying WHOIS",
                domain,
                result.error,
            )
            whois_result = await self._whois.query(domain, effective_tld)
            # Prefer WHOIS result if it actually got data
            if whois_result.error is None:
                return whois_result
            # If both failed, return the WHOIS error (more likely to be actionable)
            return whois_result

        if result.error and "No RDAP endpoint" in result.error:
            # No RDAP at all — WHOIS is the only option
            logger.debug("No RDAP for %s, using WHOIS", domain)
            return await self._whois.query(domain, effective_tld)

        return result

    async def check_bulk(
        self, domains: list[str]
    ) -> AsyncIterator[DomainResult]:
        """Check multiple domains, yielding results as they complete."""
        assert self._rdap is not None

        # Pre-load bootstrap once
        await self._rdap.load_bootstrap()

        tasks: dict[asyncio.Task[DomainResult], str] = {}
        for raw in domains:
            task = asyncio.create_task(self.check(raw))
            tasks[task] = raw

        for coro in asyncio.as_completed(tasks):
            result = await coro
            yield result
