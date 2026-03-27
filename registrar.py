"""Porkbun domain registration API client."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import aiohttp

from constants import PORKBUN_API_BASE, PORKBUN_RATE_LIMIT
from models import PricingResult, RegistrationResult
from rate_limiter import RateLimiter

logger = logging.getLogger(__name__)

SERVER_KEY = "api.porkbun.com"


class PorkbunClient:
    """Porkbun domain registration API client.

    Uses its own rate limiter (1 req/10s) independent of RDAP/WHOIS.
    """

    def __init__(
        self,
        session: aiohttp.ClientSession,
        api_key: str,
        secret_key: str,
    ):
        self._session = session
        self._auth = {"apikey": api_key, "secretapikey": secret_key}
        self._rate_limiter = RateLimiter(rate=PORKBUN_RATE_LIMIT)
        self._bulk_pricing: dict[str, dict[str, str]] | None = None

    def __repr__(self) -> str:
        return "PorkbunClient(authenticated=True)"

    async def ping(self) -> bool:
        """Validate credentials via the Porkbun /ping endpoint."""
        url = f"{PORKBUN_API_BASE}/ping"
        try:
            await self._rate_limiter.acquire(SERVER_KEY)
            async with self._session.post(url, json=self._auth) as resp:
                data = await resp.json()
                if data.get("status") == "SUCCESS":
                    logger.debug("Porkbun ping successful")
                    return True
                logger.warning("Porkbun ping failed: %s", data.get("message", "unknown error"))
                return False
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            logger.warning("Porkbun ping error: %s", e)
            return False

    async def _load_bulk_pricing(self) -> None:
        """Load all TLD pricing in one API call. Cached for session lifetime."""
        if self._bulk_pricing is not None:
            return

        url = f"{PORKBUN_API_BASE}/pricing/get"
        try:
            await self._rate_limiter.acquire(SERVER_KEY)
            async with self._session.post(url, json=self._auth) as resp:
                data = await resp.json()
                if data.get("status") == "SUCCESS":
                    self._bulk_pricing = data.get("pricing", {})
                    logger.debug("Loaded Porkbun pricing for %d TLDs", len(self._bulk_pricing))
                else:
                    logger.warning("Porkbun bulk pricing failed: %s", data.get("message"))
                    self._bulk_pricing = {}
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            logger.warning("Porkbun bulk pricing error: %s", e)
            self._bulk_pricing = {}

    async def get_pricing(self, domain: str) -> PricingResult:
        """Get registration/renewal price for a domain via bulk pricing cache."""
        await self._load_bulk_pricing()

        # Extract TLD from domain
        parts = domain.rsplit(".", 1)
        if len(parts) != 2:
            return PricingResult(domain=domain, error=f"Cannot determine TLD for {domain}")

        tld = parts[1]

        # Check bulk pricing cache
        if self._bulk_pricing and tld in self._bulk_pricing:
            tld_pricing = self._bulk_pricing[tld]
            return PricingResult(
                domain=domain,
                registration_price=_parse_price(tld_pricing.get("registration")),
                renewal_price=_parse_price(tld_pricing.get("renewal")),
            )

        # Try second-level TLD (e.g. "co.uk")
        domain_parts = domain.split(".")
        if len(domain_parts) >= 3:
            sld_tld = ".".join(domain_parts[-2:])
            if self._bulk_pricing and sld_tld in self._bulk_pricing:
                tld_pricing = self._bulk_pricing[sld_tld]
                return PricingResult(
                    domain=domain,
                    registration_price=_parse_price(tld_pricing.get("registration")),
                    renewal_price=_parse_price(tld_pricing.get("renewal")),
                )

        return PricingResult(
            domain=domain,
            error=f"No Porkbun pricing available for .{tld} domains",
        )

    async def register(self, domain: str, years: int = 1) -> RegistrationResult:
        """Register a domain. Caller must confirm price first.

        Does NOT retry on failure — a 5xx doesn't guarantee the
        registration didn't succeed.
        """
        url = f"{PORKBUN_API_BASE}/domain/register/{domain}"
        body = {**self._auth, "years": years}

        try:
            await self._rate_limiter.acquire(SERVER_KEY)
            async with self._session.post(url, json=body) as resp:
                data = await resp.json()

                if data.get("status") == "SUCCESS":
                    return RegistrationResult(
                        domain=domain,
                        success=True,
                        message=data.get("message", "Domain registered successfully"),
                    )

                return RegistrationResult(
                    domain=domain,
                    success=False,
                    error=data.get("message", f"Registration failed (HTTP {resp.status})"),
                )

        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            return RegistrationResult(
                domain=domain,
                success=False,
                error=(
                    f"Connection error during registration: {e}. "
                    "Check your Porkbun dashboard — the registration may have succeeded."
                ),
            )


def _parse_price(value: Any) -> float | None:
    """Parse a price value from the Porkbun API."""
    if value is None:
        return None
    try:
        return float(value)
    except (ValueError, TypeError):
        return None
