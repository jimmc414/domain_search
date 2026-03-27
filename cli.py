#!/usr/bin/env python3
"""Domain availability checker — queries RDAP and WHOIS directly."""

from __future__ import annotations

import argparse
import asyncio
import csv
import io
import json
import logging
import subprocess
import sys
from datetime import datetime, timedelta

from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, MofNCompleteColumn
from rich.prompt import Confirm, Prompt
from rich.table import Table

from checker import DomainChecker
from models import DomainResult

console = Console()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Check domain name availability via RDAP/WHOIS",
        epilog="Examples:\n"
        "  python cli.py example.com\n"
        "  python cli.py example.com example.org foo.co.uk\n"
        "  python cli.py --file domains.txt --format json\n"
        "  python cli.py --suggest cloud\n"
        "  python cli.py --suggest cloud --tlds com,io,dev\n"
        "  python cli.py coolstartup.dev --register\n"
        '  python cli.py expiring.com --watch 300 --register --auto-register --max-price 15\n',
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "domains",
        nargs="*",
        help="Domain name(s) to check",
    )
    parser.add_argument(
        "--file", "-f",
        type=str,
        help="File with domain names (one per line)",
    )
    parser.add_argument(
        "--format",
        choices=["table", "json", "csv"],
        default="table",
        help="Output format (default: table)",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Show raw RDAP/WHOIS responses",
    )
    parser.add_argument(
        "--rate", "-r",
        type=float,
        default=1.0,
        help="Queries per second per server (default: 1)",
    )
    parser.add_argument(
        "--timeout", "-t",
        type=float,
        default=10.0,
        help="Per-query timeout in seconds (default: 10)",
    )
    parser.add_argument(
        "--watch", "-w",
        type=int,
        metavar="SECONDS",
        help="Watch mode: re-check every N seconds until available, then alert (e.g. --watch 300)",
    )
    parser.add_argument(
        "--suggest", "-s",
        type=str,
        metavar="KEYWORD",
        help="Generate and check domain names from a keyword (e.g. --suggest cloud)",
    )
    parser.add_argument(
        "--tlds",
        type=str,
        help="Comma-separated TLDs for --suggest (default: com,io,dev,ai,co,app,...)",
    )
    parser.add_argument(
        "--available-only",
        action="store_true",
        help="With --suggest, only show available domains",
    )
    # Registration flags
    parser.add_argument(
        "--register",
        action="store_true",
        help="Enable domain registration via Porkbun for available domains",
    )
    parser.add_argument(
        "--auto-register",
        action="store_true",
        help="Skip confirmation prompt (required for unattended --watch + --register)",
    )
    parser.add_argument(
        "--max-price",
        type=float,
        default=None,
        help="Max registration price in USD (default: 20.00, protects against premium domains)",
    )

    args = parser.parse_args()

    # Collect domains
    domains: list[str] = list(args.domains or [])
    if args.file:
        try:
            with open(args.file) as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#"):
                        domains.append(line)
        except FileNotFoundError:
            console.print(f"[red]File not found: {args.file}[/red]")
            sys.exit(1)

    # Configure logging
    if args.verbose:
        logging.basicConfig(
            level=logging.DEBUG,
            format="%(name)s: %(message)s",
        )

    # Validate registration flags
    if args.auto_register and not args.register:
        console.print("[red]--auto-register requires --register[/red]")
        sys.exit(1)

    # Load registrar config if needed
    registrar_config = None
    if args.register:
        from config import load_registrar_config, credentials_help
        registrar_config = load_registrar_config(max_price=args.max_price)
        if not registrar_config:
            console.print(f"[red]{credentials_help()}[/red]")
            sys.exit(1)

    if not domains and not args.suggest:
        parser.print_help()
        sys.exit(1)

    # Suggest mode
    if args.suggest:
        tlds = args.tlds.split(",") if args.tlds else None
        asyncio.run(_suggest(
            args.suggest, tlds, args.rate, args.format, args.verbose,
            args.available_only, registrar_config, args.auto_register,
        ))
        return

    # Watch mode
    if args.watch:
        if len(domains) != 1:
            console.print("[red]--watch only supports a single domain[/red]")
            sys.exit(1)
        if args.register and not args.auto_register:
            console.print(
                "[yellow]Note: --watch + --register without --auto-register "
                "means you must be present to confirm registration.[/yellow]\n"
            )
        asyncio.run(_watch(
            domains[0], args.watch, args.rate,
            registrar_config=registrar_config,
            auto_register=args.auto_register,
        ))
        return

    # Run
    results = asyncio.run(_run(domains, args.rate))

    # Output
    if args.format == "json":
        _output_json(results, args.verbose)
    elif args.format == "csv":
        _output_csv(results)
    else:
        _output_table(results, args.verbose)

    # Offer registration for available domains
    if registrar_config:
        available = [r for r in results if r.available is True]
        if available:
            asyncio.run(_offer_registration(
                available, registrar_config, args.auto_register,
            ))


async def _run(domains: list[str], rate: float) -> list[DomainResult]:
    """Run domain checks with progress display."""
    results: list[DomainResult] = []

    async with DomainChecker(rate=rate) as checker:
        if len(domains) == 1:
            with console.status(f"Checking {domains[0]}..."):
                result = await checker.check(domains[0])
            results.append(result)
        else:
            with Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                BarColumn(),
                MofNCompleteColumn(),
                console=console,
            ) as progress:
                task = progress.add_task(
                    "Checking domains", total=len(domains)
                )
                async for result in checker.check_bulk(domains):
                    results.append(result)
                    progress.update(task, advance=1)

    domain_order = {d.lower(): i for i, d in enumerate(domains)}
    results.sort(
        key=lambda r: domain_order.get(r.domain.lower(), len(domains))
    )
    return results


# ---------------------------------------------------------------------------
# Registration helpers
# ---------------------------------------------------------------------------

async def _init_registrar(session, config):
    """Create and validate a PorkbunClient."""
    from registrar import PorkbunClient
    client = PorkbunClient(session, config.api_key, config.secret_key)
    with console.status("Validating Porkbun credentials..."):
        ok = await client.ping()
    if not ok:
        console.print("[red]Porkbun authentication failed. Check your API key and secret.[/red]")
        return None
    console.print("[green]Porkbun credentials verified.[/green]\n")
    return client


async def _try_register(client, domain: str, max_price: float, auto: bool) -> bool:
    """Price check + optional register for a single domain. Returns True if registered."""
    # Price check
    pricing = await client.get_pricing(domain)
    if pricing.error:
        console.print(f"  [red]Could not get pricing for {domain}: {pricing.error}[/red]")
        return False

    price = pricing.registration_price
    if price is None:
        console.print(f"  [red]No pricing available for {domain}[/red]")
        return False

    renewal = pricing.renewal_price
    price_info = f"${price:.2f}/yr"
    if renewal and renewal != price:
        price_info += f" (renews at ${renewal:.2f}/yr)"

    # Max price guard
    if price > max_price:
        console.print(
            f"  [yellow]{domain} costs {price_info} — exceeds --max-price ${max_price:.2f}, skipping[/yellow]"
        )
        return False

    # Confirm
    if not auto:
        confirmed = Confirm.ask(
            f"  Register [cyan]{domain}[/cyan] for {price_info} via Porkbun?",
            default=False,
        )
        if not confirmed:
            return False
    else:
        console.print(f"  Auto-registering [cyan]{domain}[/cyan] for {price_info}...")

    # Register
    result = await client.register(domain)
    if result.success:
        console.print(f"  [bold green]Registered {domain} for {price_info} via Porkbun[/bold green]")
        return True
    else:
        console.print(f"  [red]Registration failed for {domain}: {result.error}[/red]")
        return False


async def _offer_registration(available, config, auto_register):
    """Offer to register available domains after a normal check."""
    import aiohttp
    async with aiohttp.ClientSession() as session:
        client = await _init_registrar(session, config)
        if not client:
            return

        console.print("[bold]Registration:[/bold]")
        for r in available:
            await _try_register(client, r.domain, config.max_price, auto_register)


# ---------------------------------------------------------------------------
# Watch mode
# ---------------------------------------------------------------------------

async def _watch(
    domain: str,
    interval: int,
    rate: float,
    registrar_config=None,
    auto_register: bool = False,
) -> None:
    """Poll a domain until it becomes available, then alert (and optionally register)."""
    console.print(
        f"[bold]Watching [cyan]{domain}[/cyan] every "
        f"{_format_interval(interval)}. Ctrl+C to stop.[/bold]"
    )
    if registrar_config:
        mode = "auto-register" if auto_register else "prompt to register"
        console.print(
            f"[bold]Registration enabled ({mode}, max ${registrar_config.max_price:.2f})[/bold]"
        )
    console.print()

    check_num = 0
    async with DomainChecker(rate=rate) as checker:
        # Validate registrar credentials upfront
        registrar_client = None
        if registrar_config:
            from registrar import PorkbunClient
            registrar_client = PorkbunClient(
                checker.session, registrar_config.api_key, registrar_config.secret_key
            )
            with console.status("Validating Porkbun credentials..."):
                ok = await registrar_client.ping()
            if not ok:
                console.print("[red]Porkbun authentication failed. Watching without registration.[/red]\n")
                registrar_client = None
            else:
                console.print("[green]Porkbun credentials verified.[/green]\n")

        try:
            while True:
                check_num += 1
                now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                result = await checker.check(domain)

                status_parts = []
                if result.error:
                    status_parts.append(f"[yellow]error: {result.error}[/yellow]")
                elif result.available is True:
                    console.print(
                        f"  [bold green]#{check_num}  {now}  AVAILABLE — {domain} is ready to register![/bold green]"
                    )
                    console.print()
                    print("\a", end="", flush=True)
                    _send_notification(domain)

                    # Try registration
                    if registrar_client and registrar_config:
                        registered = await _try_register(
                            registrar_client, domain, registrar_config.max_price, auto_register
                        )
                        if registered:
                            return
                        # Registration failed — continue watching
                        console.print("  [yellow]Continuing to watch...[/yellow]\n")
                    else:
                        return
                else:
                    tag = ", ".join(result.statuses[:2]) if result.statuses else "registered"
                    status_parts.append(f"[dim]{tag}[/dim]")

                if status_parts:
                    console.print(
                        f"  [dim]#{check_num}  {now}[/dim]  [red]not available[/red]  {' '.join(status_parts)}"
                    )
                await asyncio.sleep(interval)
        except KeyboardInterrupt:
            console.print(f"\n[bold]Stopped watching {domain}.[/bold]")


def _format_interval(seconds: int) -> str:
    """Human-readable interval string."""
    if seconds >= 3600 and seconds % 3600 == 0:
        return f"{seconds // 3600}h"
    if seconds >= 60 and seconds % 60 == 0:
        return f"{seconds // 60}m"
    return f"{seconds}s"


def _send_notification(domain: str) -> None:
    """Send a desktop notification (best-effort, WSL2 + Linux)."""
    title = "Domain Available!"
    body = f"{domain} is available for registration"

    try:
        ps_cmd = (
            f"[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, "
            f"ContentType = WindowsRuntime] > $null; "
            f"$template = [Windows.UI.Notifications.ToastNotificationManager]::"
            f"GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]::ToastText02); "
            f"$text = $template.GetElementsByTagName('text'); "
            f"$text.Item(0).AppendChild($template.CreateTextNode('{title}')) > $null; "
            f"$text.Item(1).AppendChild($template.CreateTextNode('{body}')) > $null; "
            f"$toast = [Windows.UI.Notifications.ToastNotification]::new($template); "
            f"[Windows.UI.Notifications.ToastNotificationManager]::"
            f"CreateToastNotifier('Domain Search').Show($toast)"
        )
        subprocess.Popen(
            ["powershell.exe", "-Command", ps_cmd],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return
    except FileNotFoundError:
        pass

    try:
        subprocess.Popen(
            ["notify-send", "--urgency=critical", title, body],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except FileNotFoundError:
        pass


# ---------------------------------------------------------------------------
# Suggest mode
# ---------------------------------------------------------------------------

async def _suggest(
    keyword: str,
    tlds: list[str] | None,
    rate: float,
    fmt: str,
    verbose: bool,
    available_only: bool,
    registrar_config=None,
    auto_register: bool = False,
) -> None:
    """Generate domain name candidates and check availability."""
    from suggest import generate_candidates

    candidates = generate_candidates(keyword, tlds=tlds)
    tld_label = ",".join(tlds) if tlds else "popular TLDs"
    err = Console(stderr=True) if fmt in ("json", "csv") else console
    err.print(
        f"[bold]Generating domains for [cyan]{keyword}[/cyan] "
        f"across {tld_label} ({len(candidates)} candidates)...[/bold]\n"
    )

    results: list[DomainResult] = []
    async with DomainChecker(rate=rate) as checker:
        await checker._rdap.load_bootstrap()
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            MofNCompleteColumn(),
            console=err,
        ) as progress:
            task = progress.add_task(
                "Checking availability", total=len(candidates)
            )
            async for result in checker.check_bulk(candidates):
                results.append(result)
                progress.update(task, advance=1)

        # Sort: available first, then by domain length
        results.sort(key=lambda r: (
            0 if r.available is True else 1,
            len(r.domain),
            r.domain,
        ))

        if available_only:
            results = [r for r in results if r.available is True]

        if not results:
            console.print("[yellow]No available domains found.[/yellow]")
            return

        if fmt == "json":
            _output_json(results, verbose)
        elif fmt == "csv":
            _output_csv(results)
        else:
            available = [r for r in results if r.available is True]
            taken = [r for r in results if r.available is not True]

            if available:
                console.print(f"[bold green]Available ({len(available)}):[/bold green]")
                table = Table(show_header=False, box=None, padding=(0, 2))
                table.add_column(style="green bold")
                table.add_column(style="dim")
                for i, r in enumerate(available, 1):
                    prefix = f"  {i}." if registrar_config else "  "
                    table.add_row(f"{prefix} {r.domain}", r.protocol_used)
                console.print(table)

            if taken and not available_only:
                console.print(f"\n[dim]Taken ({len(taken)}): {', '.join(r.domain for r in taken[:20])}", end="")
                if len(taken) > 20:
                    console.print(f" ... and {len(taken) - 20} more", end="")
                console.print("[/dim]")

            if available:
                console.print(
                    f"\n[bold]{len(available)} of {len(candidates)} "
                    f"candidates available[/bold]"
                )

        # Registration for suggest mode
        if registrar_config and fmt == "table":
            available = [r for r in results if r.available is True]
            if available:
                registrar_client = await _init_registrar(checker.session, registrar_config)
                if registrar_client:
                    await _suggest_register(
                        registrar_client, available, registrar_config.max_price, auto_register
                    )


async def _suggest_register(client, available, max_price, auto_register):
    """Handle registration selection from suggest mode results."""
    if auto_register:
        console.print("\n[bold]Auto-registering all available domains under max price:[/bold]")
        for r in available:
            await _try_register(client, r.domain, max_price, auto=True)
        return

    console.print()
    selection = Prompt.ask(
        "Enter numbers to register (e.g. 1,3,5) or Enter to skip",
        default="",
    )
    if not selection.strip():
        return

    indices = set()
    for part in selection.split(","):
        part = part.strip()
        try:
            idx = int(part) - 1
            if 0 <= idx < len(available):
                indices.add(idx)
        except ValueError:
            continue

    if not indices:
        console.print("[yellow]No valid selections.[/yellow]")
        return

    console.print()
    for idx in sorted(indices):
        await _try_register(client, available[idx].domain, max_price, auto=False)


# ---------------------------------------------------------------------------
# Output formatting
# ---------------------------------------------------------------------------

def _output_table(results: list[DomainResult], verbose: bool) -> None:
    """Print results as a rich table."""
    table = Table(show_header=True, header_style="bold")
    table.add_column("Domain", style="cyan")
    table.add_column("Available")
    table.add_column("Owner")
    table.add_column("Registrar")
    table.add_column("Expires (YYYY-MM-DD)")
    table.add_column("Est. Release")
    table.add_column("Status")
    table.add_column("Via", style="dim")

    has_transitional = False

    for r in results:
        if r.error:
            avail = "[yellow]?[/yellow]"
            status = f"[red]{r.error}[/red]"
        elif r.available is True:
            avail = "[green]Yes[/green]"
            status = ""
        elif r.available is False:
            avail = "[red]No[/red]"
            status = ", ".join(r.statuses[:3]) if r.statuses else ""
        else:
            avail = "[yellow]?[/yellow]"
            status = ""

        owner = _format_owner(r)
        release = _estimate_release(r)
        if release:
            has_transitional = True

        table.add_row(
            r.domain,
            avail,
            owner,
            r.registrar or "",
            _format_date(r.expiry_date),
            release,
            status,
            r.protocol_used or "",
        )

    console.print(table)

    statuses_seen = set()
    for r in results:
        for s in r.statuses:
            statuses_seen.add(s.lower().replace(" ", ""))
    _print_legend(statuses_seen, has_transitional)

    if verbose:
        for r in results:
            if r.raw_response:
                console.print(f"\n[bold]--- Raw response for {r.domain} ---[/bold]")
                console.print(r.raw_response)


def _format_owner(r: DomainResult) -> str:
    if r.available is True or r.available is None:
        return ""
    if r.privacy_protected is True:
        return "[dim]PRIVATE[/dim]"
    parts = []
    if r.registrant_name:
        parts.append(r.registrant_name)
    if r.registrant_org and r.registrant_org != r.registrant_name:
        parts.append(r.registrant_org)
    if parts:
        return "\n".join(parts)
    return "[dim]hidden[/dim]"


def _estimate_release(r: DomainResult) -> str:
    if r.available is True or r.available is None:
        return ""

    statuses_lower = {s.lower().replace(" ", "") for s in r.statuses}

    if "pendingdelete" in statuses_lower:
        return "[bold yellow]~1-5 days[/bold yellow]"

    if "redemptionperiod" in statuses_lower:
        return "[yellow]~30-35 days[/yellow]"

    expiry = _parse_date(r.expiry_date)
    if expiry and expiry < datetime.now():
        days_expired = (datetime.now() - expiry).days
        if days_expired > 0:
            remaining = max(0, 80 - days_expired)
            if remaining > 0:
                return f"[yellow]~{remaining} days[/yellow]"
            return "[bold yellow]any day now[/bold yellow]"

    return ""


def _parse_date(date_str: str | None) -> datetime | None:
    if not date_str:
        return None
    try:
        clean = date_str.split("T")[0] if "T" in date_str else date_str
        return datetime.strptime(clean, "%Y-%m-%d")
    except ValueError:
        return None


_STATUS_LEGEND: dict[str, str] = {
    "pendingdelete":              "Registry will delete and release in ~5 days",
    "redemptionperiod":           "Owner can still reclaim for a fee (~30 day window)",
    "pendingrenew":               "Renewal initiated but not yet processed",
    "pendingrestore":             "Owner is attempting to restore from redemption",
    "pendingtransfer":            "Transfer to another registrar in progress",
    "pendingcreate":              "Domain recently created, pending activation",
    "pendingupdate":              "Update in progress at the registry",
    "serverhold":                 "Registry has suspended resolution (may be reserved or policy hold)",
    "clienthold":                 "Registrar has suspended resolution (often unpaid)",
    "clientdeleteprohibited":     "Registrar lock: cannot be deleted",
    "clienttransferprohibited":   "Registrar lock: cannot be transferred",
    "clientupdateprohibited":     "Registrar lock: cannot be modified",
    "serverdeleteprohibited":     "Registry lock: cannot be deleted",
    "servertransferprohibited":   "Registry lock: cannot be transferred",
    "serverupdateprohibited":     "Registry lock: cannot be modified",
    "active":                     "Domain is registered and resolving normally",
    "inactive":                   "Domain is registered but not resolving (no nameservers)",
    "autorenewperiod":            "In auto-renewal grace period after expiry",
    "addperiod":                  "Newly registered, within add grace period",
    "transferperiod":             "Recently transferred, within transfer grace period",
}


def _print_legend(statuses_seen: set[str], has_transitional: bool) -> None:
    relevant = {}
    for status in statuses_seen:
        normalized = status.lower().replace(" ", "")
        if normalized in _STATUS_LEGEND:
            relevant[normalized] = _STATUS_LEGEND[normalized]

    if not relevant:
        return

    console.print()
    console.print("[bold]Status legend:[/bold]")
    for code, desc in sorted(relevant.items()):
        console.print(f"  [dim]{code}[/dim] — {desc}")

    if has_transitional:
        console.print()
        console.print(
            "[dim]Est. Release is approximate. Actual timelines vary by registrar and TLD.[/dim]"
        )


def _output_json(results: list[DomainResult], verbose: bool) -> None:
    data = []
    for r in results:
        entry = {
            "domain": r.domain,
            "available": r.available,
            "registrant_name": r.registrant_name,
            "registrant_org": r.registrant_org,
            "privacy_protected": r.privacy_protected,
            "registrar": r.registrar,
            "creation_date": r.creation_date,
            "expiry_date": r.expiry_date,
            "statuses": r.statuses,
            "protocol_used": r.protocol_used,
            "error": r.error,
        }
        if verbose and r.raw_response:
            entry["raw_response"] = r.raw_response
        data.append(entry)
    print(json.dumps(data, indent=2))


def _output_csv(results: list[DomainResult]) -> None:
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(
        ["domain", "available", "registrant_name", "registrant_org", "privacy_protected",
         "registrar", "creation_date", "expiry_date", "statuses", "protocol", "error"]
    )
    for r in results:
        writer.writerow([
            r.domain,
            r.available,
            r.registrant_name or "",
            r.registrant_org or "",
            r.privacy_protected if r.privacy_protected is not None else "",
            r.registrar or "",
            r.creation_date or "",
            r.expiry_date or "",
            "|".join(r.statuses),
            r.protocol_used,
            r.error or "",
        ])
    print(buf.getvalue(), end="")


def _format_date(date_str: str | None) -> str:
    if not date_str:
        return ""
    if "T" in date_str:
        return date_str.split("T")[0]
    return date_str


if __name__ == "__main__":
    main()
