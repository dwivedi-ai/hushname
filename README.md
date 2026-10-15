# hushname

**Hunt domains without tipping anyone off.**

Find unregistered domain names worth owning, without leaking your ideas and without trusting a single, sometimes misleading, source.

```bash
git clone https://github.com/dwivedi-ai/hushname.git && cd hushname
python3 -m hushname scan wordlists/oneword.txt --tlds com,si,dev
```

You need Python 3.11+. There are no dependencies. To get the `hushname` command on your PATH, run `pip install .` (or `pipx install .`).

## How a name is checked

Each name goes through up to three checks, stopping early once one gives a definite answer:

| Step | Source | What it can tell you |
|---|---|---|
| 1. RDAP | The registry itself, via IANA's official server list | `taken` if a registration exists. A "not found" response that names a block (e.g. `solar.dev blocked by BSA`) becomes `reserved` |
| 2. WHOIS | The registry's WHOIS server, asked only about names step 1 didn't find | `reserved` (e.g. `.xyz`: "reserved by the registry") or `taken` |
| 3. Registrar *(optional)* | Porkbun's read-only availability API | `available` at standard price, `premium`, or `unavailable`, plus the real price |

**Why step 3 exists:** registries don't publish their reserved or premium lists. For example, `retrieval.dev` shows as "not found" in every public registry source, yet it can't be bought. Without step 3, the best the tool can honestly say is **`unregistered`**: no registration exists, but you may still be unable to buy it. It never claims `available` unless a registrar has confirmed it.

### Statuses
| status | meaning |
|---|---|
| `available` | A registrar confirmed it can be bought at the normal price |
| `premium` | Can be bought, but at a premium price (often $100s–$1000s) |
| `unregistered` | No registration and no reserve signal found. **Not confirmed buyable** |
| `reserved` | The registry is holding the name back (blocked, reserved, or kept for itself) |
| `unavailable` | The registrar says it can't be registered |
| `taken` | Already registered (left out of the output unless you pass `--include-taken`) |
| `error` | Lookup failed; it will be retried on the next run |

## Turning on registrar verification (recommended)

1. Create a free Porkbun account and API key at <https://porkbun.com/account/api>. The key only needs the **read** permission.
2. Export the keys. Never commit them; `.env` is already in `.gitignore`.
   ```bash
   export PORKBUN_API_KEY=pk1_...
   export PORKBUN_SECRET_KEY=sk1_...
   ```
3. Scans now verify `unregistered` names automatically. To verify an older run, use:
   ```bash
   python3 -m hushname verify results/2026-10-15_142415_full-scan
   ```

The tool only calls the availability endpoint, sends up to 25 names per request, and paces itself well under Porkbun's limits. It never registers or pays for anything.

## Commands

```bash
# Check word lists against TLDs (default TLDs: com,si; default budget $30/yr renewal)
hushname scan wordlists/oneword.txt wordlists/emerging.txt --tlds com,si,dev,app,xyz

# Quick one-off names
hushname scan --names "gpuhq,evalos" --tlds si,com

# Build compound names from two lists (keyword x suffix)
hushname generate wordlists/heads.txt wordlists/tails.txt -o wordlists/compounds.txt

# Confirm a previous run with Porkbun
hushname verify results/<run-folder>

# TLD price table and which TLDs can be checked
hushname tlds
```

Useful `scan` options: `--budget 20`, `--label my-idea`, `--include-taken`, `--cache-hours 0` (force fresh lookups), and `--no-verify`.

## Project layout

```
hushname/            the package (standard library only)
  cli.py           commands: scan, verify, generate, tlds
  registry.py      RDAP + WHOIS lookups, rate limiting, status rules
  porkbun.py       optional registrar verification
  scoring.py       0-100 sorting heuristic
  storage.py       word list parsing, cache, run output
  data/tlds.toml   price estimates per TLD   <- edit me
  data/hot_words.txt  keywords that get a scoring boost  <- edit me
wordlists/         inputs: one name per line, '#' comments allowed
  oneword.txt      single words in demand (AI, agents, compute, energy, finance)
  emerging.txt     2026 "agent economy" phrases
  heads.txt / tails.txt  parts for `generate`
  compounds.txt    generated from heads x tails
results/           outputs (git-ignored), one folder per run:
  <date>_<time>_<label>/
    report.md      readable summary grouped by status
    results.csv    every row: domain, status, score, prices, evidence
    run.json       inputs, TLDs, budget and verification mode, for reproducibility
tests/             offline unit tests: python3 -m unittest discover tests
```

The lookup cache lives in `~/.cache/hushname/` (or `$XDG_CACHE_HOME/hushname`), outside the repo.

## Safety
- **Standard library only.** There's nothing to install and nothing third-party to audit.
- **No registrar search boxes.** Names typed into registrar or reseller search pages can leak to *front-runners*, who register them before you do. Steps 1–2 only contact the registries. Step 3 is opt-in and uses your own account at one reputable registrar.
- **Polite by default.** About 1 request per 0.6 s per registry, backoff on errors, and answers cached for 24 h. Registries throttle or block heavy users, so don't lower `--interval` much.
- **Read-only.** Nothing is ever purchased.

## What makes a domain high-value
1. **Short.** 1–5 characters is the top tier, and value drops sharply after two words.
2. **Commercial keyword.** It names a market where people spend money (AI, finance, insurance, health, compute).
3. **Extension.** `.com` dominates. `.ai` is the only strong challenger (Bot.ai sold for $1.2M; Genesis.ai and Lotus.ai for $400k each), but it renews at ~$93–115/yr with a 2-year minimum.
4. **Brandable.** Easy to say and spell, with no hyphens or digits.
5. **Clean.** No trademark conflicts and no spam history. Check USPTO/EUIPO before buying.

`score` is a rough sorting heuristic built from factors 1–4. **It is not an appraisal.**

## Budget reality (renewal per year)
| TLD | ~renew | ≤ $30? |
|---|---|---|
| .com | $15 | yes |
| .si | $13–25 | yes. Cheap, but names rarely resell |
| .app / .dev / .xyz | $15–16 | yes |
| .ai | $93–115, 2-year minimum | no |
| .io / .co / .me | n/a | not checkable (no official RDAP server) |

On a hand-registration budget, good names come from **registering around new terms early**, before they go mainstream (add them to `wordlists/`). Another route is **backordering** good `.com` names as they expire.

## Contributing
New word lists, price updates in `data/tlds.toml`, and more registry block phrases in `registry.py` are all welcome. Run the tests before opening a PR.

## License
MIT
