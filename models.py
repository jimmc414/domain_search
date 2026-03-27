from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class DomainResult:
    domain: str
    available: bool | None = None  # None = couldn't determine
    registrar: str | None = None
    creation_date: str | None = None
    expiry_date: str | None = None
    statuses: list[str] = field(default_factory=list)
    protocol_used: str = ""  # "rdap" or "whois"
    raw_response: str | None = None
    error: str | None = None
