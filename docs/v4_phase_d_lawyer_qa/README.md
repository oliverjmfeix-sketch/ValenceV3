# Phase D — Lawyer QA diagnostic (`lawyer_dc_rp` eval set)

> Phase D Commit 3 deliverable. Runs the 6 lawyer questions from
> `app/data/gold_standard/lawyer_dc_rp.json` through the synthesis_v4
> pipeline (D1 fetch + D2 two-stage filter+synthesize) and categorizes
> each result. The categorization scopes Phases E (extraction additions)
> and F (prompt iterations / fetch coverage extensions).

## Phase rename note (2026-04-29)

Per the agreed phase taxonomy, the work originally scoped under "Phase F"
in this document is properly **Phase D2** — synthesis-side iteration on
Phase D's foundation. Phase E remains extraction additions; Phase F
remains extraction prompt improvements. In-tree filenames and commit
messages for the synthesis-side work use the `phase_d2` prefix. The
"Phase F scope" / "Phase E scope" sections below remain as authored
for historical reference; the actual work landed on `v4-deontic` as
five commits prefixed `v4: Phase D2 commit N — …` (a8849a1, b4995ff,
345b46c, 7f79d90, 44aaab9).

## Phase D2 outcome (2026-04-29)

Final regression eval of `lawyer_dc_rp` against `valence_v4` with all
five Phase D2 commits applied, model `claude-sonnet-4-6`:

| Q | Category | Pre-D2 | Post-D2 | Notes |
|---|---|---|---|---|
| Q1 | builder_basket | PASS | PASS | unchanged |
| Q2 | unsub_distribution | PASS | PASS | unchanged |
| Q3 | reallocation | PARTIAL | PARTIAL | RP↔RDP fungible reallocation captured; tailored RDP carveouts (intercompany, reorganization, IPO) still not enumerated. Phase E extraction work. |
| Q4 | asset_sale_proceeds | PARTIAL | PARTIAL (richer) | Sweep tiers (5.75x→100% / 5.5–5.75x→50% / ≤5.5x→0%) explicitly enumerated with section refs ✓; de minimis attempt present but `individual_de_minimis_pct` is mis-stored in v3 extraction (0.3 vs gold 0.15); 2.10(c)(iv) / 6.05(z) / non-collateral / casualty / ordinary-course still missing — Phase E. |
| Q5 | total_capacity | PARTIAL | PARTIAL (richer) | Stage 1 PRIMARY picks rose from 12 → 13 (general_rdp_basket_permission flipped from SUPP/SKIP to PRIMARY) ✓; Stage 2 citations rose from 8 → 11; Stage 2 sum **still $260M** (Stage 2 LLM semantic-filter excludes RDP from dividend sum despite the v4 picker rule, despite the v4-aware synthesis_guidance). general_investment_basket re-extraction deferred per Phase C. |
| Q6 | ratio_basket_application | PASS | PASS | unchanged; defensive conditional in picker guidance and the new single-norm-applicability case in PRIMARY definition both held. |

**Score: 3 PASS / 3 PARTIAL.** The plan's 4 PASS / 2 PARTIAL target was not
reached because Stage 2 continues to apply a semantic filter ("RDP basket
= debt purpose, not dividend capacity") even when synthesis_guidance
explicitly instructs otherwise. Both partials are visibly richer than the
pre-D2 baseline.

Cost / latency for the post-D2 run: **$0.74 total, 272s** (vs $0.60 / 267s
pre-D2). Slight cost increase from longer prompts (added picker
guidance + v4-aware synthesis_guidance + provision_level_entities in
the Stage 2 payload). Per-question median: $0.12, ~45s.

Eval JSON: `lawyer_dc_rp_20260429T110700Z.json` in this directory.

### Residual gaps (Phase E candidates)

Q5 — Stage 2 RDP-exclusion semantic override: closing this requires
either (a) a Stage 2 prompt restructure that prevents the LLM from
applying its own purpose-based filter, OR (b) re-extraction of v3 with
capacity_category aligned to v4 reallocable scope. Both are outside
Phase D2's scope.

Q4 — sweep upstream mechanics (2.10(c)(iv) product-line carveout, 6.05(z)
unlimited basket carveout, non-collateral, ordinary-course, casualty)
are not authored as v4 norms today; the Q4 gold answer references them.
Phase E extraction work.

Q4 — `individual_de_minimis_pct` stored as 0.3 (30%) on Duck Creek's
asset_sale_sweep entity; gold says 15%. `individual_de_minimis_usd` is
null; gold says $20M. Re-extraction with corrected de_minimis values
needed.

Q3 — `basket_reallocates_to` v3 relation has 0 instances on Duck Creek;
no extraction question targets reallocation language directly. Phase E.

## Phase E outcome (2026-04-29)

Phase E ("extraction additions") landed in 6 commits on `v4-deontic`
(`2d739c2` → `5ce642c` → `3428d48` → final eval). Total spend
$4.32 — $1.84 commit 1 (rp_el_reallocations diagnostic) + $1.73
commit 3 (carveout extraction) + $0.75 commit 5 (lawyer eval) +
trivial commit-0 verification cost.

### Final lawyer_dc_rp regression (post-Phase-E)

Eval JSON: `lawyer_dc_rp_20260429T131018Z.json` in this directory.

| Q | Pre-D2 | Post-D2 | Post-E | Notes |
|---|---|---|---|---|
| Q1 | PASS | PASS | PASS | — |
| Q2 | PASS | PASS | PASS | — |
| Q3 | PARTIAL | PARTIAL | PARTIAL | Reallocation extraction question existed pre-Phase-E (rp_el_reallocations from 2026-03-13). Re-running it surfaces 4 LLM-identified reallocation paths but storage fails (capacity_effect cardinality + general_investment_basket missing). Synthesis already covers RP↔RDP fungible reallocation via the action_scope marker; the v3 relation isn't strictly required. Tailored carveouts (intercompany, reorganization, IPO) remain unenumerated. |
| Q4 | PARTIAL | PARTIAL (richer) | PARTIAL (data richer; surface unchanged) | Phase E populated 4 new carveout attrs on `asset_sale_sweep` (`permits_product_line_exemption_2_10_c_iv=true`, `permits_section_6_05_z_unlimited=true`, `section_6_05_z_threshold=6.0`, `product_line_2_10_c_iv_threshold=null`). Synthesis_guidance updated to instruct Stage 2 to surface these. Stage 2 still does not reference 2.10(c)(iv) or 6.05(z) in answers — same LLM-semantic-override pattern as Q5. |
| Q5 | PARTIAL | PARTIAL (richer) | PARTIAL (unchanged) | Phase E doesn't address Q5; deferred. |
| Q6 | PASS | PASS | PASS | — |

**Score: 3 PASS / 3 PARTIAL** — same headline as Phase D2, but Q3
diagnosis is now precise (storage-layer bugs + Phase C deferred entity)
and Q4's data graph is richer (4 carveout attrs available, even if
Stage 2 doesn't surface them).

### What landed

- **Commit 0** (`2d739c2`) — Incremental extraction infrastructure +
  CLI on `extraction.py`. Adds optional `question_ids` filter to
  `extract_covenant()` and a `__main__` block that fetches the
  universe from Railway and runs only the listed question_ids
  locally. Lazy PDF parser import (PyMuPDF not in local venv).
- **Commit 1** (`e6f3d32`) — Diagnoses why Duck Creek has zero
  `basket_reallocates_to` v3 entities. Finding: extraction question
  exists; LLM identifies 4 paths; storage fails on
  capacity_effect cardinality (RP↔RDP pair) and missing
  general_investment_basket entity (Phase C deferred). Doc:
  `docs/v4_phase_e/q3_reallocation_diagnosis.md`.
- **Commit 2** (`62e2459`) — Schema-additive: 4 new attrs on
  `asset_sale_sweep` for 2.10(c)(iv) and 6.05(z) carveouts.
  Migration script + question loader. 4 new scalar questions
  (`rp_l24`-`rp_l27`) authored + linked to category L + annotated.
- **Commit 3** (`5ce642c`) — Incremental extraction run for
  rp_l24-rp_l27. 3 of 4 attrs populated correctly on Duck Creek.
  Cost: $1.73 (entity-list/scalar costs are higher than the Phase 1
  estimate; revised projection in
  `docs/v4_phase_e/q4_carveout_extraction_run.md`).
- **Commit 4** (`3428d48`) — Category L synthesis_guidance extended
  to instruct Stage 2 to surface the new carveout flags. Reframed
  from the original plan: skipped projection-rule additions and the
  deferred `event_governed_by_norm` relation (these would be
  architectural enrichments without immediate Q4 closure benefit).
- **Commit 5** (this commit) — Lawyer eval re-run + this outcome
  section.

### Residual gaps after Phase E (genuine Phase F / future-phase work)

- **Q3 Stage 2 PASS** requires:
  - Storage-layer fix for `basket_reallocates_to` capacity_effect
    cardinality (Phase F).
  - `general_investment_basket` v3 entity extraction (Phase C
    deferred re-extraction window — gated on a deal worth re-running).
- **Q4 Stage 2 PASS** requires Stage 2 to surface the now-available
  2.10(c)(iv)/6.05(z) carveout flags. The data is in the v4 graph
  (verified). Possible routes: more-directive synthesis_guidance
  prose, OR promoting the carveouts to first-class v4 norms via
  projection rules + adding the deferred `event_governed_by_norm`
  relation type from Phase B Commit 3 (would let Stage 2 see them as
  citable norms, not just attrs on a sub-entity). Phase F or a
  dedicated event-class governance phase.
- **Q4 `product_line_2_10_c_iv_threshold` LLM extraction quality** —
  rp_l25 returned null on Duck Creek despite the gold answer
  specifying 6.25x. Prompt iteration (Phase F) likely closes this.
- **Q4 `individual_de_minimis_pct` extraction quality** — already
  noted in Phase D2 outcome; carries forward.
- **Q5 Stage 2 RDP-exclusion semantic override** — same as Phase D2
  residual; not addressed by Phase E.

### Cost-shape calibration

The Phase 1 investigation estimated incremental extraction at
$0.05/question. The Phase E reality:

- Entity-list question (`rp_el_reallocations`): $1.84/question.
- 4 scalar questions in a single batch: $0.43/question.

The scalar batching mode runs each question against the full 446K-char
universe per call when only a few are filtered. Phase F should consider
narrowing the universe slice for incremental runs, or document this
cost-shape so future Phase E-class plans don't underestimate.

Total Phase E spend: $4.32, well below the strategy (a) full
re-extraction cost (~$31).

## Eval run summary

- **Eval set:** `lawyer_dc_rp` (6 questions, Duck Creek `6e76ed06`)
- **DB:** `valence_v4`
- **Model:** `claude-sonnet-4-6`
- **Cost:** **$0.5972 total** (~$0.10/question)
- **Latency:** **267s total** (~45s/question)
- **Output JSON:** `lawyer_dc_rp_20260428T173848Z.json`
- **Verbatim:** `verbatim.txt` (gold | v4 answer | Stage 1 PRIMARY picks)

## Per-question verdict

| # | Question | Category | Verdict | Cost | S1 picks | S2 cites |
|---|---|---|---|---:|---:|---:|
| Q1 | Builder basket structure & start date | builder_basket | **PASS** | $0.10 | 6 P / 9 S / 13 K | 6 |
| Q2 | Unrestricted Sub equity dividend | unsub_distribution | **PASS** | $0.08 | 1 P / 4 S / 23 K | 2 |
| Q3 | Reallocation paths to RP | reallocation | **PARTIAL** | $0.11 | 3 P / 5 S / 20 K | 6 |
| Q4 | Asset sale proceeds → dividends | asset_sale_proceeds | **PARTIAL** | $0.10 | 2 P / 11 S / 15 K | 5 |
| Q5 | Total quantifiable dividend capacity | total_capacity | **PARTIAL** | $0.12 | 12 P / 5 S / 11 K | 8 |
| Q6 | Negative-EBITDA dividend at 6.0x ratio | ratio_basket_application | **PASS** | $0.09 | 1 P / 3 S / 24 K | 3 |

**Score: 3 PASS, 3 PARTIAL, 0 FAIL.** No question hit a complete
extraction-gap that broke the answer entirely; the partials are
incremental detail gaps where v4 synthesis got the load-bearing
finding right but missed quantitative specifics or one of multiple
applicable provisions.

## What worked (PASS detail)

**Q1 — builder_basket.** V4 synthesis correctly identified the three
"greatest of" tests (50% CNI, retained ECF, 140% LTM EBITDA-FC),
named the starter floor ($130M / 100% EBITDA), cited the start-date
anchor (closing-date fiscal-quarter-start). Cited 6 norms across the
builder structure. **Richer than gold** — adds detail about additive
sources (equity proceeds, retained asset sale proceeds, investment
returns, debt-to-equity conversion) that gold elides.

**Q2 — unsub_distribution.** Direct yes, cited 6.06(p) p.198, noted
`capacity_category = "categorical"`, added correct nuance that JCrew
blocker restricts *designation* of new unsubs holding Material IP
but doesn't restrict *distribution* from already-designated unsubs.

**Q6 — ratio_basket_application.** Correctly applied the no-worse
test to the negative-EBITDA scenario. Reasoned that removing
negative-EBITDA business *increases* Consolidated EBITDA → reduces
pro forma leverage ratio → no-worse branch satisfied even though
6.0x exceeds the 5.75x absolute threshold. This is the v3 synthesis
lawyer-question failure mode that motivated Phase D — and v4
synthesis nails it.

## What's incomplete (PARTIAL detail + categorization)

### Q3 — reallocation gaps

| Gap | Category | Evidence |
|---|---|---|
| 6.03(y) Investment basket → RP reallocation not surfaced | **Extraction-gap** | Duck Creek has **0 `basket_reallocates_to` v3 entities**. Phase C handover documents reallocations as deferred (no v3 data). 6.03(y)'s reallocability is a v3 attribute on `general_investment_basket` that wasn't extracted as a relation. |
| Tailored RDP carveouts (intercompany, reorganization/IPO) not surfaced | **Extraction-gap** | These are scalar answers in v3 not currently projected as norm subtypes. Either (a) project as new norm_kinds, or (b) extend the general_rdp_basket norm with attributes capturing the carveout structure. |

### Q4 — asset_sale_proceeds gaps

| Gap | Category | Evidence |
|---|---|---|
| Specific sweep_tier thresholds (5.75x → 100%, 5.5x–5.75x → 50%, ≤5.5x → 0%) | **Synthesis-gap (fetch coverage)** | `sweep_tier` entity has 3 instances in `valence_v4` with `leverage_threshold` + `sweep_percentage` + full `source_text` populated. The data is THERE — `fetch_norm_context` doesn't include provision-level entities (sweep_tier, de_minimis_threshold) that aren't directly linked to a norm. Fetch-extension fix. |
| De minimis thresholds ($20M/15% individual, $40M/30% annual) | **Extraction-gap** | `de_minimis_threshold` entity type **not in v4 schema**. Need to add the entity type + extraction question for asset-sale de minimis carveouts. |
| 6.05(z) product-line sale exemption (6.25x ratio + product-line AND test) | **Synthesis-gap (prompt coverage)** | The relevant attributes (`has_no_worse_test`, `ratio_threshold`) are on `general_investment_basket` / equivalent in v3, accessible via `norm_extracted_from`. Synthesis didn't surface this carve-out. Stage 2 prompt iteration. |

### Q5 — total_capacity gaps

| Gap | Category | Evidence |
|---|---|---|
| general_rdp_basket ($130M/100% EBITDA) not summed | **Synthesis-gap (Stage 1)** | `general_rdp_basket_permission` exists as a v4 norm with `cap_usd: 130000000`, `action_scope: reallocable`, `capacity_composition: fungible`. Stage 1 picked it as SUPPLEMENTARY, not PRIMARY, for a capacity question. Stage 2 then mentioned it in passing but didn't include it in the $260M sum. Stage 1 prompt fix: "for capacity questions, mark all reallocable baskets (across RP and RDP covenants) as PRIMARY." |
| general_investment_basket ($130M/100% EBITDA) not extracted | **Extraction-gap** | `general_investment_basket` has **0 v3 entities** in `valence_v4` for Duck Creek. The norm rule exists (`rule_conv_general_investment_basket`) but doesn't fire because no extracted v3 entity. Either (a) re-extract Duck Creek (violates Phase C constraint, costly) or (b) accept this gap and confirm next deal extraction includes general_investment_basket. |
| "Plus all assets that don't secure Loans, non-EBITDA producing assets" (the no-worse test → unlimited capacity for divestitures) | **Synthesis-gap (Stage 2)** | `ratio_rp_basket_permission` was in the eval and has the no-worse logic available (Q6 used it correctly). Q5 didn't combine the ratio basket's unlimited-conditional capacity with the dollar-cap baskets. Stage 2 prompt fix: "for capacity questions involving asset divestitures, evaluate the ratio basket's no-worse branch as a separate capacity dimension." |

## Diagnostic categorization (rolled up)

| Category | Q3 | Q4 | Q5 | Total |
|---|:-:|:-:|:-:|:-:|
| **Extraction-gap** (Phase E) | 2 | 1 | 1 | **4** |
| **Synthesis-gap fetch coverage** (Phase F) | 0 | 1 | 0 | **1** |
| **Synthesis-gap prompt** (Phase F) | 0 | 1 | 2 | **3** |

## Phase E scope (extraction additions)

Ranked by impact on the lawyer question set:

1. **`de_minimis_threshold` entity** — Q4. New entity type in v4 schema
   covering individual-transaction and annual de minimis carveouts.
   Owns `dollar_amount_usd`, `ebitda_pct`, `period` (individual /
   annual), `applies_to` (asset_sale / casualty_event / etc.). Add an
   extraction question that captures the threshold structure from §6.05
   exception language. Deal-coverage: would surface for any RP/sweep
   covenant.

2. **`basket_reallocates_to` extraction** — Q3. Phase B added the
   relation type but Duck Creek extraction didn't populate it. Either
   (a) author a new extraction question that targets reallocation
   language explicitly (current questions don't), or (b) post-process
   from existing extracted attributes (`reduces_rp_basket`,
   `reduces_investment_basket`, etc.) which encode the same data
   point-wise but not as a relation.

3. **Tailored RDP carveouts** — Q3. Either new norm subtypes
   (`intercompany_rdp_permission`, `reorganization_rdp_permission`,
   `ipo_rdp_permission`) or boolean attributes on `general_rdp_basket`
   capturing the carveout structure. Aligned with the v3 ontology's
   "S/T" categories which already have synthesis_guidance.

4. **`general_investment_basket` re-extraction** (deferred). The norm
   rule exists but no v3 entity was extracted for Duck Creek. Either
   re-extract Duck Creek (violates Phase C constraint, ~$15) or
   confirm next deal's extraction includes the general_investment
   basket. Defer until next-deal extraction is being planned anyway.

## Phase F scope (synthesis prompt + fetch coverage)

Ranked by impact:

1. **Extend `fetch_norm_context` to include provision-level entities**
   — Q4. Right now `fetch_norm_context` walks norms only; entities
   like `sweep_tier` (3 instances), `investment_pathway` (6 instances),
   `unsub_designation` (1 instance) live one hop further out and don't
   appear in synthesis context. Add a second fetch path: walk
   `provision_has_extracted_entity` for the deal's `rp_provision`,
   classify each by entity type, attach to context as
   `provision_level_entities`. Stage 2 prompt then references them
   alongside norms.

2. **Stage 1 prompt — capacity-question hint** — Q5. Add to the
   classifier system prompt: "For capacity-aggregation questions
   (containing 'total', 'capacity', 'how much', 'all baskets'), mark
   every basket-permission norm with `capacity_composition: fungible`
   AND `action_scope: reallocable` as PRIMARY, regardless of covenant
   (RP, RDP, or investment)."

3. **Stage 2 prompt — capacity-arithmetic instruction** — Q5. Add to
   the synthesis system prompt: "For capacity questions, enumerate
   each PRIMARY basket-permission norm by name, list its `cap_usd`
   and `cap_grower_pct`, then sum across reallocable + fungible
   baskets explicitly. Show the arithmetic."

4. **Stage 2 prompt — sweep_tier enumeration** — Q4. Add to the
   asset-sale section of synthesis_guidance: "When `sweep_tier`
   entities are present, enumerate each tier's `leverage_threshold`
   and `sweep_percentage` in order from highest to lowest; cite the
   `section_reference` for each."

5. **Stage 2 prompt — ratio basket no-worse capacity dimension** —
   Q5. Add to category G synthesis_guidance: "When evaluating
   capacity questions involving asset divestitures, separately
   evaluate the ratio basket's unlimited-conditional capacity for
   any divestiture that improves the leverage ratio (negative-EBITDA
   divestitures, non-collateral asset disposals). State this as a
   separate capacity branch alongside fixed-dollar baskets."

## Implication: which approach was right (synthesis-first vs extraction-first)?

The Phase D framing — "synthesis-first beats extraction-first" — is
strongly validated by this diagnostic:

- **3/6 questions PASS without any extraction additions.** The data
  was always in v4; v3 synthesis was failing on retrieval/relevance
  ranking, not storage. Adapting the two-stage filter pattern fixed
  this directly.
- **PARTIAL gaps split 4 extraction / 4 synthesis.** Without running
  the synthesis adaptation, we'd have guessed extraction was the
  bottleneck and built ~4-6 new extraction questions. The diagnostic
  shows half of the partials are synthesis-side fixes that take
  hours, not extraction-side fixes that take days + $15.
- **Phase E is now small and tightly scoped.** 4 concrete extraction
  additions (de_minimis_threshold + basket_reallocates_to extraction
  question + tailored RDP carveouts + general_investment_basket
  re-extraction). Three of these are schema-additive; one is deferred
  until next-deal extraction.
- **Phase F is even smaller.** Mostly prompt iteration on the existing
  synthesis_v4 service + one fetch-coverage extension. Estimated
  effort: 1-2 commits.

## What this run does NOT establish

- **Other deals** (next deals beyond Duck Creek). All findings here
  are deal-specific. A second deal's run could surface different gaps.
- **Other covenant types.** v4 only has RP categories seeded. MFN/DI
  Phase C work would extend the corpus.
- **Other lawyer-question sets.** `xtract_dc_rp_mfn` (22 questions)
  and `xtract_dc_di` (10 questions) are v3-era eval sets. Adapting
  them to the v4 path is a follow-on diagnostic.
- **Quality at scale.** 6 questions is a small sample; statistically,
  the PASS rate could swing under more questions.

## Cost / latency baseline

For future Phase F prompt-iteration runs:

| Metric | Value |
|---|---|
| Cost per question (Sonnet 4.6) | ~$0.10 |
| Cost per full eval (6q) | ~$0.60 |
| Latency per question | ~45s |
| Latency per full eval | ~4.5 min |
| Tokens per question (avg in/out) | ~6.5K / 1.0K |

Opus 4.7 alternative: ~$0.50/question, 5x cost — only worth running
if Sonnet quality regresses with prompt iterations.

## Reproducing this run

```bash
TYPEDB_DATABASE=valence_v4 \
  C:/Users/olive/ValenceV3/.venv/Scripts/python.exe \
  -m app.services.synthesis_v4 \
  --deal 6e76ed06 \
  --eval-set lawyer_dc_rp
```

Output JSON timestamps the run; archive in this directory.

## Phase G outcome (2026-04-29)

Phase G ("synthesis architecture diagnostic") landed in 6 commits on
`v4-deontic`. Total spend ~$1.60 — $0.85 (Commit 1 diagnostic) +
$0.15 (Commit 3 verification) + $0.75 (Commit 5 eval).

### Final lawyer_dc_rp regression (post-Phase-G)

Eval JSON: `lawyer_dc_rp_20260429T150437Z.json`.

| Q | Pre-D2 | Post-D2 | Post-E | Post-G | Notes |
|---|---|---|---|---|---|
| Q1 | PASS | PASS | PASS | PASS | builder_rdp_basket_permission now cited (Phase G payload sort effect) |
| Q2 | PASS | PASS | PASS | PASS | unchanged |
| Q3 | PARTIAL | PARTIAL | PARTIAL | PARTIAL | Phase G payload sort surfaces "shared general-purpose pool" framing; tailored carveouts still not enumerated |
| Q4 | PARTIAL | PARTIAL (richer) | PARTIAL (data richer) | PARTIAL | 2.10(c)(iv)/6.05(z) still not enumerated by name; mechanism (a)/(c)-attribute-level per Phase G commit 1 diagnostic |
| Q5 | PARTIAL | PARTIAL (richer) | PARTIAL | PARTIAL | builder_rdp_basket_permission now cited; conclusion still $260M (general_rdp not summed); Phase G commit 1 confirmed mechanism (c) data positioning, bounded fix in commit 3 didn't fully reproduce V4 probe behavior |
| Q6 | PASS | PASS | PASS | PASS | unchanged |

**Score: 3 PASS / 3 PARTIAL** — same headline as Phase E. Phase G's
bounded payload-sort fix changed citation patterns without changing
pass/fail outcomes. This is the expected "outcomes are evidence not
target" Phase G framing.

### Architectural deliverables

- **Mechanism (c) data positioning confirmed for Q5**
  (`docs/v4_synthesis_architecture.md`). Stage 2's citation behavior
  is sensitive to payload position; the architecture's declared
  authority hierarchy doesn't hold uniformly under default ordering.
- **Adaptation review** (`docs/v4_synthesis_adaptation_review.md`)
  categorizes the v3-to-v4 pattern adaptation: Stage 1 fits with
  iteration distance; Stage 2 has structural mismatch + iteration
  distance; v3 entity bridge is iteration distance; authority
  hierarchy under conflict is structural mismatch.
- **Bounded fix applied** (`fetch_norm_context` payload sort by
  `action_scope: 'reallocable'` first). Aggregate eval shows Q1 cites
  RDP (improvement), Q3 cites RDP, Q5 cites builder_rdp_basket_permission
  (different RDP norm). Sort changed patterns without flipping pass/
  fail.
- **Entity inventory** (`docs/v4_entity_inventory.md`) — 85 of 198
  schema entity types extracted on Duck Creek; 2 RP-relevant true
  gaps (general_investment_basket, amendment_threshold);
  completeness definition for future extraction work.

### Phase H scope candidates (out of Phase G)

1. **Generalized relevance scoring.** Per-question relevance score
   over all norms, ordering payload by score. The bounded sort in
   Phase G doesn't address Q4 carveout enumeration or fully
   reproduce the V4 probe; a question-aware score would.
2. **Stage 2 must-cite layer.** Adding a structured directive to
   Stage 2's payload listing norms that MUST appear in citations,
   independent of payload position.
3. **Wholesale v3-to-v4 vocab rewrite for synthesis_guidance.**
   Phase D2 commit 3 did category N; remaining 17 categories are
   per-category iterative work.
4. **`general_investment_basket` re-extraction.** Phase C deferred;
   re-extraction window opportunity.
5. **V5 attribute-level probe for Q4.** Test whether elevating
   asset_sale_sweep carveout flags out of nested attribute position
   surfaces 2.10(c)(iv)/6.05(z) by name. Disambiguates Q4 between
   (a) and attribute-level (c).
