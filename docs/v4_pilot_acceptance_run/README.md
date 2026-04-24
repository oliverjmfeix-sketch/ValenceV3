# V4 Pilot Acceptance Run

Date: 2026-04-24
Branch: `v4-deontic`
Deal: Duck Creek Technologies (`6e76ed06`)
Gold standard: `lawyer_dc_rp` (6 lawyer-authored RP questions)
Parser model: `claude-sonnet-4-6`

This is the pilot's final acceptance artifact. Human judgment on the
verbatim side-by-side comparison of gold answers vs Valence answers.

**No scoring, no pass/fail rubric.** The consumer reads the verbatim TXT
and judges whether the Valence answers demonstrate acceptable quality
for the pilot's stated scope (structural analysis of RP; evaluation
against supplied state; explicit out-of-scope handling).

## Files

- [v4_eval_6e76ed06_20260424_152042_verbatim.txt](v4_eval_6e76ed06_20260424_152042_verbatim.txt)
  **Primary deliverable.** Side-by-side gold vs Valence per question.
  Read this first.
- [v4_eval_6e76ed06_20260424_152042_summary.txt](v4_eval_6e76ed06_20260424_152042_summary.txt)
  Per-question status + timing + parser operation chosen.
- [v4_eval_6e76ed06_20260424_152042_full.json](v4_eval_6e76ed06_20260424_152042_full.json)
  Complete trace data for debugging / later analysis. Includes full
  parser responses, computation traces, and world-state inputs used.

## Run characteristics

- Total wall time: ~64 s for 6 questions
- 6 Claude SDK calls (one per question), all via prompt cache
- Total cost: ~$0.10-0.20 (Sonnet pricing, cached system prompt)
- All 6 questions completed without execution errors
- Default pilot world state (EBITDA ≈ $127M, leverage 4.50, pre-IPO,
  healthy baseline) auto-supplied when evaluated operations were
  routed. Per `docs/v4_gold_question_posture_audit.md`.

## Per-question summary

| Q  | Parser operation          | Notes |
|----|---------------------------|-------|
| Q1 | describe_norm             | Cumulative Amount structure + 13 contributing sources listed |
| Q2 | clarification_needed      | Parser surfaces 3 interpretations for the "Is permitted" question |
| Q3 | trace_pathways            | 35 permissions for make_dividend_payment (collapse=false inherited from parser) |
| Q4 | trace_pathways            | Same as Q3 — structural pathway list |
| Q5 | clarification_needed      | Structural vs evaluated capacity ambiguity |
| Q6 | evaluate_feasibility      | INCONCLUSIVE — parser-extracted partial world state insufficient for full evaluation |

Two of six questions (Q2, Q5) trigger `clarification_needed` rather
than auto-routing. Under the Rule 8.1 posture this is correct
behavior: the parser surfaces ambiguity rather than guessing. The
renderer displays the two or three plausible interpretations so the
consumer can pick one. Whether this is the right UX for a pilot
acceptance run is a judgment call for the reviewer — alternative
behaviors (auto-routing to the first interpretation, or pre-seeding
a default world state for any "permitted/capacity" question) are
discussed in the post-pilot follow-up items.

## Post-pilot follow-up (out of scope for this run)

- Compare against v3 scalar pipeline on the same 6 questions
- Extraction coverage gaps flagged in `docs/v4_known_gaps.md`
- TypeDB 3.x capability requests (rollback ergonomics; parameterized
  function calls without persistent arg entities)
- Rule 5.2 concession revisit — the Python evaluator for
  evaluate_feasibility / evaluate_capacity
- Prompt tuning for Q2/Q5-style questions (auto-route to structural
  interpretation when no world state is supplied)
- Trace_pathways on action_class=make_dividend_payment returning 35
  permissions even with collapse=true reveals that collapse was
  reset by intent parser; confirm parser passes collapse_contributors
  through properly
- `source_page`/`source_text` verification for the 19 Option-A
  corrected norms — already in known-gaps
