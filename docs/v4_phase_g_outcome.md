# Phase G outcome — synthesis architecture diagnostic

> Phase G is **architectural diagnostic of the synthesis layer** plus
> **entity inventory completeness** plus **lawyer eval re-run as
> evidence**. Six commits on `v4-deontic`. Phase G's deliverable is
> architectural understanding and discipline; the eval re-run
> documents the state, not a target.

## Summary

| Workstream | Outcome |
|---|---|
| A — Synthesis authority audit | ✓ complete; mechanism categorized per question |
| B — v3-to-v4 adaptation review | ✓ complete; structural mismatch found |
| C — SSoT discipline | bounded fix applied; larger work → Phase H |
| D — Entity inventory + completeness | ✓ complete; 2 RP gaps documented |
| Eval re-run | 3 PASS / 3 PARTIAL (unchanged from Phase E) |
| Validation harness | ✓ baseline preserved across all 6 commits |

Total cost: ~$1.60 ($0.85 commit 1 diagnostic + $0.15 commit 3
verification + $0.75 commit 5 eval). Within the planned $1-3 range.

## Synthesis architecture as it stands post-Phase-G

### Authority hierarchy (declared)

1. Hard constraints in Python prompts (system prompt JSON output
   schema, classification buckets) — invariant.
2. `synthesis_guidance` per matched ontology_category — domain
   authority.
3. `stage1_picker_guidance` per matched ontology_category — picker bias.
4. Norm attribute values — what the data says.
5. LLM defaults — implicit; unconstrained beyond the above.

### Authority hierarchy (empirical)

The audit found:

- **Stage 1's hierarchy is honored.** Phase D2 commit 3 verified
  `stage1_picker_guidance` for category N successfully flips
  `general_rdp_basket_permission` from SUPPLEMENTARY/SKIP to
  PRIMARY. Phase G commit 1 V4 probe re-confirmed.

- **Stage 2's hierarchy is graded.** `synthesis_guidance` strongly
  affects framing and answer structure (Phase G V1 and V2 probes
  showed measurable effects when guidance was filtered out). But
  guidance does NOT consistently override LLM attention defaults
  for citation behavior. The Q5 RDP exclusion phenomenon is the
  load-bearing example: Stage 2 doesn't cite
  `general_rdp_basket_permission` under default payload ordering
  even with explicit guidance instructing inclusion. V4 probe
  showed reordering the norm to a prominent payload position flips
  this behavior — confirming mechanism (c) data positioning.

- **Fetch helpers don't sort by relevance.** They return data in
  TypeDB query order (arbitrary). Synthesis_v4's behavior depends
  on this order. Phase G commit 3 added a deterministic sort by
  `action_scope: 'reallocable'` first, addressing the bounded case.

### v3-to-v4 pattern adaptation

Per `docs/v4_synthesis_adaptation_review.md`:

- **Stage 1 classification:** fits with iteration distance (Phase
  D2 commit 3 already applied).
- **Stage 2 reasoning + answer:** structural mismatch. The v3
  pattern's authority hierarchy doesn't hold in v4 for citation
  behavior. Bounded fix in Phase G commit 3; larger fix in Phase H.
- **v3 entity bridge (extracted_from):** iteration distance.
  Phase D2 commit 3 did category N's vocab; remaining 17 are
  per-category iterative work.
- **Authority hierarchy under conflict:** structural mismatch.
  Phase G commit 3's payload sort makes the hierarchy enforceable
  for the bounded case.

## Phase G in-scope changes

- **Phase G commit 3:** payload sort in `fetch_norm_context` by
  `action_scope` and capacity-bearing markers. Stable, deterministic,
  idempotent. Read-only at the fetch layer; doesn't affect
  projection or storage. Aggregate eval shows it changed citation
  patterns for Q1, Q3, Q5 without flipping pass/fail.

The bounded fix is principled (uses schema's reallocable marker)
but didn't reproduce the V4 probe's behavior on Q5 specifically.
The probe pushed multiple RDP norms to the very front; the sort
puts general_rp_basket BEFORE general_rdp_basket within the same
sort tier. Q5's conclusion still says $260M floor.

This is the expected outcome given Phase G's framing: outcomes are
evidence not target. The architectural finding (mechanism c) stands;
the bounded fix is documented; further work is Phase H scope.

## Lawyer eval results — outcome documentation

Same 3 PASS / 3 PARTIAL headline as Phase E. Per-question changes
from Phase G commit 3's payload sort:

- Q1 builder_basket: cite count up; builder_rdp_basket_permission
  now cited (Phase G effect).
- Q3 reallocation: better framing ("shared general-purpose pool
  drawn down equally by RP, RDP, investments"); cites RDP basket;
  tailored carveouts still not enumerated.
- Q5 total_capacity: cites builder_rdp_basket_permission (different
  RDP norm than expected); conclusion still $260M.

Q4 unchanged: 2.10(c)(iv) and 6.05(z) still not enumerated by name.

## Phase H scope candidates (recommended priority order)

Per Phase H triage rule (bounded scope + compounds into future work):

### Tier 1 — strong Phase H candidates

1. **Generalized relevance scoring with question awareness.**
   Question text + target categories drive a per-norm relevance
   score; payload ordered by score. Replaces the bounded
   action_scope-only sort. Bounded scope (~3-5 commits). Compounds
   because multiple subsequent question types benefit, and
   future eval question additions auto-benefit.
2. **Stage 2 must-cite layer.** Add `must_cite_norm_ids` to Stage 2
   payload as a fourth structured layer (alongside primary,
   supplementary, proceeds_flows, provision_level_entities). Stage 2
   prompt instructs explicit citation of every must-cite. Bounded
   scope (~2-3 commits). Compounds because authority hierarchy
   becomes enforceable across question types.

### Tier 2 — Phase H if scope budget allows; post-pilot otherwise

3. **V5 attribute-level probe for Q4.** One-question diagnostic
   that flattens asset_sale_sweep carveout flags to top-level
   `provision_level_entities` keys. If V5 surfaces 2.10(c)(iv) /
   6.05(z) by name, the architectural fix is to restructure
   provision_level_entities payload. ~$0.13 + 1 small commit.
   Compounds weakly (Q4-specific finding).
4. **Wholesale v3-to-v4 vocab rewrite for synthesis_guidance.**
   17 categories x ~30 minutes per audit and rewrite. Per-category
   work that doesn't compound to a single commit. Better as
   per-category iterations driven by future eval residuals.

### Tier 3 — post-pilot (not Phase H)

5. `general_investment_basket` re-extraction. Requires re-extraction
   window. $12.95 cost. Doesn't compound (single deal).
6. Stage 1 finer classification (PRIMARY-CAPACITY,
   PRIMARY-CONDITIONAL, etc.). Future refinement; not blocking.
7. MFN/DI eval set adaptation. Per-question work.
8. Cross-deal extraction. Per-deal work.

## Items deferred to known-gaps

Consolidated entries from Phase G commits (existing entries in
`docs/v4_known_gaps.md`):

- Pre-Phase-F duplicate `provision_has_answer` instances
  (Phase F commit 3) — forward-only discipline.
- `event_governed_by_norm` rules unpopulated (Phase F commit 3) —
  populated by future event-class governance phase.
- Schema range constraints deferred (Phase F commit 5) — schema-
  additive whenever justified.
- Convention 1 percentage form reconciliation (Phase F commit 5) —
  Phase G no-op; Phase H or future Phase G' (extraction-prompt
  iteration phase).
- v3-vocab synthesis_guidance for 17 remaining categories —
  per-category iterative work.

Phase G doesn't add new known-gaps entries beyond what's already
documented. The Phase H scope candidates above are the principal
"deferred but actionable" items from Phase G's findings.

## Validation harness baseline (final, post-Phase-G)

A1=pass, A4 m=45 s=6 mm=0, A5=pass aggregate_accuracy=1.0, A6=pass.

Identical to pre-Phase-G baseline; preserved across all 6 commits.

## What Phase G changed in the architecture

- **Documented authority hierarchy gap.** The architecture's
  declared hierarchy (`guidance > LLM defaults`) is graded in
  practice. This is now documented as a known property of v4
  synthesis, with a bounded fix applied (Commit 3 payload sort)
  and a larger fix scoped for Phase H.
- **Empirical mechanism categorization.** Q5 RDP exclusion is
  mechanism (c) data positioning, not (a) LLM stylistic or (b)
  prompt-iteration distance. Q4 carveout non-enumeration is
  ambiguous (a or attribute-level c). The 3-phase controlled-
  variation diagnostic methodology is reproducible
  (`app/scripts/phase_g_synthesis_diagnostic.py`).
- **Adaptation review documented.** v3-to-v4 pattern adaptation
  categorized per element. Future synthesis architecture changes
  reference this review for which elements fit cleanly vs which
  need rework.
- **Entity inventory completeness standard documented.** Future
  extraction work has a target.
- **Bounded fix applied.** `fetch_norm_context` payload sort makes
  the declared authority hierarchy enforceable for capacity-related
  questions in the bounded case.

## Branch state at Phase G end

- Branch: `v4-deontic`
- HEAD: this commit (Phase G commit 6)
- Commits ahead of `origin/v4-deontic`: 6
- Push planned at end of Phase G (per locked scope: end-of-phase
  push only).
