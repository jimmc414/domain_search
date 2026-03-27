"""Registrar credential loading from environment variables or config file."""

from __future__ import annotations

import json
import logging
import os
import stat
from dataclasses import dataclass
from pathlib import Path

from constants import DEFAULT_MAX_PRICE

logger = logging.getLogger(__name__)

CONFIG_DIR = Path.home() / ".config" / "domain_search"
CONFIG_FILE = CONFIG_DIR / "config.json"


@dataclass
class RegistrarConfig:
    api_key: str
    secret_key: str
    max_price: float


def load_registrar_config(max_price: float | None = None) -> RegistrarConfig | None:
    """Load Porkbun credentials. Priority: env vars > config file.

    Returns None if no credentials found (caller should print help).
    """
    api_key = os.environ.get("PORKBUN_API_KEY")
    secret_key = os.environ.get("PORKBUN_SECRET_KEY")

    if api_key and secret_key:
        logger.debug("Loaded Porkbun credentials from environment variables")
        return RegistrarConfig(
            api_key=api_key,
            secret_key=secret_key,
            max_price=max_price or DEFAULT_MAX_PRICE,
        )

    # Try config file
    if CONFIG_FILE.exists():
        _check_permissions(CONFIG_FILE)
        try:
            data = json.loads(CONFIG_FILE.read_text())
            api_key = data.get("porkbun_api_key")
            secret_key = data.get("porkbun_secret_key")
            if api_key and secret_key:
                logger.debug("Loaded Porkbun credentials from %s", CONFIG_FILE)
                return RegistrarConfig(
                    api_key=api_key,
                    secret_key=secret_key,
                    max_price=max_price or DEFAULT_MAX_PRICE,
                )
        except (json.JSONDecodeError, KeyError) as e:
            logger.warning("Failed to parse %s: %s", CONFIG_FILE, e)

    return None


def _check_permissions(path: Path) -> None:
    """Warn if config file has overly permissive permissions."""
    try:
        mode = path.stat().st_mode
        if mode & (stat.S_IRGRP | stat.S_IROTH):
            logger.warning(
                "Config file %s is readable by other users. "
                "Run: chmod 600 %s",
                path,
                path,
            )
    except OSError:
        pass


def credentials_help() -> str:
    """Return help text for setting up credentials."""
    return (
        "Porkbun API credentials required for --register.\n"
        "\n"
        "Option 1: Environment variables (recommended)\n"
        '  export PORKBUN_API_KEY="pk1_..."\n'
        '  export PORKBUN_SECRET_KEY="sk1_..."\n'
        "\n"
        f"Option 2: Config file at {CONFIG_FILE}\n"
        '  {"porkbun_api_key": "pk1_...", "porkbun_secret_key": "sk1_..."}\n'
        "\n"
        "Get your API keys at: https://porkbun.com/account/api"
    )
