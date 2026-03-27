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

    def __repr__(self) -> str:
        return "PorkbunClient(authenticated=True)"

    async def ping(self) -> bool:
        """Validate credentials by checking pricing for a known domain.

        The Porkbun /ping endpoint doesn't reliably validate API keys,
        so we use a pricing call against a known domain instead.
        """
        pricing = await self.get_pricing("google.com")
        if pricing.error:
            logger.warning("Porkbun auth check failed: %s", pricing.error)
            return False
        logger.debug("Porkbun credentials validated via pricing check")
        return True

    async def get_pricing(self, domain: str) -> PricingResult:
        """Get registration/renewal price for a domain."""
        url = f"{PORKBUN_API_BASE}/pricing/get/{domain}"

        for attempt in range(2):
            try:
                await self._rate_limiter.acquire(SERVER_KEY)
                async with self._session.post(url, json=self._auth) as resp:
                    data = await resp.json()

                    if data.get("status") == "SUCCESS":
                        pricing = data.get("pricing", {})
                        reg_price = _parse_price(pricing.get("registration"))
                        renew_price = _parse_price(pricing.get("renewal"))
                        return PricingResult(
                            domain=domain,
                            registration_price=reg_price,
                            renewal_price=renew_price,
                        )

                    return PricingResult(
                        domain=domain,
                        error=data.get("message", f"Porkbun pricing error (HTTP {resp.status})"),
                    )

            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                if attempt == 0:
                    logger.debug("Pricing retry for %s: %s", domain, e)
                    continue
                return PricingResult(domain=domain, error=f"Porkbun connection error: {e}")

        return PricingResult(domain=domain, error="Porkbun pricing failed after retries")

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
