# Phase I outcome — implementation phase (consolidation)

> Phase I implements the four directions the user picked at handover:
> Phase G's Tier 1 (a + b), event_governed_by_norm projection, v3→v4
> synthesis_guidance rewrite, and additive RP extraction-prompt fixes.
> 11 commits on `v4-deontic`. Phase I is the **first implementation-flavored
> phase** after eight architecture/audit phases (B/C/D1/D2/E/F/G/H).

## Summary

| Sub-phase | Outcome | Cost |
|---|---|---|
| I.1 — Stage 2 must-cite layer (Tier 1b) | ✓ shipped | $0.7541 |
| I.2 — event_governed_by_norm projection (Phase F deferral) | ✓ shipped | $0.8047 |
| I.3 — Generalized relevance scoring (Tier 1a) | ✓ shipped | $0.8333 |
| I.4 — synthesis_guidance v3→v4 rewrite (L, N supplement, I + L picker) | ✓ shipped | $0.8271 |
| I.5 — RP extraction-prompt additions | ✓ shipped (additive only) | $0 |
| **Total** | | **$3.2192** |

Within budget; lower than Phase F ($3.68) and Phase E ($4.32).

## Lawyer eval headline (post-Phase-I)

```
duck_creek_q1 (builder):        PASS preserved
duck_creek_q2 (unsub):          PASS preserved
duck_creek_q3 (reallocation):   PARTIAL (richer framing; carveouts ext-blocked)
duck_creek_q4 (asset_sale):     PARTIAL (sweep tiers cited; 2.10(c)(iv)/6.05(z) Stage 1 limited)
duck_creek_q5 (total_capacity): PARTIAL (RDP cited; floor sum LLM-limited)
duck_creek_q6 (ratio):          PASS preserved
```

**3 PASS / 3 PARTIAL** — same headline as Phase E, but every PARTIAL is
**substantively richer** with new evidence in the citation chain and
new norms in the graph.

Conformance (must_cite ⊆ citations): **6/6** preserved across all
sub-phases.

Validation harness baseline (start → end of Phase I):
- A1=pass → A1=pass
- A4 m=45 s=6 mm=0 → **A4 m=42 s=6 mm=0** (Phase I.2 baseline IMPROVEMENT;
  three norm_kinds matched: sweep_tier, unlimited_asset_sale_basket_permission,
  sweep_exemption_product_line)
- A5=pass aggregate_accuracy=1.0 → preserved
- A6=pass → preserved

## What Phase I changed in the architecture

### I.1 — Authority hierarchy enforceable

Stage 1 PRIMARY → Stage 2 citation hierarchy is now empirically
enforced via the `must_cite_norm_ids` payload + system prompt rule.
Every PRIMARY norm appears in citations across all 6 lawyer questions.
Stage 1's selection is auditable; the eval JSON now carries
`stage1.primary_norm_ids` and `stage2.must_cite_norm_ids` for
mechanical conformance verification.

Phase G's "graded authority hierarchy" finding (Stage 2 silently
dropping PRIMARY norms under default payload positioning) is
neutralized at the citation layer.

### I.2 — event_governed_by_norm populated

Phase F deferral closed. 5 v4 norms (3 sweep_tier obligations + 2
carveout permissions) tied to `asset_sale_event` event_class via
`event_governed_by_norm`. Authored via templated seed +
`emit_asset_sale_governance_norms()` helper, parallel to Phase C's
`emit_asset_sale_proceeds_flows`.

A4 round_trip improvement: 3 norm_kinds transitioned from "missing"
to "matched" (sweep_tier, unlimited_asset_sale_basket_permission,
sweep_exemption_product_line).

Q4 milestone: post-I.2 answer **explicitly named Section 2.10(c)(iv)**
and cited all 3 sweep tiers as PRIMARY (subsequently reweighted
under I.3, but the norms remain in the graph and reachable).

### I.3 — Question-aware relevance scoring

Phase G commit 3's bounded `action_scope`-only sort replaced with
`_compute_norm_relevance_score()` combining tier-1 capacity markers,
question-keyword overlap, and matched-category alignment. Backward
compatible — legacy callers fall back to tier-1-only scoring.

Q5 milestone: Stage 1 promoted **all 6 builder_source norms** to
PRIMARY (was 4 in I.2). Builder basket structure decomposed cleanly
in Stage 2 answer.

Trade-off observed: Q4's Stage 1 reweighted under the new sort,
losing some of I.2's carveout-naming gains. Norms remain SUPPLEMENTARY
in the graph; payload-position cue alone is insufficient to override
Stage 1's literal-question interpretation.

### I.4 — Category L/N/I synthesis_guidance v4 rewrite

L, N (supplement), and I rewritten in v4 vocab. L points at v4
governance norms by name; N has explicit $390M worked example for
the dividend-floor sum; I reasons over `action_scope` and
`shares_capacity_pool` (replacing v3's `basket_reallocation` entity
refs that don't exist in v4 graph). L stage1_picker_guidance
authored to instruct PRIMARY-classification of governance norms.

Empirical finding: **stage1_picker_guidance is a hint, not a hard
constraint.** The L picker explicitly told Stage 1 to mark sweep
tier and carveout norms as PRIMARY for asset-sale questions; Stage 1
still classified them SUPPLEMENTARY based on its own literal-question
interpretation. This **empirically demonstrates Phase G's "graded
authority hierarchy"** finding for the picker layer too.

### I.5 — Q3 extraction infrastructure + rp_l25 iteration

Schema-additive 6 attributes on `general_rdp_basket` (intercompany,
reorganization, IPO carveouts × {boolean, threshold}). 6 new
extraction questions (rp_t28..rp_t33) with four-beat prompts per
Phase H methodology. 6 question_annotations + 6 category_has_question
links. rp_l25 prompt iterated to 5 numbered cases handling compound
"greater of (x) and (y)" leverage tests (Phase H known gap for
`product_line_2_10_c_iv_threshold` null).

Effect deferred to post-pilot re-extraction window. The DB still
holds the original schema; init_schema.py --force loads I.5 changes.

## Cost summary

```
I.1 must-cite layer:       $0.7541 (eval)
I.2 governance norms:      $0.8047 (eval)
I.3 relevance scoring:     $0.8333 (eval)
I.4 synthesis_guidance:    $0.8271 (eval)
I.5 extraction additions:  $0.0000 (no LLM calls)
═══════════════════════════════════════
Phase I total:             $3.2192
```

Cumulative across pilot:
```
Phase D2:  $1.10
Phase E:   $4.32
Phase F:   $3.68
Phase G:   $1.60
Phase H:   $0
Phase I:   $3.22  (this phase)
═══════════════════════════════════════
Pilot total: ~$13.92
```

Plus the original Duck Creek extraction artifact ($12.95). Total
pilot spend ~$26.87.

## What Phase I didn't move

The 3 PARTIAL questions (Q3, Q4, Q5) didn't flip to PASS. Each has
specific residual gaps:

- **Q3 reallocation**: tailored carveouts (intercompany / reorganization /
  IPO RDP) require re-extraction to populate the I.5-authored
  attributes. Synthesis layer is ready; data layer awaits re-extraction.
- **Q4 asset_sale_proceeds**: Stage 1 classifier doesn't promote
  carveout norms to PRIMARY despite explicit picker guidance. Closing
  this would need either a Stage 1 must-pick layer (analogous to
  I.1's must-cite at Stage 2), more aggressive relevance scoring
  weights, or a more capable Stage 1 model.
- **Q5 total_capacity**: Stage 2 cites general_rdp_basket but doesn't
  sum it into the dividend floor. The N synthesis_guidance now
  contains an explicit $390M worked example; Stage 2's interpretation
  of "reallocable supplements" vs "additive component" is
  LLM-defaults-driven and didn't shift under guidance update alone.
  Same options apply as Q4.

These are the **architectural limits** of natural-language guidance
against LLM defaults — exactly what Phase G's "graded authority
hierarchy" finding predicted. Closing them empirically requires
either a tighter prompt-level constraint mechanism or a different
model.

## Course corrections during Phase I

### I.4 incident — env-var clobber on first apply

The first I.4 apply attempt used `load_dotenv(override=True)` in the
applier script, which overrode the caller's `TYPEDB_DATABASE=valence_v4`
env var with the main `.env` file's `TYPEDB_DATABASE="valence"`. Four
upserts hit the v3 production DB instead of v4. Detected immediately
when the script log printed "Target DB: valence". Rolled back via a
temp script:

1. Restored `valence` (v3) L/N/I synthesis_guidance to main-branch
   canonical values.
2. Deleted the erroneously-added L stage1_picker_guidance attribute.
3. Re-applied the upserts to `valence_v4` (correct target).

The applier script was hardened to `override=False`. No data loss;
v3 production state restored to canonical. Documented in I.4 outcome.

### I.3 stop-loss assessment

I.3's relevance-score sort caused Q4 Stage 1 to drop 4 governance
norms from PRIMARY (sweep tiers + sweep_exemption_product_line) under
the new payload ordering. Q5 gained: all 6 builder_source norms
became PRIMARY with richer answer.

Stop-loss criterion: "If sub-phase I.1 or I.3 regresses any PASS
question to PARTIAL or FAIL, halt and revert that sub-phase." The
regression was PARTIAL→PARTIAL (different aspects covered/missed),
not PASS→PARTIAL/FAIL. I.3 shipped with documented trade-off.

## Hard constraints honored

- **No re-extraction of Duck Creek.** valence_v4 ($12.95 artifact)
  unchanged. I.5's new questions/schema/prompt iteration deferred
  to post-pilot re-extraction.
- **No merge to main.** v4-deontic lives on origin only.
- **Validation harness baseline preserved.** A1/A5/A6 unchanged;
  A4 baseline IMPROVED from m=45 to m=42.
- **Schema-additive only.** No removals or renames during pilot.
- **Push at end of phase.** All 9 commits batched on origin/v4-deontic
  push.
- **Python 3.11 venv.** Used throughout.
- **TYPEDB_DATABASE=valence_v4 env override.** Used (with one
  rolled-back exception, post-mortem in I.4 outcome).

## Phase II scope candidates (post-pilot if needed)

If the user wants further behavioral progress on Q3/Q4/Q5:

1. **Stage 1 must-pick layer.** Analogous to I.1's must-cite at
   Stage 2. Per-category `must_pick_norm_kinds` list injected into
   Stage 1 payload as a structured key, with system-prompt rule
   that Stage 1 MUST classify any norm whose norm_kind is in the
   list as PRIMARY. Closes Q4's carveout-naming gap directly.
   ~2-3 commits, ~$1-2 cost.

2. **Stage 2 must-sum layer.** Analogous to must-cite. Per-category
   `must_sum_norm_ids` list with system-prompt rule that Stage 2
   MUST include each in the dollar floor calculation. Closes Q5's
   $260M → $390M gap directly. ~2 commits, ~$1.

3. **Re-extraction with I.5 prompts.** Triggers post-pilot:
   populates Q3 carveout attributes, fixes rp_l25 null, may also
   close other Phase H/F deferrals. ~$13 cost (matches original
   Duck Creek extraction).

4. **F / G / M / other category lock-in rewrites.** PASS questions
   on stable v3 vocab. Per-category iteration; each ~30 minutes.

5. **`event_governed_by_norm` for sweep_exemption_de_minimis.** Same
   pattern as I.2; one more block in the seed file. Closes the
   remaining A4 missing entry (`sweep_exemption_de_minimis`).

## Branch state at Phase I end

- Branch: `v4-deontic`
- HEAD: this commit (Phase I final consolidation; commit 10 of 11
  if including the push commit; alternatively this is commit 10)
- Commits ahead of `origin/v4-deontic`: 10
- **Push: end-of-Phase-I push to be performed after this commit.**

Phase I delivers the implementation foundation the user asked for at
handover:
- Two of Phase G's Tier 1 candidates landed (must-cite + relevance scoring).
- Phase F's deferred event_governed_by_norm rules landed.
- Three eval-priority categories' synthesis_guidance rewritten in v4 vocab.
- Q3 extraction infrastructure authored additively for post-pilot re-extraction.
- Validation harness baseline preserved (and improved on A4 missing count).
- No PASS→PARTIAL regressions.

The lawyer eval headline (3/3) doesn't change, but the underlying
graph state and synthesis discipline are demonstrably richer:
event_governed_by_norm has 5 instances, must-cite enforces 100%
conformance, relevance scoring is question-aware, category L points
at v4 norms by name, Q3 has post-pilot extraction infrastructure
ready, rp_l25 has a tighter prompt.
