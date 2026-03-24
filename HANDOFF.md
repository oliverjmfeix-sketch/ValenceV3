# HANDOFF.md — Valence V3

> Last updated: 2026-03-24 by Claude Code session
> Previous session: Synthesis guidance SSoT (Prompt 4)
> Branch: `claude/navigate-valence-project-tLy5z`

## FIRST THING: Test Railway Access

The previous session could not reach the Railway backend from the sandbox (proxy returned `403 host_not_allowed` for `valencev3-production.up.railway.app`). **Before doing anything else**, run:

```bash
curl -s --max-time 10 https://valencev3-production.up.railway.app/health
```

If this returns a JSON health response, proceed with verification below. If it fails with 403 or timeout, the sandbox egress allowlist still doesn't include Railway — ask the user to fix permissions or run verification locally.

## What Was Done This Session

### Synthesis Guidance SSoT (`57db12c`)
Moved hardcoded synthesis rules from Python to TypeDB as `synthesis_guidance` attribute on `ontology_category` entities:

- **Deleted from `deals.py`**: `MFN_SYNTHESIS_RULES` (~175 lines) and `rp_specific_rules` (~100 lines, 2 copies)
- **Added `seed_synthesis_guidance.tql`**: Per-category analysis guidance for all 28 categories (RP, A-Z, JC1-JC3, MFN1-MFN6)
- **Added `synthesis_guidance` attribute** to `ontology_category` in `schema_unified.tql`
- **Added `get_synthesis_guidance()`** to `topic_router.py` — loads guidance from TypeDB at runtime, injects into synthesis prompt
- **Fixed `/ask-graph` bug**: `covenant_type` was hardcoded to "restricted payments covenant" — now uses TopicRouter's covenant_type
- **Added `verify_synthesis_guidance.py`** script to check coverage
- **Added `init_schema.py` entry**: loads `seed_synthesis_guidance.tql` during seeding

### Railway Reseed (`1ed1594`) — NEEDS REVERT
Changed `railway.toml` to run `init_schema --force` on deploy so TypeDB gets the new `synthesis_guidance` data. **This is a temporary change that must be reverted** — otherwise every deploy re-seeds (slow startup, ~30s extra).

### CLAUDE.md Update
Simplified the "Development Workflow for Seed Data Changes" section — replaced the 4-step railway.toml toggle workflow with `railway ssh` approach.

## What Still Needs to Be Done

### 1. Revert `railway.toml` (CRITICAL)
Current `railway.toml` line 7:
```
startCommand = "sh -c 'python -m app.scripts.init_schema --force && uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}'"
```
Must be reverted to:
```
startCommand = "sh -c 'uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}'"
```
The reseed has already happened (deployed as commit `1ed1594`). Every future deploy will wastefully re-seed unless reverted.

### 2. Verify TypeDB Seeding (Part 1)
Confirm all 28 categories have `synthesis_guidance` in TypeDB. Run against deployed server:
```bash
curl -s https://valencev3-production.up.railway.app/api/categories | python -m json.tool
```
Or use the verification script (requires TypeDB access, i.e. Railway):
```bash
python -m app.scripts.verify_synthesis_guidance
```
Expected: 28 categories, all with non-empty `synthesis_guidance`.

### 3. Verify `/ask-graph` Integration (Part 3)
The `verify_prompt4.py` script was created (commit `e4a698b`) but was deleted in a later commit. It needs to be **restored from git history** and run:
```bash
git show e4a698b:app/scripts/verify_prompt4.py > app/scripts/verify_prompt4.py
python -m app.scripts.verify_prompt4 --deal-id 87852625
```

This tests 4 questions against `/ask-graph`:
| Test | Question | Expected Categories | Check |
|------|----------|-------------------|-------|
| (a) | MFN yield components | MFN3 | Guidance in trace |
| (b) | Total dividend capacity | G, F, N | Guidance in trace |
| (c) | J.Crew IP blocker | K | Guidance in trace |
| (d) | How strong is MFN protection? | MFN1-MFN6 | All 6 in trace |
| (e) | All answers | — | "Verified against:" at end |

### 4. Merge to `main`
Once verification passes, the feature branch `claude/navigate-valence-project-tLy5z` has 3 commits to merge:
- `57db12c` schema: move synthesis rules from hardcoded Python to TypeDB SSoT
- `c5327fc` fix: auto-reseed TypeDB on deploy when RESEED=true env var is set
- `1ed1594` fix: run init_schema --force on deploy to reseed TypeDB

Plus the railway.toml revert commit (step 1 above).

## Current State of Branch

**Branch**: `claude/navigate-valence-project-tLy5z` (3 commits ahead of `main`)

**Diff from main** (after excluding the deleted verify_prompt4.py):
- `app/data/schema_unified.tql` — added `synthesis_guidance` attribute to `ontology_category`
- `app/data/seed_synthesis_guidance.tql` — NEW: 28 category guidance entries (~138 lines)
- `app/routers/deals.py` — removed ~280 lines of hardcoded rules, added `get_synthesis_guidance()` calls
- `app/scripts/init_schema.py` — added `seed_synthesis_guidance.tql` to load order
- `app/scripts/verify_synthesis_guidance.py` — NEW: verification script
- `app/services/topic_router.py` — added `get_synthesis_guidance()` method
- `railway.toml` — TEMPORARY: has `--force` reseed (must revert)
- `CLAUDE.md` — simplified seed workflow docs

## Key Files Changed

| File | Change |
|------|--------|
| `app/data/seed_synthesis_guidance.tql` | NEW — 28 synthesis guidance entries |
| `app/data/schema_unified.tql` | Added `synthesis_guidance` owns on `ontology_category` |
| `app/routers/deals.py` | Removed MFN_SYNTHESIS_RULES + rp_specific_rules; calls get_synthesis_guidance() |
| `app/services/topic_router.py` | Added get_synthesis_guidance() → loads from TypeDB |
| `app/scripts/init_schema.py` | Added seed_synthesis_guidance.tql to load list |
| `railway.toml` | TEMP: --force reseed, must revert |

## Part 2 (Code Verification) — Already Passed

These checks passed locally this session:
- `MFN_SYNTHESIS_RULES` removed from `deals.py` ✓
- `rp_specific_rules` removed from `deals.py` ✓
- `get_synthesis_guidance()` exists in `topic_router.py` ✓
- Guidance injected into synthesis system prompt in `/ask-graph` ✓

## Gotchas

- **railway.toml is still in reseed mode** — revert ASAP (step 1 above)
- **verify_prompt4.py was deleted** — restore from `git show e4a698b:app/scripts/verify_prompt4.py`
- **Don't re-extract Duck Creek** — data is current (87852625, extracted 2026-03-23), costs $0.50+
- **28 categories** (not 27) — EXPECTED_CATEGORIES in the verify scripts uses 28 (19 RP + 3 JC + 6 MFN)
- **Both filter AND synthesis use Opus 4.6** — don't downgrade without re-running eval
- **All 6 gold standard questions pass** on Prompt 8d + Opus 4.6 — don't regress
- **Test deal**: Duck Creek, deal_id `87852625`, provision_id `87852625_rp`

## Environment

- **Branch**: `claude/navigate-valence-project-tLy5z` (develop here, push here)
- **Railway**: Auto-deploys from `main`. Current deploy includes the `--force` reseed.
- **TypeDB**: `valence` on `ip654h-0.cluster.typedb.com:80`. Freshly seeded with synthesis_guidance.
- **Config**: `claude_model = "claude-opus-4-6"`, `synthesis_model = "claude-opus-4-6"`
- **290 ontology questions** across 28 categories
