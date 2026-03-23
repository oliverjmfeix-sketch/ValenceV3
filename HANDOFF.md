# HANDOFF.md — Valence V3

> Last updated: 2026-03-23 by Claude Code session
> Previous session: Prompts 7b-8 (capacity_effect, shares_capacity_pool, no_worse_is_uncapped)

## What Was Done This Session

### Prompt 8b: Two-Stage Synthesis (`1e3325a`)
- Split `/ask-graph` from single Claude call into two calls:
  - **Stage 1 (Filter)**: Classifies which entity types are relevant to the question
  - **Stage 2 (Synthesis)**: Answers using only filtered entities
- Reduced context noise from 43 entities to ~7-15 for most questions
- Added filter stage tracing to `trace_collector.py`

### Prompt 8c: Conservative Filter (`e96de4e`)
- Filter was too aggressive in 8b (Q4: 43→7, lost sweep tiers/de minimis)
- Updated filter prompt to err heavily on inclusion
- Q4 fixed (sweep tiers restored), but Q5 got all 43 entities (no filtering benefit)

### Prompt 8d: Two-Tier Entity Filter (`de04c1b`)
- Replaced flat include/exclude with PRIMARY / SUPPLEMENTARY / EXCLUDE tiers
- Primary entities: core analysis. Supplementary: detail, qualifications, corrections
- Tiered context: synthesis gets labeled sections, focuses on primary, checks supplementary
- Added **SELF-VERIFICATION** block to system prompt:
  - Re-derive capacity totals by capacity_category
  - Check capacity_effect + shares_capacity_pool for reallocation interpretation
  - Align hedge language to definitive boolean conclusions
  - Check supplementary entities for qualifying data

### Model Upgrade to Opus 4.6 (`5193eaf`)
- Changed both filter and synthesis from Opus 4.5 / Sonnet 4 to **Opus 4.6**
- Critical for Q6: Opus 4.5 incorrectly reasoned that removing negative EBITDA "worsens" leverage; Opus 4.6 gets this right
- Config: `app/config.py` `claude_model = "claude-opus-4-6"`, `synthesis_model = "claude-opus-4-6"`
- Filter model hardcoded in `deals.py` line ~2411 as `"claude-opus-4-6"`

### Handoff Skill Created
- `/handoff` skill at `.claude/skills/handoff/SKILL.md`
- Reads git history, updates CLAUDE.md + HANDOFF.md, commits and pushes

## Key Decisions Made

1. **Two-tier filter over flat filter**: Flat include/exclude was either too aggressive (lost entities) or too conservative (kept all 43). Two tiers let synthesis focus on primary while still having supplementary available for verification.

2. **Self-verification in system prompt**: After data-level signals alone failed to prevent shared-pool errors across 7 prompt iterations (7-8c), adding explicit verification instructions ("re-derive your total, check capacity_category, check capacity_effect on edges") finally worked. This is methodology in the prompt but narrowly scoped to verification, not interpretation.

3. **Opus 4.6 for both stages**: Sonnet was too weak for the filter (wrong tier assignments) and Opus 4.5 had a critical reasoning error on Q6 (negative EBITDA direction). Opus 4.6 costs more (~$0.55-0.71/question vs ~$0.35) but all 6 questions now pass.

4. **Annotation text carries interpretation**: Rather than adding system prompt rules about what `reduced_by_rp_usage` means, the question text on the annotation itself was updated to say "Per-basket usage accounting — does not indicate shared capacity." Claude reads the annotation and gets the correct interpretation from the data.

## Current State

### What's Working
- **All 6 gold standard questions pass** (first time ever)
- Two-stage synthesis deployed: filter (Opus 4.6) → synthesis (Opus 4.6)
- Schema: `shares_capacity_pool`, `capacity_effect`, `no_worse_is_uncapped` all in place
- Duck Creek fully extracted: 66 entities, 176 scalar answers, 5 reallocation edges
- init_schema.py was run with `--force` earlier this session (schema is fresh)

### What's In Progress
- Nothing — all prompt iterations complete, eval passes

### What's Broken / Known Issues
- **Old fetcher functions in graph_reader.py**: 10 unused functions, safe to delete
- **J.Crew Tier 3 prompt too long**: 212K > 200K token limit
- **Filter cost**: Opus 4.6 for filter is ~$0.10/call (vs ~$0.02 with Sonnet). Acceptable for quality but could optimize later.
- **Q5 answer still slightly hedged**: Says "$260M base, maximum $520M with reallocation" — correct total but frames reallocation as optional rather than straightforward capacity. Gold standard is met but could be cleaner.

## Next Steps

1. **Test with a second deal** — All 6 questions have only been validated against Duck Creek (87852625). Extract another deal and run the eval to confirm the pipeline generalizes.

2. **Frontend integration** — The `/ask-graph` response now includes `filtered_entity_count`, `total_entity_count`, and tier information in the trace. Frontend could show these.

3. **Cost optimization** — Consider whether Sonnet 4.6 (when available) could handle the filter stage without quality loss. Current Opus 4.6 filter costs ~5x more than Sonnet.

4. **Cleanup pass** — Delete unused fetcher functions in `graph_reader.py`, remove check scripts and eval files from worktree.

5. **J.Crew Tier 3** — Fix the 212K token prompt that exceeds 200K limit. Either trim context or split into multiple extraction calls.

## Gotchas for Next Session

- **Don't re-extract Duck Creek** — it was re-extracted this session and costs $0.50+. Data is current.
- **Don't re-run init_schema --force** — schema was freshly seeded this session. Only needed if .tql files change.
- **Worktree branch**: Work was done on `claude/wizardly-davinci` worktree, pushed to `main`. The worktree has untracked eval files and check scripts.
- **Railway deploy is current** — last push was `5193eaf` (Opus 4.6 upgrade). Server is live and healthy.
- **Both filter AND synthesis use Opus 4.6** — this is intentional. Don't downgrade the filter to Sonnet without re-running eval.
- **290 questions now** (was 289) — `rp_g5b` added in `seed_new_questions_008.tql` for `no_worse_is_uncapped`

## Eval State

### Final Eval: Prompt 8d + Opus 4.6 (eval_post_prompt8d_opus46.txt)

| Q# | Question | Filter | Result | Status |
|----|----------|--------|--------|--------|
| Q1 | Builder basket tests + start date | 43→1P+4S | ✅ Three tests + fiscal quarter of closing | Pass |
| Q2 | Dividend equity in unsubs? | 43→4P+20S | ✅ 6.06(p) categorical carve-out | Pass |
| Q3 | Reallocation baskets? | 43→15P+28S | ✅ All reallocations, notes "additive" capacity_effect | Pass |
| Q4 | Asset sale → dividends? | 43→15P+28S | ✅ Sweep tiers + de minimis + full detail | Pass |
| Q5 | Total dividend capacity? | 43→14P+29S | ✅ **$520M** — "4 baskets × $130M = $520,000,000" | Pass |
| Q6 | Negative EBITDA asset at 6.0x? | 43→15P+28S | ✅ **Yes** — removing negative EBITDA improves leverage, no-worse passes | Pass |

Cost per question: ~$0.27–$0.71 (filter + synthesis on Opus 4.6)

### Eval History This Session

| Prompt | Q5 | Q6 | Notes |
|--------|----|----|-------|
| 8b (Sonnet filter) | $150M ❌ | Yes ✅ | Filter too aggressive |
| 8c (conservative) | $280M–$410M ⚠️ | "may be" ⚠️ | Filter too loose |
| 8d (two-tier, Opus 4.5) | $520M mentioned, hedged ⚠️ | "Cannot" ❌ | Opus 4.5 reasoning error on Q6 |
| **8d (two-tier, Opus 4.6)** | **$520M ✅** | **Yes ✅** | **All 6 pass** |

Eval files in worktree:
- `eval_post_prompt7b.txt` — Post P7b baseline
- `eval_post_prompt8.txt` — Post P8
- `eval_post_prompt8b.txt` — Post P8b
- `eval_post_prompt8c.txt` — Post P8c
- `eval_post_prompt8d.txt` — Post P8d (Opus 4.5)
- `eval_post_prompt8d_opus46.txt` — **Final eval (Opus 4.6, all pass)**

## Environment Notes

- **Railway**: Auto-deploys from `main` branch. Current deploy: commit `5193eaf`
- **TypeDB**: Database `valence` on `ip654h-0.cluster.typedb.com:80`. Schema freshly seeded this session.
- **Config**: `claude_model = "claude-opus-4-6"`, `synthesis_model = "claude-opus-4-6"` in `app/config.py`
- **Duck Creek**: deal_id `87852625`, provision_id `87852625_rp`. Last extracted 2026-03-23.
- **290 ontology questions** across 27 categories (was 289 before `rp_g5b`)
