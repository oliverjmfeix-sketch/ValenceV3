# v3 → v4 vocabulary map for synthesis_guidance

> Phase D Commit 1 audit. Surveys every v3 concept the synthesis_guidance
> strings reference and confirms whether it's reachable from `valence_v4`.
> Output: a per-concept verdict table + the migration call (copy all 18
> guidance entries from `valence` to `valence_v4` AS-IS, since v3 entity
> types are preserved in `valence_v4` via the Phase B/C extraction layer).

## Audit basis

- v3 has **39 ontology_categories** total; **27 have `synthesis_guidance`**.
- v4 has **18 ontology_categories** (RP-relevant subset). All 18 have v3
  guidance available — **perfect overlap**.
- v3-only categories without v4 equivalents: DI1–12, JC1–3, MFN1–6
  (covenant types not yet in v4 schema).
- v3 entity types (`builder_basket`, `ratio_basket`, `jcrew_blocker`,
  etc.) **are preserved in `valence_v4`** as the $12.95 extraction
  artifact. v4 norms link to them via `norm_extracted_from:fact`.
- This means most v3-vocabulary references in synthesis_guidance work
  AS-IS in `valence_v4` — the entities they describe are still there.

## Concept-by-concept verdict

### Portable AS-IS (no translation needed)

| v3 concept | Used in categories | Why portable |
|---|---|---|
| `builder_basket` entity attributes | F | `builder_basket` exists in `valence_v4` (1 instance for Duck Creek). Reachable via `(provision: $p, extracted: $b) isa provision_has_extracted_entity`. |
| `ratio_basket` entity attributes (`ratio_threshold`, `has_no_worse_test`, etc.) | G | Same — `ratio_basket` entity preserved. |
| `jcrew_blocker` entity boolean attributes | JC1, JC3, K | Preserved — `jcrew_blocker` (1 instance) + `blocker_exception` children. |
| `management_equity_basket`, `tax_distribution_basket`, `equity_award_basket`, `holdco_overhead_basket` attributes | C, D, E, H | All preserved (1 each). |
| `unsub_designation` entity | J | Preserved (1 instance). |
| `investment_pathway` entity | JC3, K | Preserved (6 instances). |
| `sweep_tier`, `sweep_exemption`, `de_minimis_threshold` | L | `sweep_tier` preserved (3 instances). |
| `general_rp_basket`, `general_rdp_basket` attributes | I, N, T | Preserved. |
| `capacity_category` attribute on baskets | F, G, N | Still owned by basket entities in `valence_v4`. **Note:** v4 also has `capacity_composition`, but `capacity_category` is the v3-vocabulary attribute synthesis_guidance references and it's preserved. |
| `provision_has_basket`, `provision_has_rdp_basket` relations | T | Preserved as relation types in `valence_v4` schema. Every basket reachable from its provision. |
| `basket_reallocates_to` link with `capacity_effect` attribute | I, N | `basket_reallocates_to` relation type exists in v4 schema; instance count is 0 for Duck Creek (no extracted reallocation data — same condition as v3). Guidance reads "if X exists, do Y" which fires zero matches; safe. |
| Pattern flags on `rp_provision` (`jcrew_pattern_detected`, `serta_pattern_detected`, `collateral_leakage_pattern_detected`) | Z | `rp_provision` preserved (1 instance) with all pattern flag attributes. Reachable directly. |

### Categorical no-ops (referenced but no instances; safe)

These v3 concepts are referenced by guidance but have zero instances
in `valence_v4` (same as Duck Creek's v3 state). The guidance reads
defensively ("if X exists, do Y") so zero matches just produces empty
sections of the synthesized answer. Not load-bearing.

| v3 concept | Used in | v4 instance count | Action |
|---|---|---:|---|
| `intercompany_permission` entity | B | 0 (type not in v4 schema) | None. Category B falls back to `scalar answers`. |
| `basket_reallocation` entity | I, JC3 | 0 (type not in v4 schema) | None. Category I covers the `basket_reallocates_to` link path which is the load-bearing surface; the dedicated `basket_reallocation` entity was a v3 redundancy. |
| `definition_clause` entity | JC2 | 0 | JC2 is not a v4 category (RP-only), so guidance unused anyway. |
| `mfn_freebie_basket`, `mfn_yield_definition`, `mfn_sunset_provision`, `mfn_exclusion` entities | MFN1–6 | 0 | MFN categories not in v4 (RP-only). |
| `lien_release_mechanics` entity | JC1 | 0 | JC1 is not a v4 category. |
| `shares_capacity_pool` relation | N | 0 (type in v4 schema; no instances) | None. Guidance reads "if shares_capacity_pool link exists between baskets, …" — zero matches yields the conjunction-of-defaults branch. Documented behavioral gap; safe. |

### Things v4 has that v3 guidance doesn't reference yet

These are net-new v4 deontic primitives not mentioned in the existing
synthesis_guidance corpus. Synthesis can still reason over them by
seeing the structured norm context, but the prompt could be extended
to nudge synthesis toward them. **Phase F scope** if D3 diagnostic
shows synthesis missing them.

- **`norm` entity scalar attributes** (`norm_id`, `norm_kind`,
  `modality`, `capacity_composition`, `cap_uses_greater_of`,
  `growth_start_anchor`, `reference_period_kind`, `floor_value`)
- **`condition` tree** (root + indexed children) — explicit deontic
  conditions vs the v3 `partial_applicability` / `has_no_worse_test`
  booleans
- **`defeater` + `defeats` edges** — explicit override semantics; v3
  guidance for K talks about "blocker exceptions" but those map 1:1
  to v4 defeaters. Synthesis can use the v4 structure if surfaced in
  the fetched context.
- **`norm_contributes_to_capacity` edges** with `child_index` +
  `aggregation_function` — explicit graph for builder-source
  composition. v3's category F talks about "child entities (sources)";
  v4's edge structure is the formal version.
- **`produced_by_rule` provenance** — for synthesis, this tells
  which projection_rule emitted a norm. Not directly useful in
  legal-answer prose, but useful for an "audit-trail" mode.

## Migration call

Copy the 18 v3 synthesis_guidance entries (categories A, B, C, D, E, F,
G, H, I, J, K, L, M, N, P, S, T, Z) from `valence` to `valence_v4`
**verbatim**. Translation surface is small enough that no rewriting
needed at migration time; iterate prompts in Phase F if D3 diagnostic
surfaces gaps.

Migration script: `app/scripts/migrate_v3_synthesis_guidance.py` (added
in this commit). Idempotent — uses match-on-category-id; replaces
existing if present.

## What this means for D2 prompt design

**Stage 1** (PRIMARY/SUPPLEMENTARY/SKIP classifier): receives the
norm context + the question. No synthesis_guidance needed; just
classification. Stage 1 prompt is a thin wrapper around the entity
list.

**Stage 2** (synthesis): receives the filtered context + the question
+ the synthesis_guidance for matched topic-router categories. The
guidance content tells Claude what attributes/entities to emphasize
and how to format. With v3 guidance loaded into v4, Stage 2 has the
same human-authored content driving it that v3 had.

**v4-aware vocabulary changes for Stage 2**: minimal. The system
prompt frames the legal context as "norms with cap_usd, modality,
scope edges" alongside "extracted basket entities the norm came
from." Most of the heavy lifting is the per-category guidance,
which is portable.

## Verification (run during D1's smoke test)

The fetch_norm_context output should include, for each norm:
- The norm's own scalars
- The v3 entity it `norm_extracted_from` (with its full attribute payload — this is where category F, G, N etc. find `capacity_category`, `basket_amount_usd`, etc.)
- Scope edges (subject/action/object/instrument)
- Any conditions, defeaters, contributes_to edges
- Provenance (produced_by_rule)

If the smoke test prints this for Duck Creek and the printed entries
contain the v3 attributes the synthesis_guidance references (e.g.,
`capacity_category` on `general_rp_basket`), the audit is verified
end-to-end.
