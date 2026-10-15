"""Optional final check through the Porkbun registrar API.

Registries don't publish their reserved or premium lists over RDAP, so only
a registrar can say for certain "yes, you can buy this, at this price".
This module is used only when PORKBUN_API_KEY and PORKBUN_SECRET_KEY are
set. It uses the read-only availability endpoint and never buys anything.

Get free keys at https://porkbun.com/account/api (an account is needed; the
key only needs the "read" permission).
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request

from . import registry

API = "https://api.porkbun.com/api/json/v3/domain/checkDomain"
BATCH = 25            # max domains per bulk call
BATCH_PAUSE = 8.0     # 25 domains / 8 s stays under the 200-per-60 s budget


def credentials() -> tuple[str, str] | None:
    key = os.environ.get("PORKBUN_API_KEY")
    secret = os.environ.get("PORKBUN_SECRET_KEY")
    return (key, secret) if key and secret else None


def interpret(info: dict) -> dict:
    """Turn one Porkbun result object into status + prices."""
    avail = info.get("avail") == "yes"
    premium = info.get("premium") == "yes"
    renew = ((info.get("additional") or {}).get("renewal") or {}).get("price")
    out = {
        "reg_usd": _num(info.get("regularPrice") or info.get("price")),
        "renew_usd": _num(renew),
        "min_years": info.get("minDuration") or "",
    }
    if not avail:
        out["status"], out["detail"] = registry.UNAVAILABLE, "porkbun: not available"
    elif premium:
        out["status"], out["detail"] = registry.PREMIUM, "porkbun: premium price"
    else:
        out["status"], out["detail"] = registry.AVAILABLE, "porkbun: available"
    return out


def _num(v):
    try:
        return round(float(v), 2)
    except (TypeError, ValueError):
        return ""


def check(domains: list[str], creds: tuple[str, str], log=print) -> dict[str, dict]:
    """Bulk-check domains. Returns {domain: interpret(...)} for answered ones."""
    key, secret = creds
    results: dict[str, dict] = {}
    pending = list(domains)
    retries = 0
    while pending:
        batch, pending = pending[:BATCH], pending[BATCH:]
        body = json.dumps({"apikey": key, "secretapikey": secret, "domains": batch}).encode()
        req = urllib.request.Request(API, data=body, method="POST", headers={
            "Content-Type": "application/json", "User-Agent": registry.USER_AGENT})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                data = json.loads(r.read())
        except urllib.error.HTTPError as e:
            data = _json_or_empty(e.read())
            if data.get("code") == "RATE_LIMIT_EXCEEDED" or e.code == 429:
                wait = int(data.get("retryAfter") or 60)
                log(f"  porkbun rate limit, waiting {wait}s")
                time.sleep(wait)
                pending = batch + pending
                continue
            log(f"  porkbun error {e.code}: {data.get('message', '')} - skipping verification")
            return results
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            log(f"  porkbun unreachable ({e}) - skipping verification")
            return results

        if data.get("status") != "SUCCESS":
            log(f"  porkbun: {data.get('message', data)} - skipping verification")
            return results
        for name, info in (data.get("domains") or {}).items():
            results[name.lower()] = interpret(info)
        unresolved = data.get("unresolved") or []
        if unresolved and retries < 3:
            retries += 1
            pending += unresolved
        log(f"  verified {len(results)}/{len(domains)}")
        if pending:
            time.sleep(BATCH_PAUSE)
    return results


def _json_or_empty(raw: bytes) -> dict:
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {}
