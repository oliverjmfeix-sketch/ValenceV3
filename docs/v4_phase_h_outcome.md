# Phase H outcome — extraction methodology audit

> Phase H is **extraction-side architectural audit** parallel to
> Phase F (storage/schema/conventions) and Phase G (synthesis
> architecture). Six commits on `v4-deontic`. Phase H's deliverable
> is the audit + bounded compounding fixes; the lawyer eval was NOT
> re-run (per locked scope: extraction discipline doesn't change
> synthesis behavior).

## Summary

| Workstream | Outcome |
|---|---|
| Survey infrastructure | ✓ complete; re-runnable |
| Audit (Method 1, broad, 8 dimensions) | ✓ complete; 7 compounding fixes identified |
| Authoring + traceability discipline | ✓ landed; 99.2% conformance |
| SSoT discipline | ✓ documented; no violations found |
| Convention enforcement | ✓ landed; Convention 1 locked as decimal |
| Validation harness | ✓ baseline preserved across all 6 commits |

Total cost: $0 (read-only audit + documentation + upsert-pattern
data updates; no re-extraction triggered).

Within the $30 budget ceiling and well below the planned $0-3 range.

## Per-commit deliverables

### Commit 1 (`1c870f8`) — Extraction survey infrastructure

**Files added:**
- `app/scripts/phase_h_extraction_survey.py` — re-runnable survey
- `docs/v4_phase_h_extraction_survey/snapshot_<timestamp>.json` —
  audit data (261KB; 238 questions enumerated)

**Key findings:**
- 238 ontology_questions on `valence_v4`, all RP covenant
- 117/238 (49%) have empty `extraction_prompt` (large finding)
- Only 1/238 enforces percentage decimal convention
- 6 questions with no category linkage

### Commit 2 (`8771cd9`) — Extraction methodology audit

**Files added:**
- `docs/v4_phase_h_extraction_audit.md` — comprehensive findings doc

**8 audit dimensions categorized:**
- Aligned: Dimensions 4 (mostly), 7 (storage), 8 (mostly)
- Compounding fix: Dimensions 1 (#1), 2 (#2,#3), 3 (#4,#5,#6), 4 (#7)
- Non-compounding: ~125 deferrals (mostly empty-prompt class +
  rp_l25 + jc_t gaps)
- SSoT violation: 0
- Convention violation: Dimension 3 systemic

**Compounding fixes total: 7** (within ≤15 cap; well below ≤5/commit
limit).

### Commit 3 (`c99465a`) — Authoring + traceability discipline

**Files added:**
- `app/scripts/phase_h_validate_extraction_questions.py` — validation
  utility (re-runnable; flags non-conforming questions)
- `docs/v4_extraction_methodology.md` — canonical authoring docs

**Fixes #1-3 landed:**
- Empty-prompt design choice documented
- Canonical four-beat prompt template documented
- Validation utility with 4 conformance checks

**Conformance: 99.2% on valence_v4** (only jc_t1_34 + jc_t2_30 fail
on category_link — non-compounding deferred).

### Commit 4 (`a31d8b4`) — SSoT discipline

**Files updated:**
- `docs/v4_extraction_methodology.md` — added "Entity_list question
  pattern" + "SSoT division summary" sections

**Fix #7 landed:**
- Entity_list pattern documented (target via question-level attrs
  vs. scalar's question_annotates_attribute relations)
- SSoT division summary table maps every concern to its
  authoritative location

**No SSoT violations found.** Phase H confirms the architecture's
graph-vs-Python division is principled.

### Commit 5 (`c464810`) — Convention enforcement

**Files updated:**
- `docs/v4_attribute_conventions.md` — Convention 1 LOCKED as decimal
- `app/data/questions.tql` — rp_f13 + rp_n2 prompts updated
- `app/services/extraction.py` — `_normalize_v3_data()` call
  bypassed
- `app/services/v3_data_normalization.py` — docstring updated to
  reflect Phase H status

**Fixes #4-6 landed:**
- Convention 1 locked as decimal (was MIXED in Phase F)
- Two non-conforming prompts updated (rp_f13, rp_n2) via upsert
- v3_data_normalization bypassed for v4 extraction (was wrong
  direction post-lock)

**Migration script:**
- `app/scripts/phase_h_update_prompts_decimal_convention.py` —
  idempotent prompt upsert

### Commit 6 (this commit) — Phase H outcome + push

**Files added:**
- `docs/v4_phase_h_outcome.md` (this file)

## Validation harness baseline (final, post-Phase-H)

A1=pass, A4 m=45 s=6 mm=0, A5=pass aggregate_accuracy=1.0, A6=pass.

Identical to pre-Phase-H baseline; preserved across all 6 commits.

## What Phase H changed in extraction methodology

- **Extraction methodology architecture is now a documented SSoT.**
  `docs/v4_extraction_methodology.md` is the canonical reference
  for: question authoring path, required attrs, prompt template,
  empty-prompt convention, value conventions, storage interface,
  versioning + universe handling (deferred), entity_list pattern
  rationale, and SSoT division summary table.
- **Convention 1 (percentage) is locked as decimal.** Previously
  MIXED per Phase F; now canonical decimal across all forward-looking
  authoring. v3_data_normalization's wrong-direction conversion
  is bypassed.
- **Authoring conformance is enforceable.** Validation utility
  (`phase_h_validate_extraction_questions.py`) checks required
  attrs, category linkage, target traceability, covenant_type
  validity. 99.2% conformance on `valence_v4` today.
- **Audit data is re-runnable.** `phase_h_extraction_survey.py`
  produces JSON snapshot for any future audit or comparison.
- **Compounding-vs-non-compounding triage rule applied.** 7
  compounding fixes landed; ~125 non-compounding findings deferred
  with documentation.

## What Phase H deferred (now explicit known-gaps)

- 117 questions with empty `extraction_prompt` — per-question
  authoring work; non-compounding individually. Documented as
  "empty-prompt convention" in methodology architecture.
- rp_l25 prompt iteration for `product_line_2_10_c_iv_threshold`
  null result — Phase E single-deal finding; per-question work.
- 5 specific empty-prompt percentage questions (rp_c5, rp_f3,
  rp_f5, rp_f6, rp_j5, rp_t21) — per-question authoring; deferred.
- jc_t1_34, jc_t2_30 category-link gaps — 2 specific questions;
  per-question fix; deferred.
- Versioning discipline (version_id attrs on questions) —
  post-pilot per locked scope.
- Universe-slice infrastructure for cost reduction — post-pilot
  per locked scope.

## Phase I scope candidates (post-pilot if needed)

If any of these become urgent, they can shape a future Phase I:

1. **Per-question authoring for empty prompts.** 117 questions
   need prompts authored or be reclassified as concept-applicability-
   routed. Per-question work; ~30 min per prompt. ~60 hours of
   authoring effort if all 117 needed prompts.
2. **Universe-slice infrastructure.** Phase E commit 3 measured
   ~$0.43/question because each question runs against full universe.
   Per-question or per-category universe slicing could drop ~10x.
   ~3-5 commits of infrastructure work.
3. **Versioning discipline.** Add `version_id` to ontology_question
   if iteration becomes frequent. Post-pilot.
4. **Re-extraction with corrected percentage convention.** With
   Convention 1 locked decimal and v3_data_normalization bypassed,
   future Duck Creek re-extractions will produce decimal values
   for cap_grower_pct, basket_grower_pct, etc. Today's data has
   mixed forms; a clean re-extraction would replace them. Post-
   pilot decision.

## Branch state at Phase H end

- Branch: `v4-deontic`
- HEAD: this commit (Phase H commit 6)
- Commits ahead of `origin/v4-deontic`: 6
- Push planned at end of Phase H (per locked scope: end-of-phase
  push only).
