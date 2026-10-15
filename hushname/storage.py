"""Inputs (word lists, TLD config), the lookup cache, and run outputs."""
from __future__ import annotations

import csv
import json
import os
import re
import threading
import time
import tomllib
from datetime import datetime
from importlib import resources
from pathlib import Path

LABEL_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")

FIELDS = ["domain", "status", "score", "in_budget", "reg_usd", "renew_usd",
          "min_years", "price_source", "detail"]


def cache_dir() -> Path:
    base = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache")
    d = base / "hushname"
    d.mkdir(parents=True, exist_ok=True)
    return d


def tld_config() -> dict[str, dict]:
    text = resources.files("hushname").joinpath("data/tlds.toml").read_text()
    return tomllib.loads(text)


# ------------------------------------------------------------------ input

def read_labels(paths: list[str], extra: str | None = None) -> tuple[list[str], list[str]]:
    """Read word lists. Returns (valid labels in order, rejected entries).

    Format: one label per line (or several separated by spaces/commas);
    '#' starts a comment. A full domain like 'foo.com' is reduced to 'foo'.
    """
    raw: list[str] = []
    for p in paths:
        for line in Path(p).read_text().splitlines():
            raw += re.split(r"[\s,]+", line.split("#", 1)[0].strip().lower())
    if extra:
        raw += re.split(r"[\s,]+", extra.lower())
    seen: set[str] = set()
    good, bad = [], []
    for w in raw:
        if not w:
            continue
        w = w.split(".", 1)[0]
        if w in seen:
            continue
        seen.add(w)
        (good if LABEL_RE.match(w) else bad).append(w)
    return good, bad


# ------------------------------------------------------------------ cache

class Cache:
    """Remembers lookups so re-runs don't hit registries again."""

    def __init__(self, ttl_hours: float):
        self.path = cache_dir() / "lookups.json"
        self.ttl = ttl_hours * 3600
        self._lock = threading.Lock()
        try:
            self.data = json.loads(self.path.read_text())
        except (FileNotFoundError, json.JSONDecodeError):
            self.data = {}

    def get(self, domain: str) -> dict | None:
        e = self.data.get(domain)
        if e and time.time() - e["t"] < self.ttl and e["status"] != "error":
            return e
        return None

    def put(self, domain: str, entry: dict) -> None:
        with self._lock:
            self.data[domain] = {**entry, "t": time.time()}

    def save(self) -> None:
        with self._lock:
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.data))
            tmp.replace(self.path)


# ----------------------------------------------------------------- output

def new_run_dir(root: str, label: str) -> Path:
    stamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    slug = re.sub(r"[^a-z0-9]+", "-", label.lower()).strip("-")[:40] or "run"
    d = Path(root) / f"{stamp}_{slug}"
    d.mkdir(parents=True, exist_ok=False)
    return d


def write_run(run_dir: Path, rows: list[dict], meta: dict) -> None:
    with open(run_dir / "results.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    (run_dir / "run.json").write_text(json.dumps(meta, indent=2))
    (run_dir / "report.md").write_text(render_report(rows, meta))


def read_run(run_dir: Path) -> tuple[list[dict], dict]:
    with open(run_dir / "results.csv", newline="") as f:
        rows = list(csv.DictReader(f))
    for r in rows:
        r["score"] = int(r["score"])
    meta = json.loads((run_dir / "run.json").read_text())
    return rows, meta


SECTIONS = [
    ("available", "Available — confirmed by a registrar at standard price"),
    ("premium", "Premium — buyable, but at a premium price"),
    ("unregistered", "Unregistered — not confirmed (registry may still hold it)"),
    ("reserved", "Reserved / blocked by the registry"),
    ("unavailable", "Unavailable — registrar refused"),
]


def render_report(rows: list[dict], meta: dict) -> str:
    counts: dict[str, int] = {}
    for r in rows:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    out = [
        f"# hushname run — {meta['started']}",
        "",
        f"- Word lists: {', '.join(meta['inputs']) or '(inline)'}",
        f"- TLDs: {', '.join(meta['tlds'])}",
        f"- Budget: renewal ≤ ${meta['budget']}/yr",
        f"- Registrar verification: {meta['verification']}",
        f"- Checked {len(rows)} domains: "
        + ", ".join(f"{v} {k}" for k, v in sorted(counts.items())),
        "",
    ]
    if meta["verification"] == "off":
        out += ["> **Registrar verification was off.** \"Unregistered\" means the registry "
                "has no record, but registries also hold back reserved and premium names "
                "without saying so. Confirm the price at checkout, or set Porkbun API keys "
                "and run `hushname verify` on this folder.", ""]
    for status, title in SECTIONS:
        group = [r for r in rows if r["status"] == status]
        if not group:
            continue
        out += [f"## {title} ({len(group)})", "",
                "| domain | score | renew $/yr | in budget | note |",
                "|---|---|---|---|---|"]
        for r in group[:200]:
            out.append(f"| {r['domain']} | {r['score']} | {r['renew_usd']} | "
                       f"{r['in_budget']} | {r['detail']} |")
        if len(group) > 200:
            out.append(f"| … {len(group) - 200} more in results.csv | | | | |")
        out.append("")
    return "\n".join(out)
