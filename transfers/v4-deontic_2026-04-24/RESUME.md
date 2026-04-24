# Resume v4-deontic on another machine

Date: 2026-04-24
Branch: `v4-deontic` (HEAD: `d4a970d`)
Bundle: `v4-deontic_20260424_d4a970d.bundle` (454 KB)
Source machine path: `C:/Users/olive/ValenceV3/.claude/worktrees/sweet-raman-b5be00`

## What's in the bundle

All 79 commits ahead of `main`, including:
- Prompts 06-13 (extraction, projection, harness, operations, intent
  parser, renderer, eval runner, pilot acceptance run)
- Phase A norm-id rename (4 commits, ending `c9ca2df`)
- Handover doc refresh (`d4a970d`) — current state for fresh-session
  pickup. Read `docs/v4_handover.md` first after restore.
- All docs under `docs/v4_*.md`, the GT YAML with renamed
  identifiers, the extraction snapshot at
  `app/data/extraction_snapshots/6e76ed06.tql`

The bundle requires base commit `7e49c37` to be present on the other
machine — that's the last commit on `origin/main` before this branch
diverged. A normal `origin/main` clone has it.

## Restore on the other machine

Assumes the repo is already cloned at some path; `<repo>` below.

```bash
cd <repo>

# 1. Make sure origin/main is fetched (provides the base commit)
git fetch origin main

# 2. Pull the bundle into the local repo
git fetch /path/to/v4-deontic_20260424_d4a970d.bundle v4-deontic:v4-deontic

# 3. Switch to it
git switch v4-deontic
git log --oneline -3  # should show d4a970d at HEAD (handover refresh)
```

If you'd rather work in a worktree (matches the source-machine setup):

```bash
git worktree add ../v4-deontic-worktree v4-deontic
cd ../v4-deontic-worktree
```

## Things NOT in the bundle (machine-local; recreate on the new box)

1. **`.env`** at the repo root. Contains `TYPEDB_ADDRESS`,
   `TYPEDB_USERNAME`, `TYPEDB_PASSWORD`, `ANTHROPIC_API_KEY`. Source
   machine path: `C:/Users/olive/ValenceV3/.env`. Copy the file across
   (USB, password manager, etc. — do NOT email the secrets).

2. **Python 3.12** + `typedb-driver` (only ships py312 wheels on
   Windows). On the new machine:
   ```bash
   py -3.12 -m pip install -r requirements.txt
   ```
   Or whichever subset of requirements you actually need
   (`anthropic`, `typedb-driver`, `python-dotenv`, `pyyaml`).

3. **TypeDB Cloud is the source of truth — nothing to migrate.**
   Both `valence_v4` (extraction + projection) and
   `valence_v4_ground_truth` are cloud DBs at
   `ip654h-0.cluster.typedb.com`. The `.env` credentials grant access
   from anywhere; the new machine reads/writes against the same
   cloud state.

4. **Duck Creek PDF** (`Duck Creek_07_2025.pdf`, 264 pages). On the
   source machine at:
   `C:/Users/olive/OneDrive/Documents/LegalVue/Credit Agreements/Duck Creek_07_2025.pdf`
   Same OneDrive should sync it on the new machine. If you re-extract
   text via PyMuPDF on the new box, the path passed to `fitz.open()`
   may differ — adjust accordingly.

5. **Gitignored runtime outputs** (regenerable):
   - `app/data/v4_eval_results/` — eval runner outputs
   - `app/data/intent_parser_log/` — per-invocation parser logs
   - `app/data/classification_measurements/` — classification harness
     outputs

   The pilot-acceptance run artifact IS in git at
   `docs/v4_pilot_acceptance_run/` so it transfers.

## Verify the new setup works

After cloning + restoring + setting up `.env`:

```bash
# Connect to TypeDB Cloud and confirm all three databases visible
py -3.12 -c "
import os, sys
from pathlib import Path
sys.path.insert(0, str(Path.cwd()))
from dotenv import load_dotenv
load_dotenv(Path('.env'), override=True)
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

Expected output:
```
databases: ['ResearcherTest1', 'auto-valence', 'mfn_ontology',
            'valence', 'valence_v4', 'valence_v4_ground_truth']
  rp_basket: 8
  jcrew_blocker: 1
  norm: 23
  defeater: 5
  party: 7
```

If those numbers match, you're picked up exactly where the source
machine left off.

Then run the harness as a sanity check:
```bash
py -3.12 -m app.services.validation_harness --deal 6e76ed06
```
Expected: A1 pass, A2-A4 fail (coverage, unchanged), A5 pass, A6 pass.

## Where to start the next session

Latest handover summary lives at [docs/v4_handover.md](../ValenceV3/docs/v4_handover.md)
(committed up through Prompt 10). Catch up reading order for a fresh
session:

1. `docs/v4_foundational_rules.md` (Rule 8.1 governs world-state)
2. `docs/v4_deontic_architecture.md` §6 (operations layer)
3. `docs/v4_handover.md` (post-Prompt-10 state — slightly stale, see
   commit log for Prompts 11-13 + Phase A)
4. `docs/v4_norm_id_rename_map.md` (Phase A reference, just landed)
5. `docs/v4_pilot_acceptance_run/README.md` (last pilot eval artifact)

Phase A is locked in. Phase B (data-model additions for temporal
anchors, reallocation relations, cross-covenant relations) is the
next prompt.
