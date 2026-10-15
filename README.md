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

Registries hide their reserved and premium lists, so only a registrar can confirm that a name can really be bought, and at what price. hushname can check through **Porkbun's** free API.

1. Log in to Porkbun (or create a free account), open **Account → API Access** (<https://porkbun.com/account/api>), and create an API key. It only needs the **read** permission.
2. Give hushname the keys, in one of two ways:
   - **A `.env` file** in the folder you run hushname from. It's already git-ignored, so it won't be committed:
     ```
     PORKBUN_API_KEY=pk1_...
     PORKBUN_SECRET_KEY=sk1_...
     ```
   - **Environment variables** for the current terminal session:
     ```bash
     export PORKBUN_API_KEY=pk1_...
     export PORKBUN_SECRET_KEY=sk1_...
     ```
3. Run a scan as usual. Names that come back `unregistered` are now confirmed automatically, and their status changes to `available`, `premium` or `unavailable`, with Porkbun's real prices. To confirm an older run without rescanning:
   ```bash
   python3 -m hushname verify results/<run-folder>
   ```
   Use `--no-verify` on `scan` to skip the Porkbun step for one run.

**Good to know**
- **It's read-only.** hushname only calls the availability check (`/domain/checkDomain`), which can't buy anything.
- **It's a privacy trade-off.** Porkbun sees the names you verify. Only names the registries didn't find are sent, never your whole word list. If you'd rather not share them, leave the keys unset; you'll get `unregistered` (unconfirmed) results instead.
- **Watch Porkbun's limits.** hushname sends 25 names per request and stays under the 200-names-per-minute budget. Porkbun also treats an account that checks about **1,800 names in 3 hours** while buying almost nothing as a bulk scanner, and limits it to 50 names per 5 minutes for the next 24 hours. Verifying one big scan is fine; verifying several back to back may trigger the limit. If that happens, hushname waits and retries.
- **The key is only as safe as you keep it.** If a key leaks, delete it on the same Porkbun page and create a new one.

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
