"""Command line: parcel-watch track|add|list|remove|check|doctor|mcp."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import timedelta
from typing import Optional

from . import __version__, config, watch
from .carriers.base import HttpError, ProviderError
from .detect import CARRIERS
from .model import TrackResult, parse_iso
from .router import Router

CARRIER_NAMES = {
    "fedex": "FedEx",
    "ups": "UPS",
    "usps": "USPS",
    "amazon": "Amazon",
    "dhl": "DHL",
    "unknown": "Unknown carrier",
}

EPILOG = """\
sources (the carrier's own API first, then Ship24 as the fallback):
  fedex-api      FedEx Track API      FEDEX_CLIENT_ID, FEDEX_CLIENT_SECRET (FEDEX_SANDBOX=1 for test keys)
  ups-api        UPS Tracking API     UPS_CLIENT_ID, UPS_CLIENT_SECRET (UPS_SANDBOX=1 for test keys)
  amazon-public  track.amazon.com     no key; PARCEL_WATCH_AMAZON=off turns it off
  ship24         Ship24, any carrier  SHIP24_API_KEY (each new number counts against the plan)

Credentials come from the environment or from credentials.env (KEY=VALUE, mode 600)
in the state directory: $PARCEL_WATCH_HOME, default ~/.config/parcel-watch.
`parcel-watch doctor` shows what is set up.
"""

STATUS_TEXT = {
    "label_created": "Label created",
    "in_transit": "In transit",
    "out_for_delivery": "Out for delivery",
    "available_for_pickup": "Ready for pickup",
    "delivered": "Delivered",
    "exception": "Exception",
    "returned": "Returning to sender",
    "unknown": "Unknown",
    "not_found": "Not found",
    "error": "Could not check",
}


def fmt_time(stamp: Optional[str]) -> str:
    """'2026-10-05T14:00:00-04:00' -> 'Mon Oct 5, 2:00 PM' (in the stamp's own zone)."""
    if not stamp:
        return "?"
    dt = parse_iso(stamp)
    if dt is None:
        return stamp
    day = f"{dt:%a %b} {dt.day}"
    if len(stamp) <= 10:
        return day
    return f"{day}, {dt.hour % 12 or 12}:{dt:%M %p}"


def fmt_eta(result: TrackResult) -> Optional[str]:
    eta = result.eta
    if eta is None:
        return None
    day = fmt_time(eta.date)
    if eta.start and eta.end:
        text = f"{day}, between {fmt_time(eta.start).split(', ')[-1]} and {fmt_time(eta.end).split(', ')[-1]}"
    elif eta.end:
        text = f"{day}, by {fmt_time(eta.end).split(', ')[-1]}"
    else:
        text = day
    return f"{text} ({eta.kind.replace('_', ' ')})"


def render(result: TrackResult, events: int) -> str:
    carrier = CARRIER_NAMES.get(result.carrier, result.carrier)
    status = STATUS_TEXT.get(result.status, result.status)
    head = f"{carrier} {result.tracking_number}: {status}"
    if result.status_text and result.status_text.lower() != status.lower():
        head += f" ({result.status_text})"
    lines = [head]
    if result.delivered_at:
        lines.append(f"  Delivered: {fmt_time(result.delivered_at)}")
    eta = fmt_eta(result)
    if eta:
        lines.append(f"  Expected: {eta}" + ("  [delayed]" if result.delayed else ""))
    elif result.delayed:
        lines.append("  Carrier reports a delay")
    for ev in result.events[:events]:
        where = f" - {ev.location}" if ev.location else ""
        lines.append(f"  {fmt_time(ev.time)}: {ev.description}{where}")
    if result.note:
        lines.append(f"  Note: {result.note}")
    if result.error:
        lines.append(f"  Error: {result.error}")
    if result.tried:
        lines.append(f"  Tried: {'; '.join(result.tried)}")
    tail = f"  Source: {result.source}"
    if result.tracking_url:
        tail += f"  {result.tracking_url}"
    lines.append(tail)
    return "\n".join(lines)


def cmd_track(args, router: Router) -> int:
    results = [router.track(n, args.carrier) for n in args.numbers]
    if args.json:
        out = [r.to_dict(args.events) for r in results]
        print(json.dumps(out if len(out) > 1 else out[0], indent=2))
    else:
        print("\n\n".join(render(r, args.events) for r in results))
    return 0 if all(r.status != "error" for r in results) else 1


def cmd_add(args, router: Router) -> int:
    entry = watch.add(args.number, args.label, args.carrier)
    result = router.track(args.number, entry.get("carrier"))
    watch.record(args.number, result)
    if args.json:
        print(json.dumps({"watching": True, "label": entry.get("label"), "result": result.to_dict(args.events)}, indent=2))
    else:
        label = f" ({entry['label']})" if entry.get("label") else ""
        print(f"Watching {result.tracking_number}{label}.")
        print(render(result, args.events))
    return 0


def cmd_list(args, router: Router) -> int:
    packages = watch.read()
    if args.json:
        print(json.dumps(packages, indent=2))
        return 0
    if not packages:
        print("The watch list is empty.")
        return 0
    for n, entry in packages.items():
        snap = entry.get("snapshot") or {}
        label = f" ({entry['label']})" if entry.get("label") else ""
        carrier = CARRIER_NAMES.get(snap.get("carrier") or "", snap.get("carrier") or "?")
        status = STATUS_TEXT.get(snap.get("status") or "", "not checked yet")
        line = f"{carrier} {n}{label}: {status}"
        if snap.get("status") != "delivered" and snap.get("eta"):
            line += f", expected {watch.describe_eta(snap['eta'])}"
        if entry.get("last_checked"):
            line += f"  (checked {entry['last_checked']})"
        if entry.get("last_error"):
            line += f"  last error: {entry['last_error']}"
        print(line)
    return 0


def cmd_remove(args, router: Router) -> int:
    removed = watch.remove(args.number)
    print(("Stopped watching " if removed else "Not on the watch list: ") + args.number)
    return 0 if removed else 1


def cmd_check(args, router: Router) -> int:
    report = watch.check(router.track, min_age=timedelta(minutes=args.min_age), force=args.force)
    if args.json:
        print(json.dumps(report, indent=2))
        return 0
    if not report["changed"] and not report["errors"]:
        if not args.quiet:
            print(f"No changes ({report['checked']} checked, {report['skipped']} checked recently).")
        return 0
    for item in report["changed"]:
        cur = item["current"]
        label = f" ({item['label']})" if item.get("label") else ""
        carrier = CARRIER_NAMES.get(cur.get("carrier") or "", cur.get("carrier"))
        print(f"{carrier} {item['tracking_number']}{label}:")
        for change in item["changes"]:
            print(f"  - {change}")
        if cur.get("status") != "delivered" and cur.get("eta"):
            print(f"  expected {watch.describe_eta(cur['eta'])}")
    for item in report["errors"]:
        label = f" ({item['label']})" if item.get("label") else ""
        print(f"{item['tracking_number']}{label}: could not check: {item['error']}")
    return 0


def cmd_doctor(args, router: Router) -> int:
    creds = config.credentials_path()
    info = {
        "version": __version__,
        "state_dir": str(config.home()),
        "credentials_file": str(creds) if creds.exists() else None,
        "sources": [],
    }
    for p in router.providers():
        entry = {"source": p.name, "carriers": list(p.carriers), "configured": p.configured()}
        if not entry["configured"]:
            entry["missing"] = p.missing()
        elif args.live and hasattr(p, "_token"):
            try:
                p._token(refresh=True)
                entry["sign_in"] = "ok"
            except (ProviderError, HttpError) as e:
                entry["sign_in"] = str(e)
        info["sources"].append(entry)
    if args.json:
        print(json.dumps(info, indent=2))
        return 0
    print(f"parcel-watch {info['version']}")
    print(f"State directory: {info['state_dir']}")
    print(f"Credentials file: {info['credentials_file'] or 'none (using environment only)'}")
    for s in info["sources"]:
        state = "ready" if s["configured"] else f"not configured (needs {', '.join(s['missing'])})"
        if "sign_in" in s:
            state += f", sign-in: {s['sign_in']}"
        print(f"  {s['source']:14} {state}")
    return 0


def cmd_mcp(args, router: Router) -> int:
    from .mcp import serve

    serve(router)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="parcel-watch",
        description="Track FedEx, UPS, Amazon, USPS and other packages through carrier APIs.",
        epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", action="version", version=f"parcel-watch {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    def common(p, events_default):
        p.add_argument("--json", action="store_true", help="print JSON")
        p.add_argument("--events", type=int, default=events_default, help="scan events to show")

    p = sub.add_parser("track", help="look up one or more tracking numbers")
    p.add_argument("numbers", nargs="+")
    p.add_argument("--carrier", choices=CARRIERS, help="skip format detection")
    common(p, 5)
    p.set_defaults(func=cmd_track)

    p = sub.add_parser("add", help="watch a package and look it up now")
    p.add_argument("number")
    p.add_argument("--label", help="what's in the package, e.g. 'new monitor'")
    p.add_argument("--carrier", choices=CARRIERS)
    common(p, 3)
    p.set_defaults(func=cmd_add)

    p = sub.add_parser("list", help="show watched packages (from the last check)")
    p.add_argument("--json", action="store_true", help="print JSON")
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("remove", help="stop watching a package")
    p.add_argument("number")
    p.set_defaults(func=cmd_remove)

    p = sub.add_parser("check", help="refresh watched packages and report changes")
    p.add_argument("--json", action="store_true", help="print JSON")
    p.add_argument("--force", action="store_true", help="ignore --min-age")
    p.add_argument("--min-age", type=float, default=15, metavar="MINUTES", help="skip packages checked more recently (default 15)")
    p.add_argument("--quiet", action="store_true", help="print nothing when nothing changed")
    p.set_defaults(func=cmd_check)

    p = sub.add_parser("doctor", help="show which tracking sources are set up")
    p.add_argument("--json", action="store_true", help="print JSON")
    p.add_argument("--live", action="store_true", help="also test FedEx/UPS sign-in")
    p.set_defaults(func=cmd_doctor)

    p = sub.add_parser("mcp", help="run as an MCP server on stdin/stdout")
    p.set_defaults(func=cmd_mcp)
    return parser


def main(argv: Optional[list] = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args, Router())


if __name__ == "__main__":
    sys.exit(main())
