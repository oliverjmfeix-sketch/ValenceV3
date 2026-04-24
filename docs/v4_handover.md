# v4 Deontic Pilot — Handover

Short-form summary for a fresh session continuing the work. Authoritative
detail lives in the docs listed below; this page exists so you don't have
to read 50+ commits to pick up.

## Where we are (as of 2026-04-24, post-Phase-A norm-id rename)

**Branch:** `v4-deontic` (local-only, never pushed). Running in worktree
`C:\Users\olive\ValenceV3\.claude\worktrees\sweet-raman-b5be00` on the
source machine. **HEAD: `c9ca2df`** plus this handover-update commit.

**Pilot state:** all 13 prompts complete. Most recent multi-commit phase
was Phase A (norm-id rename) which landed 4 commits on top of the pilot
acceptance run.

**Scope:** Valence v4 pilot — covenant-analysis platform refactor from
v3's attribute-heavy model to a deontic graph model. Pilot target: RP
only, Duck Creek deal (`6e76ed06`), 6 lawyer gold-standard questions.
Other covenants (MFN, DI, Liens, Asset Sales, …) remain v3 behavior
until the RP pilot passes its acceptance test (Rule 7.1).

## What exists now (all four layers live in TypeDB Cloud + Python)

- **Foundational rules + architecture:**
  [docs/v4_foundational_rules.md](v4_foundational_rules.md) (now 8
  rule families; Rule 8.1 governs world-state-as-input),
  [docs/v4_deontic_architecture.md](v4_deontic_architecture.md)
- **Schema:** [app/data/schema_v4_deontic.tql](../app/data/schema_v4_deontic.tql)
- **Function library:** 6 files in `app/data/deontic_*_functions.tql`
- **Projection engine:** [app/services/deontic_projection.py](../app/services/deontic_projection.py)
  Phase A: emits categorical norm_ids + kinds.
- **Classification harness:** [app/services/classification_measurement.py](../app/services/classification_measurement.py)
- **Validation harness:** [app/services/validation_harness.py](../app/services/validation_harness.py)
  Includes A6 graph-state invariant assertions.
- **Operations layer:** [app/services/operations.py](../app/services/operations.py)
  All 7 operations: describe_norm, get_attribute, enumerate_linked,
  trace_pathways (with `collapse_contributors` flag), filter_norms,
  evaluate_feasibility, evaluate_capacity. Defaults to
  `valence_v4_ground_truth`; override with `--db`.
- **Intent parser:** [app/services/intent_parser.py](../app/services/intent_parser.py)
  Claude Sonnet 4.6, cached system prompt, 3-class intent
  (operation_call / clarification_needed / out_of_scope) with 4
  sub-categories on out_of_scope. Per-invocation audit log at
  `app/data/intent_parser_log/<deal>_<date>.jsonl` (gitignored).
- **Renderer:** [app/services/renderer.py](../app/services/renderer.py)
  Pure Python, deterministic; per-operation prose + predicate-label map.
- **Eval runner:** [app/services/v4_eval.py](../app/services/v4_eval.py)
  Adapted from v3's graph_eval; produces full.json + summary.txt +
  verbatim.txt artifacts.
- **Ground truth:** [app/data/duck_creek_rp_ground_truth.yaml](../app/data/duck_creek_rp_ground_truth.yaml)
  63 norms + 5 J.Crew defeaters. Phase A renamed every norm_id to
  `<deal_id>_<categorical_kind>` format. Full rename map at
  [docs/v4_norm_id_rename_map.md](v4_norm_id_rename_map.md).
- **$12.95 extracted artifact:** Duck Creek RP fully extracted into
  `valence_v4`. **Must not be re-extracted.**
- **Extraction snapshot (post-Prompt-10):**
  [app/data/extraction_snapshots/6e76ed06.tql](../app/data/extraction_snapshots/6e76ed06.tql)
  30 entities + 29 relations dumped to TQL, committed in git.
  Recoverable via `restore_extraction_snapshot` if cloud DB ever
  disappears.
- **Pilot acceptance artifact:**
  [docs/v4_pilot_acceptance_run/](v4_pilot_acceptance_run/) — 6
  lawyer questions side-by-side with Valence answers. Verbatim TXT is
  the primary deliverable.

## TypeDB Cloud databases

| DB | Purpose | Populated? |
|---|---|---|
| `valence` | v3 live data (untouched) | yes |
| `valence_v4` | v4 extraction target + projection output | 8 rp_baskets + 1 jcrew_blocker + 5 blocker_exceptions + 23 projected norms (with new categorical IDs) + 5 defeaters + 7 parties + 14 expected_norm_kind |
| `valence_v4_ground_truth` | authored ground-truth graph | 63 norms (renamed) + 25 conditions + 18 gold_questions + 5 J.Crew defeaters + per-deal party instances + 14 expected_norm_kind |

## Verify TypeDB connectivity + extraction preservation

```bash
py -3.12 -c "
import os, sys
from pathlib import Path
sys.path.insert(0, str(Path.cwd()))
from dotenv import load_dotenv
load_dotenv(Path('C:/Users/olive/ValenceV3/.env'), override=True)
from typedb.driver import TypeDB, Credentials, DriverOptions, TransactionType
d = TypeDB.driver(os.environ['TYPEDB_ADDRESS'],
                  Credentials(os.environ['TYPEDB_USERNAME'], os.environ['TYPEDB_PASSWORD']),
                  DriverOptions())
try:
    print('databases:', sorted([db.name for db in d.databases.all()]))
    tx = d.transaction('valence_v4', TransactionType.READ)
    try:
        for tp in ('rp_basket','jcrew_blocker','norm','defeater','party'):
            r = tx.query(f'match \$e isa {tp}; select \$e;').resolve()
            print(f'  {tp}: {len(list(r.as_concept_rows()))}')
    finally:
        tx.close()
finally:
    d.close()
"
```

**Expected output:**
- `databases:` includes `valence`, `valence_v4`, `valence_v4_ground_truth`
- `rp_basket: 8`, `jcrew_blocker: 1`, `norm: 23`, `defeater: 5`, `party: 7`

If `rp_basket` or `jcrew_blocker` returns 0, the $12.95 extraction has
been lost — stop and investigate via the recovery runbook below.

## Current baseline (post-Phase-A)

**Harness verdicts:**

| Check | Verdict | Notes |
|---|---|---|
| A1 structural | **pass** | 22/22 norms structurally complete |
| A2 segment counts | fail | Coverage gaps (extraction produces fewer norms than gold expects) |
| A3 kind coverage | fail | 2 always-expected kinds missing |
| A4 round-trip | fail | **missing=45, spurious=6, mismatched=0** (preserved through Phase A) |
| A5 rule-selection | **pass** | 100% per-entity-type accuracy |
| A6 graph invariants | **pass** | Modality / defeater / carry / norm-count floor checks |

**Classification (V2 prompts, matched):**

| Field | Accuracy-on-matched |
|---|---|
| `capacity_composition` | 76.5% (13/17) |
| `action_scope` | 88.2% (15/17) |
| `condition_structure` | 94.1% (16/17) |
| Rule-selection | 100% |

## Latest commit timeline (Phase A through pilot acceptance run)

```
c9ca2df  v4: Phase A commit 4 — update Python references to new norm identifiers
9e1e543  v4: Phase A commit 3 — projection emits new norm_ids and norm_kinds
a39f87d  v4: Phase A commit 2 — rename norm_ids and norm_kinds in GT authoring
2a28353  v4: Phase A commit 1 — norm_id rename map (taxonomy)
f9b8a55  v4: pilot acceptance run — lawyer_dc_rp verbatim output
b932b82  v4: pilot eval runner — lawyer_dc_rp (6 questions)
97f5365  v4: rendering layer — structured operation output to lawyer prose
c8d630c  v4: GT defeater backfill — 5 J.Crew blocker exceptions
cd09033  v4: intent parser — audit logging + transparency fields
1b7219a  v4: intent parser — clarification request + out-of-scope handling
05728a3  v4: intent parser — operation dispatch + parameter validation
07abf05  v4: intent parser — prompt construction + intent classification
ad07326  v4: trace_pathways — collapse_contributors flag (default true)
e88e7f0  v4: typedb pattern #14 + role-collision note + Python-evaluator concession
```

Read commit messages for design rationale — institutional memory lives there.

## Phase A norm-id rename — what changed

Before: `dc_rp_<section>_<slug>` format encoded Duck-Creek-specific
clause letters (`_l_`, `_p_`, `_o_`, ...) and clause-content kinds
(`parent_investment_funding_permission` etc.). Loading a second deal
would collide.

After: `<deal_id>_<categorical_kind>[_<categorical_disambiguator>]`.
Clause letters live exclusively in `source_section` strings.

Examples:
- `dc_rp_6_06_p_unsub_equity_distribution` → `6e76ed06_unrestricted_sub_equity_distribution_permission`
- `dc_rp_cumulative_amount` → `6e76ed06_builder_basket_aggregate`
- `dc_rp_jcrew_blocker` → `6e76ed06_jcrew_blocker_prohibition`

Full table in [docs/v4_norm_id_rename_map.md](v4_norm_id_rename_map.md).
A4 round-trip preserved exactly across Phase A — structural-tuple
matching is independent of identifier strings.

## What's pending

**Phase B = data-model additions.** Categories per the discussions
during this session:
- Temporal anchors (closing date, fiscal periods, applicable date)
- Reallocation relations as first-class typed edges (currently
  inferred from cross-references)
- Cross-covenant relations (asset-sale → builder; investment → builder)
- World-state input shape formalization (currently a free-form
  predicate_values dict)

Phase A locked in stable categorical identifiers; Phase B can proceed
without a cascading rename.

## Hard-learned gotchas (memory file `typedb-patterns.md` has the full list)

Most important from this session's Prompts 11-13 + Phase A:

1. **TypeDB 3.x REP1 trap:** `$rel (role: $var) isa <reltype>` combined
   with `try { $rel has attr $v }` fails with "'rel' cannot be both
   Object and ThingType." Use the `links` form:
   `$rel isa <reltype>, links (role: $var, ...)`. Bit me 4 times this
   session — if a query that should return rows returns empty, this is
   the first thing to check. Documented in
   [docs/typedb_patterns.md](typedb_patterns.md) #14.

2. **Role-name collisions on `condition`.** Three relations declare a
   `condition` role; `norm_has_condition` sidesteps via `root` role
   name. Future schema additions should prefer disambiguated role
   names. Documented in `docs/v4_known_gaps.md`.

3. **Python-side evaluator** in operations.py (Rule 5.2 concession).
   `_eval_predicate` / `_eval_condition_tree` execute in Python rather
   than calling the function library — TypeDB 3.x lacks ergonomic
   transient `event_instance` injection. Containment discipline:
   thresholds + operators + tree topology read from graph; zero legal
   rules in Python. Drift between function library and Python
   evaluator IS the accepted failure mode; parity required on any
   semantics change.

4. **Seed re-load skip-by-probe.** `seed_loader` skips `.tql` files
   whose probe query returns rows. Updating a `.tql` content doesn't
   re-apply against an existing DB — need either an in-place
   delete+insert patch (Phase A pattern) or a `--force` reseed (loses
   data). For schema-affecting seed updates, prefer in-place patches.

## Recovery path (if cloud `valence_v4` is ever lost)

```bash
# 1. Rebuild schema + seeds into fresh valence_v4
py -3.12 -m app.scripts.init_schema_v4

# 2. Restore v3 extraction from the committed snapshot
py -3.12 -m app.scripts.restore_extraction_snapshot --deal 6e76ed06

# 3. Regenerate projection output (norms, conditions, defeaters, etc.)
py -3.12 -m app.services.deontic_projection --deal 6e76ed06

# 4. Sanity-check: the verify query above should report the expected counts.
```

The snapshot captures only the irreplaceable v3 extraction (deal,
provision, baskets, blocker, exceptions, sweep_tiers, pathways, and
all relations wiring them). Projection output regenerates from it.

## Cross-machine continuity (this session's transfer)

**Bundle file in main:** `transfers/v4-deontic_2026-04-24/v4-deontic_20260424_c9ca2df.bundle`
plus `RESUME.md` with full restoration steps. Commit `7126e99` on
main pushed to origin.

To resume on the other machine after `git pull origin main`:
```bash
git fetch transfers/v4-deontic_2026-04-24/v4-deontic_20260424_c9ca2df.bundle v4-deontic:v4-deontic
git switch v4-deontic
git log --oneline -3   # should show this handover-update commit at HEAD
```

Bring across separately (NOT in bundle, NOT in main):
- `.env` from `C:/Users/olive/ValenceV3/.env` — TypeDB + Anthropic creds
- Duck Creek PDF if not already on the other machine's OneDrive

## How to start a new session

1. `cd "C:/Users/olive/ValenceV3/.claude/worktrees/sweet-raman-b5be00"`
   on the source machine, OR follow the cross-machine restore above
   if on a fresh box.
2. `git status` to confirm clean working tree on `v4-deontic`
3. `git log --oneline -5` to confirm latest commits
4. Read [docs/v4_foundational_rules.md](v4_foundational_rules.md) —
   governing invariants (now includes Rule 8.1 on world-state-as-input)
5. Read [docs/v4_deontic_architecture.md](v4_deontic_architecture.md)
   §6 (operations layer) and §6.0 (structural vs evaluated dichotomy)
6. Read this file for current state
7. Read [docs/v4_norm_id_rename_map.md](v4_norm_id_rename_map.md) for
   Phase A reference
8. Read [docs/v4_pilot_acceptance_run/README.md](v4_pilot_acceptance_run/README.md)
   for the latest pilot deliverable
9. Run the verify connectivity query above
10. Await next prompt (expected: Phase B — data model additions)

## Hard constraints

- **No re-extraction.** `valence_v4` extraction is a $12.95 artifact.
- **TypeDB Cloud only.** `ip654h-0.cluster.typedb.com:80`. `.env` at
  `C:/Users/olive/ValenceV3/.env`.
- **Branch is local-only on remote.** Bundle in main is the transport;
  no `refs/heads/v4-deontic` on origin.
- **`py -3.12`** required (not system `py`) — typedb-driver needs
  Python 3.12 on Windows.
- **Before any TypeQL/TypeDB Python work**, read `typedb-patterns.md`
  in memory. Pattern #14 (links form) is the most-frequently-needed.
