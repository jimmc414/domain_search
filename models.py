from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class DomainResult:
    domain: str
    available: bool | None = None  # None = couldn't determine
    registrar: str | None = None
    registrant_name: str | None = None
    registrant_org: str | None = None
    privacy_protected: bool | None = None  # True = behind privacy proxy
    creation_date: str | None = None
    expiry_date: str | None = None
    statuses: list[str] = field(default_factory=list)
    protocol_used: str = ""  # "rdap" or "whois"
    raw_response: str | None = None
    error: str | None = None


@dataclass
class PricingResult:
    domain: str
    registration_price: float | None = None  # USD
    renewal_price: float | None = None  # USD
    error: str | None = None


@dataclass
class RegistrationResult:
    domain: str
    success: bool = False
    message: str = ""
    price_paid: float | None = None
    error: str | None = None
