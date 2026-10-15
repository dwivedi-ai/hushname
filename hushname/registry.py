"""Talk to domain registries: RDAP first, WHOIS as a second opinion.

Important limitation (and the reason for the WHOIS + registrar steps):
an RDAP "404 Not Found" only means the name is not in the registry's
database of *registered* domains. Names the registry has reserved, blocked
or kept for itself are often also "not found". So a 404 is reported as
"unregistered", never as "available".
"""
from __future__ import annotations

import json
import re
import socket
import threading
import time
import urllib.error
import urllib.request
from urllib.parse import urlparse

from . import __version__
from .storage import cache_dir

USER_AGENT = f"hushname/{__version__} (domain availability checker)"
BOOTSTRAP_URL = "https://data.iana.org/rdap/dns.json"

# Statuses a lookup can end in, from most to least certain.
TAKEN = "taken"                  # registry has a registration record
RESERVED = "reserved"            # registry says reserved / blocked / premium-held
UNREGISTERED = "unregistered"    # no record and no reserve signal; NOT confirmed buyable
AVAILABLE = "available"          # a registrar confirmed it can be bought at standard price
PREMIUM = "premium"              # a registrar can sell it, but at a premium price
UNAVAILABLE = "unavailable"      # a registrar says it cannot be registered
ERROR = "error"

# Phrases that mean "you can't just register this", seen in RDAP 404 bodies
# (e.g. Google Registry: "solar.dev blocked by BSA") and WHOIS replies
# (e.g. CentralNic: "reserved by the registry").
_BLOCK_RE = re.compile(
    r"\bblocked\b|(?<!rights )\breserved\b|not available for registration|"
    r"\bpremium\b|\bunavailable\b",
    re.IGNORECASE,
)


def block_signal(text: str) -> str | None:
    """Return the matching phrase if `text` says the name is held back."""
    m = _BLOCK_RE.search(text or "")
    return m.group(0).lower() if m else None


# --------------------------------------------------------------- throttle

class HostThrottle:
    """At most one request per `interval` seconds to each host."""

    def __init__(self, interval: float):
        self.interval = interval
        self._lock = threading.Lock()
        self._next: dict[str, float] = {}

    def wait(self, host: str) -> None:
        with self._lock:
            now = time.monotonic()
            slot = max(now, self._next.get(host, 0.0))
            self._next[host] = slot + self.interval
        time.sleep(max(0.0, slot - time.monotonic()))


# -------------------------------------------------------------- discovery

def _http_get(url: str, timeout: float = 20) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def rdap_servers() -> dict[str, str]:
    """Map TLD -> RDAP base URL from IANA's bootstrap file (cached 7 days)."""
    path = cache_dir() / "iana_rdap.json"
    if not path.exists() or time.time() - path.stat().st_mtime > 7 * 86400:
        path.write_bytes(_http_get(BOOTSTRAP_URL))
    out: dict[str, str] = {}
    for tlds, urls in json.loads(path.read_text())["services"]:
        https = [u for u in urls if u.startswith("https://")] or urls
        for t in tlds:
            out[t.lower()] = https[0].rstrip("/") + "/"
    return out


def whois_server(tld: str) -> str | None:
    """Ask whois.iana.org which WHOIS server a TLD uses (cached 7 days)."""
    path = cache_dir() / "whois_servers.json"
    try:
        known = json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        known = {}
    entry = known.get(tld)
    if entry and time.time() - entry["t"] < 7 * 86400:
        return entry["server"]
    server = None
    try:
        text = whois_query("whois.iana.org", tld)
        m = re.search(r"^whois:\s*(\S+)", text, re.MULTILINE)
        server = m.group(1) if m else None
    except OSError:
        return None
    known[tld] = {"server": server, "t": time.time()}
    path.write_text(json.dumps(known))
    return server


# ---------------------------------------------------------------- lookups

def whois_query(server: str, query: str, timeout: float = 15) -> str:
    with socket.create_connection((server, 43), timeout=timeout) as s:
        s.sendall(query.encode() + b"\r\n")
        chunks = []
        while True:
            data = s.recv(4096)
            if not data:
                break
            chunks.append(data)
    return b"".join(chunks).decode("utf-8", "replace")


def rdap_lookup(domain: str, base: str, throttle: HostThrottle) -> tuple[str, str]:
    """Return (status, detail). Status is TAKEN, UNREGISTERED, RESERVED or ERROR."""
    url = base + "domain/" + domain
    host = urlparse(url).netloc
    last = "retries exhausted"
    for attempt in range(4):
        throttle.wait(host)
        req = urllib.request.Request(url, headers={
            "User-Agent": USER_AGENT, "Accept": "application/rdap+json"})
        try:
            with urllib.request.urlopen(req, timeout=20):
                return TAKEN, "rdap record exists"
        except urllib.error.HTTPError as e:
            if e.code == 404:
                body = e.read().decode("utf-8", "replace")
                try:
                    j = json.loads(body)
                    msg = " ".join(j.get("description") or []) + " " + str(j.get("title", ""))
                except json.JSONDecodeError:
                    msg = body[:500]
                hit = block_signal(msg)
                if hit:
                    return RESERVED, f"rdap: {msg.strip()}"
                return UNREGISTERED, "rdap: not found"
            if e.code in (429, 500, 502, 503, 504):
                last = f"http {e.code}"
                time.sleep(5 * (attempt + 1))
                continue
            return ERROR, f"rdap http {e.code}"
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            last = str(e)
            time.sleep(2 * (attempt + 1))
    return ERROR, f"rdap unreachable: {last}"


def whois_second_opinion(domain: str, server: str, throttle: HostThrottle) -> tuple[str, str] | None:
    """For an RDAP-unregistered name, ask WHOIS whether it is reserved.

    Returns (RESERVED, detail) when WHOIS reports a hold, (TAKEN, detail)
    when WHOIS shows a record after all, else None (no new information).
    """
    throttle.wait(server)
    try:
        text = whois_query(server, domain)
    except OSError:
        return None
    hit = block_signal(text)
    if hit:
        line = next((l.strip() for l in text.splitlines() if hit in l.lower()), hit)
        return RESERVED, f"whois: {line[:160]}"
    if re.search(r"^\s*(Registrar|Creation Date|Registry Domain ID):\s*\S", text, re.MULTILINE | re.IGNORECASE):
        return TAKEN, "whois: registration record exists"
    return None
