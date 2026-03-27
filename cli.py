#!/usr/bin/env python3
"""Domain availability checker — queries RDAP and WHOIS directly."""

from __future__ import annotations

import argparse
import asyncio
import csv
import io
import json
import logging
import sys

from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, MofNCompleteColumn
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
        '  python cli.py --file domains.txt --format json\n',
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

    if not domains:
        parser.print_help()
        sys.exit(1)

    # Configure logging
    if args.verbose:
        logging.basicConfig(
            level=logging.DEBUG,
            format="%(name)s: %(message)s",
        )

    # Run
    results = asyncio.run(_run(domains, args.rate))

    # Output
    if args.format == "json":
        _output_json(results, args.verbose)
    elif args.format == "csv":
        _output_csv(results)
    else:
        _output_table(results, args.verbose)


async def _run(domains: list[str], rate: float) -> list[DomainResult]:
    """Run domain checks with progress display."""
    results: list[DomainResult] = []

    async with DomainChecker(rate=rate) as checker:
        if len(domains) == 1:
            # Single domain — no progress bar needed
            with console.status(f"Checking {domains[0]}..."):
                result = await checker.check(domains[0])
            results.append(result)
        else:
            # Bulk — show progress
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

    # Sort results to match input order
    domain_order = {d.lower(): i for i, d in enumerate(domains)}
    results.sort(
        key=lambda r: domain_order.get(r.domain.lower(), len(domains))
    )
    return results


def _output_table(results: list[DomainResult], verbose: bool) -> None:
    """Print results as a rich table."""
    table = Table(show_header=True, header_style="bold")
    table.add_column("Domain", style="cyan")
    table.add_column("Available")
    table.add_column("Owner")
    table.add_column("Registrar")
    table.add_column("Expires (YYYY-MM-DD)")
    table.add_column("Status")
    table.add_column("Via", style="dim")

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

        table.add_row(
            r.domain,
            avail,
            owner,
            r.registrar or "",
            _format_date(r.expiry_date),
            status,
            r.protocol_used or "",
        )

    console.print(table)

    if verbose:
        for r in results:
            if r.raw_response:
                console.print(f"\n[bold]--- Raw response for {r.domain} ---[/bold]")
                console.print(r.raw_response)


def _format_owner(r: DomainResult) -> str:
    """Format the owner/registrant column."""
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


def _output_json(results: list[DomainResult], verbose: bool) -> None:
    """Print results as JSON."""
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
    """Print results as CSV."""
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
    """Format an ISO date string for display."""
    if not date_str:
        return ""
    # Truncate to date portion if it's a full ISO datetime
    if "T" in date_str:
        return date_str.split("T")[0]
    return date_str


if __name__ == "__main__":
    main()
