"""Domain name suggestion engine — generates candidate domains from a keyword."""

from __future__ import annotations

# Popular TLDs ordered by desirability for tech/startup domains
DEFAULT_TLDS: list[str] = [
    "com", "io", "dev", "ai", "co", "app", "net", "org",
    "sh", "so", "me", "to", "is", "cc", "xyz",
]

# Common prefixes that create natural-sounding domain names
PREFIXES: list[str] = [
    "get", "try", "use", "go", "my", "the", "hey",
    "run", "with", "join", "on",
]

# Common suffixes that create natural-sounding domain names
SUFFIXES: list[str] = [
    "app", "hq", "hub", "lab", "labs", "ify", "ly",
    "io", "up", "now", "go", "dev", "ops", "kit",
    "ai", "run", "box", "base", "stack", "pad",
]


def generate_candidates(
    keyword: str,
    tlds: list[str] | None = None,
    max_length: int = 20,
    include_prefixes: bool = True,
    include_suffixes: bool = True,
    include_hyphens: bool = False,
) -> list[str]:
    """Generate candidate domain names from a keyword.

    Returns a deduplicated, ordered list of domain strings.
    Shorter and simpler names come first.
    """
    keyword = keyword.lower().strip().replace(" ", "")
    use_tlds = tlds or DEFAULT_TLDS
    seen: set[str] = set()
    candidates: list[str] = []

    def _add(name: str, tld: str) -> None:
        domain = f"{name}.{tld}"
        if domain not in seen and len(name) <= max_length and len(name) >= 2:
            seen.add(domain)
            candidates.append(domain)

    # Tier 1: bare keyword across all TLDs (highest priority)
    for tld in use_tlds:
        _add(keyword, tld)

    # Tier 2: prefix + keyword
    if include_prefixes:
        for prefix in PREFIXES:
            if prefix == keyword:
                continue
            for tld in use_tlds:
                _add(f"{prefix}{keyword}", tld)

    # Tier 3: keyword + suffix
    if include_suffixes:
        for suffix in SUFFIXES:
            if suffix == keyword:
                continue
            for tld in use_tlds:
                _add(f"{keyword}{suffix}", tld)

    # Tier 4: hyphenated variants (less common but sometimes available)
    if include_hyphens:
        for prefix in PREFIXES:
            if prefix == keyword:
                continue
            for tld in use_tlds:
                _add(f"{prefix}-{keyword}", tld)
        for suffix in SUFFIXES:
            if suffix == keyword:
                continue
            for tld in use_tlds:
                _add(f"{keyword}-{suffix}", tld)

    return candidates
