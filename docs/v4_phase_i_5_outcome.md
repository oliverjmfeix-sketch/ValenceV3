# Phase I.5 outcome — RP extraction-prompt additions

> Phase I.5 authors **additive Q3 carveout extraction infrastructure**
> and **iterates rp_l25's prompt** for compound-test handling. Seed-file
> only; effect deferred to post-pilot re-extraction window.

## Summary

| Workstream | Outcome |
|---|---|
| Q3 tailored RDP carveouts | ✓ schema + 6 questions + annotations + category links |
| rp_l25 prompt iteration | ✓ 5 explicit numbered cases for compound-form leverage tests |
| Validation harness | ✓ baseline preserved (no DB writes) |
| Lawyer eval | not run (extraction discipline doesn't change synthesis behavior) |

Total cost: $0 (seed-file only). Cumulative Phase I: $3.2192.

## Implementation

### Q3 tailored RDP carveouts

`app/data/schema_unified.tql`:
- 6 new attribute declarations near existing Phase E asset_sale_sweep
  carveout attrs:
  - `permits_intercompany_rdp_carveout` (boolean)
  - `intercompany_rdp_carveout_threshold` (double)
  - `permits_reorganization_rdp_carveout` (boolean)
  - `reorganization_rdp_carveout_threshold` (double)
  - `permits_ipo_rdp_carveout` (boolean)
  - `ipo_rdp_carveout_threshold` (double)
- 6 `owns` clauses added to `general_rdp_basket` entity.

`app/data/seed_new_questions.tql`:
- 6 new questions `rp_t28`..`rp_t33` (3 booleans + 3 numbers).
- Booleans ask "Does Section 6.09(a) include a separately-stated
  X carveout?" with description distinguishing from general RDP
  basket and equity-funded RDP basket where ambiguous.
- Numbers extract the leverage threshold using the compound-test
  pattern from rp_l25 / rp_l27 (return (x) hard component).
- 6 category_has_question links to category T.

`app/data/question_annotations.tql`:
- 6 new question_annotates_attribute relations linking each question
  to its target attribute on `general_rdp_basket`.

### rp_l25 prompt iteration

Phase H's known gap: rp_l25 returned null on Duck Creek despite the
gold answer specifying 6.25x. The original prompt said "no greater
than X to Y on a Pro Forma Basis -> answer X" — but Duck Creek's
text is compound: "less than or equal to the greater of (x) 6.25 to
1.00 and (y) the First Lien Leverage Ratio (determined without giving
effect to the Subject Transactions on a Pro Forma Basis)". The
original prompt didn't explicitly disambiguate (x) vs (y).

Iterated prompt (1164 chars, was ~440):
- 5 explicit numbered cases:
  1. Simple form -> answer X
  2. Compound form with x=6.25 -> answer 6.25
  3. Compound form with x=6.00 -> answer 6.00
  4. No-worse-only test -> answer null
  5. Carveout absent -> answer null
- Closing reinforcement: return the (x) hard component, not the (y)
  no-worse branch.

Effect deferred to post-pilot re-extraction window. The DB still
holds the original prompt; no re-extraction triggered in pilot scope.

## Validation harness baseline (post-I.5)

A1=pass, A2/A3 fail (pre-existing), A4 m=42 s=6 mm=0 (preserved
from I.2/I.3/I.4), A5=pass aggregate_accuracy=1.0, A6=pass.

No DB writes. Seed-file changes are forward-looking SSoT.

## What I.5 changed in the architecture

- **Q3 has extraction infrastructure for the gold-answer carveouts.**
  Intercompany / reorganization / IPO RDP carveouts are now first-class
  attributes on `general_rdp_basket` with named extraction questions
  and annotations. Re-extraction will populate them automatically.
- **rp_l25 has a tighter prompt.** The compound-test handling is
  unambiguous; future re-extraction should yield 6.25 (matches gold).
- **Phase H methodology applied.** Each new prompt follows the
  four-beat structure (context → instruction → example → null
  policy). All 6 boolean prompts include explicit "distinguish from
  X" guidance to avoid double-counting with existing baskets.

## What I.5 deferred

- **Population of the new attributes on Duck Creek.** Requires
  re-extraction (locked scope). Q3 PARTIAL headline persists until
  post-pilot re-extraction window opens.
- **Validation of the iterated rp_l25 prompt.** Requires re-extraction
  to verify that Duck Creek's compound test yields 6.25 (matches
  gold). Today's data has product_line_2_10_c_iv_threshold=null.
- **Schema attribute load.** init_schema.py with --force would load
  the 6 new attributes + 6 new questions + 6 new annotations into
  TypeDB. Deferred per "no DB schema-affecting changes during pilot"
  carryforward.

## Branch state at I.5 end

- Branch: `v4-deontic`
- HEAD: this commit (Phase I commit 9)
- Commits ahead of `origin/v4-deontic`: 9
- Push deferred to end of Phase I.

Phase I.5 complete. Moving to Phase I final consolidation.
