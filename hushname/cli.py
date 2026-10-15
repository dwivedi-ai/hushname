"""Command line interface: hushname scan | verify | generate | tlds."""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import sys
from datetime import datetime
from pathlib import Path

from . import __version__, porkbun, registry, storage
from .scoring import score

STATUS_ORDER = {s: i for i, s in enumerate([
    registry.AVAILABLE, registry.PREMIUM, registry.UNREGISTERED, registry.RESERVED,
    registry.UNAVAILABLE, registry.ERROR, registry.TAKEN])}


def log(*a):
    print(*a, file=sys.stderr, flush=True)


# ------------------------------------------------------------------- scan

def lookup(domain: str, tld: str, rdap_base: str, cache: storage.Cache,
           rdap_throttle: registry.HostThrottle, whois_throttle: registry.HostThrottle) -> dict:
    hit = cache.get(domain)
    if hit:
        return {"status": hit["status"], "detail": hit["detail"]}
    status, detail = registry.rdap_lookup(domain, rdap_base, rdap_throttle)
    if status == registry.UNREGISTERED:
        server = registry.whois_server(tld)
        if server:
            second = registry.whois_second_opinion(domain, server, whois_throttle)
            if second:
                status, detail = second
    entry = {"status": status, "detail": detail}
    cache.put(domain, entry)
    return entry


def price_fields(tld: str, cfg: dict, budget: float) -> dict:
    p = cfg.get(tld)
    if not p:
        return {"reg_usd": "", "renew_usd": "", "min_years": "", "in_budget": "?",
                "price_source": "unknown"}
    return {"reg_usd": p["reg"], "renew_usd": p["renew"], "min_years": p["min_years"],
            "in_budget": "yes" if p["renew"] <= budget else "no", "price_source": "estimate"}


def apply_verification(rows: list[dict], budget: float) -> str:
    """Confirm 'unregistered' rows with Porkbun if keys are set. Returns mode."""
    creds = porkbun.credentials()
    if not creds:
        return "off"
    todo = [r["domain"] for r in rows if r["status"] == registry.UNREGISTERED]
    if not todo:
        return "porkbun"
    log(f"verifying {len(todo)} unregistered names with Porkbun...")
    answers = porkbun.check(todo, creds, log=log)
    for r in rows:
        a = answers.get(r["domain"])
        if not a:
            continue
        r["status"], r["detail"] = a["status"], a["detail"]
        if a["renew_usd"] != "":
            r.update(reg_usd=a["reg_usd"], renew_usd=a["renew_usd"],
                     min_years=a["min_years"], price_source="porkbun",
                     in_budget="yes" if a["renew_usd"] <= budget else "no")
        if r["status"] == registry.PREMIUM:
            r["in_budget"] = "no"
    return "porkbun" if len(answers) == len(todo) else "porkbun (partial)"


def sort_rows(rows: list[dict]) -> None:
    rows.sort(key=lambda r: (STATUS_ORDER.get(r["status"], 99), -int(r["score"]), r["domain"]))


def cmd_scan(args) -> int:
    labels, bad = storage.read_labels(args.wordlists, args.names)
    for b in bad:
        log(f"skipping invalid name: {b!r}")
    if not labels:
        log("no names to check - pass word list files and/or --names")
        return 2
    tlds = [t.strip().lower().lstrip(".") for t in args.tlds.split(",") if t.strip()]
    servers = registry.rdap_servers()
    skipped = [t for t in tlds if t not in servers]
    if skipped:
        log(f"no official RDAP server for .{', .'.join(skipped)} - skipped")
        tlds = [t for t in tlds if t in servers]
    if not tlds:
        return 2

    cfg = storage.tld_config()
    cache = storage.Cache(args.cache_hours)
    rdap_throttle = registry.HostThrottle(args.interval)
    whois_throttle = registry.HostThrottle(max(args.interval, 1.5))
    jobs = [(f"{l}.{t}", l, t) for l in labels for t in tlds]
    log(f"checking {len(jobs)} domains ({len(labels)} names x {len(tlds)} TLDs)")

    rows = []
    with cf.ThreadPoolExecutor(max_workers=8) as ex:
        futures = {ex.submit(lookup, d, t, servers[t], cache, rdap_throttle, whois_throttle): (d, l, t)
                   for d, l, t in jobs}
        for i, fut in enumerate(cf.as_completed(futures), 1):
            domain, label, tld = futures[fut]
            res = fut.result()
            rows.append({"domain": domain, **res,
                         "score": score(label, cfg.get(tld, {}).get("weight", 0)),
                         **price_fields(tld, cfg, args.budget)})
            if i % 100 == 0:
                log(f"  {i}/{len(jobs)}")
                cache.save()
    cache.save()

    mode = "off" if args.no_verify else apply_verification(rows, args.budget)
    sort_rows(rows)
    if not args.include_taken:
        rows = [r for r in rows if r["status"] != registry.TAKEN]

    name = args.label or "+".join(Path(p).stem for p in args.wordlists) or "names"
    run_dir = storage.new_run_dir(args.out, name)
    meta = {"started": datetime.now().isoformat(timespec="seconds"), "version": __version__,
            "inputs": args.wordlists, "inline_names": args.names, "tlds": tlds,
            "budget": args.budget, "verification": mode, "checked": len(jobs)}
    storage.write_run(run_dir, rows, meta)
    summarize(rows, run_dir, mode)
    return 0


def summarize(rows: list[dict], run_dir: Path, mode: str) -> None:
    best = [r for r in rows if r["status"] in (registry.AVAILABLE, registry.UNREGISTERED)
            and r["in_budget"] == "yes"]
    log(f"\nresults: {run_dir}/report.md  (also results.csv, run.json)")
    if mode == "off":
        log("note: no registrar verification (set PORKBUN_API_KEY / PORKBUN_SECRET_KEY). "
            "'unregistered' names are NOT confirmed buyable.")
    for r in best[:20]:
        print(f"  {r['score']:>3}  {r['domain']:<30} {r['status']:<13} ${r['renew_usd']}/yr")


# ----------------------------------------------------------------- verify

def cmd_verify(args) -> int:
    run_dir = Path(args.run)
    if not porkbun.credentials():
        log("set PORKBUN_API_KEY and PORKBUN_SECRET_KEY first (see README)")
        return 2
    rows, meta = storage.read_run(run_dir)
    meta["verification"] = apply_verification(rows, float(meta["budget"]))
    meta["verified_at"] = datetime.now().isoformat(timespec="seconds")
    sort_rows(rows)
    storage.write_run(run_dir, rows, meta)
    summarize(rows, run_dir, meta["verification"])
    return 0


# --------------------------------------------------------------- generate

def cmd_generate(args) -> int:
    heads, _ = storage.read_labels([args.heads])
    tails, _ = storage.read_labels([args.tails])
    out = []
    for h in heads:
        for t in tails:
            out.append(h + t)
            if args.both_ways:
                out.append(t + h)
    names = [n for n in dict.fromkeys(out) if len(n) <= args.max_len]
    text = f"# generated: {args.heads} x {args.tails}\n" + "\n".join(names) + "\n"
    if args.output:
        Path(args.output).write_text(text)
        log(f"wrote {len(names)} names to {args.output}")
    else:
        sys.stdout.write(text)
    return 0


# ------------------------------------------------------------------- tlds

def cmd_tlds(args) -> int:
    cfg = storage.tld_config()
    servers = registry.rdap_servers()
    print(f"{'tld':<8}{'reg $':>7}{'renew $':>9}{'min yrs':>9}  {'in budget':<10} rdap")
    for t, p in sorted(cfg.items(), key=lambda kv: kv[1]["renew"]):
        ok = "yes" if p["renew"] <= args.budget else "no"
        print(f".{t:<7}{p['reg']:>7}{p['renew']:>9}{p['min_years']:>9}  {ok:<10} "
              f"{'yes' if t in servers else 'no'}")
    return 0


# ------------------------------------------------------------------- main

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="hushname",
                                description="Find unregistered high-value domain names, safely.")
    p.add_argument("--version", action="version", version=f"hushname {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("scan", help="check word lists against TLDs")
    s.add_argument("wordlists", nargs="*", help="word list files (see wordlists/)")
    s.add_argument("--names", help="extra comma-separated names")
    s.add_argument("--tlds", default="com,si", help="comma-separated TLDs (default: com,si)")
    s.add_argument("--budget", type=float, default=30, help="max renewal $/yr (default 30)")
    s.add_argument("--out", default="results", help="results root folder (default: results)")
    s.add_argument("--label", help="name for the run folder (default: word list names)")
    s.add_argument("--include-taken", action="store_true", help="keep taken names in output")
    s.add_argument("--no-verify", action="store_true", help="skip Porkbun even if keys are set")
    s.add_argument("--interval", type=float, default=0.6,
                   help="seconds between requests to one registry (default 0.6)")
    s.add_argument("--cache-hours", type=float, default=24,
                   help="reuse registry answers this fresh (default 24, 0 = always re-query)")
    s.set_defaults(func=cmd_scan)

    v = sub.add_parser("verify", help="confirm a previous run's names with Porkbun")
    v.add_argument("run", help="run folder, e.g. results/2026-10-15_120000_oneword")
    v.set_defaults(func=cmd_verify)

    g = sub.add_parser("generate", help="combine two word lists into compound names")
    g.add_argument("heads")
    g.add_argument("tails")
    g.add_argument("-o", "--output", help="write to file instead of stdout")
    g.add_argument("--both-ways", action="store_true", help="also add tail+head")
    g.add_argument("--max-len", type=int, default=15)
    g.set_defaults(func=cmd_generate)

    t = sub.add_parser("tlds", help="show TLD prices and RDAP support")
    t.add_argument("--budget", type=float, default=30)
    t.set_defaults(func=cmd_tlds)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except KeyboardInterrupt:
        log("interrupted")
        return 130
