# Phase E Commit 1 — `rp_el_reallocations` zero-result diagnosis

## Question

Why does Duck Creek (`6e76ed06`) have zero `basket_reallocates_to` v3
entities in `valence_v4`, despite an extraction question
(`rp_el_reallocations`) being authored before the deal's extraction
ran?

## Pre-flight git timeline

```
2026-03-13  a1d1038  data: Phase 2d-i — entity_list answer type + retire extraction_metadata
            (creates seed_entity_list_questions.tql with rp_el_reallocations)
2026-04-01  ...      Duck Creek extraction run (per CLAUDE.md / Phase D handover)
```

The reallocation question existed for ~3 weeks before Duck Creek
extracted. Theoretically should have run during the original
extraction.

## Diagnosis (Phase E commit 1, 2026-04-29)

Re-ran `rp_el_reallocations` against Duck Creek's universe via the
new Phase E commit 0 incremental extraction CLI:

```bash
TYPEDB_DATABASE=valence_v4 \
  C:/Users/olive/ValenceV3/.venv/Scripts/python.exe \
  -m app.services.extraction \
  --deal 6e76ed06 \
  --covenant-type RP \
  --question-ids rp_el_reallocations
```

Result:

- **Cost:** $1.8382 (significantly higher than the Phase 1 estimate of
  ~$0.05/question — entity-list questions are more expensive than
  scalars due to longer outputs).
- **Latency:** 29.5s.
- **LLM behavior:** Successfully identified 4 reallocation paths in
  Duck Creek's RP covenant — the LLM did NOT return an empty list.
- **Storage outcome:** 0 entities stored. Two distinct failures.

### Failure 1: 4 reallocations reference `general_investment_basket`,
which doesn't exist as a v3 entity on Duck Creek

The LLM's identified paths (verbatim from logs):

```
general_rdp_basket(6e76ed06_rp_general_rdp_basket) -> general_investment_basket(None)
general_rp_basket(6e76ed06_rp_general_rp_basket) -> general_investment_basket(None)
general_investment_basket(None) -> general_rp_basket(6e76ed06_rp_general_rp_basket)
general_investment_basket(None) -> general_rdp_basket(6e76ed06_rp_general_rdp_basket)
```

Storage layer correctly logs:

```
No general_investment_basket found on provision 6e76ed06_rp
```

This matches the Phase C handover's known gap: Duck Creek's
`general_investment_basket` v3 entity wasn't extracted (deferred).
Until that gap is closed (re-extraction with corrected investment
extraction OR manual injection), these reallocations cannot persist.

### Failure 2: `capacity_effect` cardinality violation on the surviving
RP↔RDP reallocation pair

After the 4 invalid edges are filtered, the remaining edges between
`general_rp_basket` and `general_rdp_basket` (both directions) trigger:

```
[CNT5] Constraint '@card(0..1)' has been violated: found 2 instances.
[DVL10] Instance [...] of type 'basket_reallocates_to' has an attribute
ownership constraint violation for attribute ownership of type 'capacity_effect'.
```

Two distinct relations (`source=RP, target=RDP` and `source=RDP, target=RP`)
each get one `capacity_effect` value, but TypeDB sees them as a single
relation with two values — likely because the storage layer's
match-then-attribute-add path queries `basket_reallocates_to` between
the two role players without distinguishing direction, then adds the
attribute twice on the matched relation.

Schema (`schema_unified.tql:1884-1898`):

```typeql
relation basket_reallocates_to,
    relates source_basket,
    relates target_basket,
    owns reallocation_source,
    owns reallocation_amount_usd,
    owns reallocation_grower_pct,
    owns is_bidirectional,
    owns reduces_source_basket,
    owns reduction_is_dollar_for_dollar,
    owns reduction_while_outstanding_only,
    owns section_reference,
    owns source_page,
    owns source_text,
    owns confidence,
    owns capacity_effect;
```

`capacity_effect` is a single-valued attribute (default cardinality
0..1). The storage layer needs either to:
- Match a specific relation by both role players AND ensure direction
  uniqueness before adding the attribute, OR
- Insert each direction as a fresh relation rather than match-then-add.

This is a **storage-layer bug** independent of extraction. The
extraction question is correctly authored.

## Conclusion

`rp_el_reallocations` is **not** a missing extraction question. The
prompt works at the LLM level — Duck Creek's PDF has identifiable
reallocation language that the LLM extracts correctly.

The Q3 PARTIAL is caused by **two storage-layer issues** plus the
v3-entity-extraction gap on `general_investment_basket`:

1. `general_investment_basket` not extracted on Duck Creek (Phase C
   deferred, needs re-extraction or manual fix).
2. `basket_reallocates_to` storage path violates `capacity_effect`
   cardinality when both directions of a reallocation pair are
   inserted (extraction-side or storage-side fix).

Both issues are out of Phase E scope (Phase E is "extraction
additions", not storage fixes or full re-extraction). Phase F should
address #2 as part of the broader extraction prompt + storage cleanup.
#1 stays gated on a future re-extraction window.

## Phase E impact

Q3 stays PARTIAL post-Phase-E. The Phase D2 outcome ("RP↔RDP fungible
reallocation captured; tailored carveouts not enumerated") is
unchanged — synthesis layer reads the extracted v3 attributes and
their `action_scope: 'reallocable'` markers; the
`basket_reallocates_to` v3 relation isn't strictly required for the
RP↔RDP fungible interpretation.

The Phase D2 README's framing — "no extraction question targets
reallocation language" — should be corrected to "extraction question
exists; storage path has a cardinality bug; v3 investment basket
deferred." This document is the correction.

## Cost reality-check

The Phase D2 plan estimated incremental extraction at ~$0.30 for 5-10
questions ($0.05/question). The actual cost for ONE entity-list
question was $1.84. Entity-list questions cost ~30× a scalar question.

If Phase E commit 3 runs the asset_sale_sweep extraction question
(also entity-list) on top of this commit's $1.84, total Phase E
extraction cost is ~$3.50, not the planned $0.30.

This doesn't change Phase E's tractability ($3.50 is still
significantly cheaper than full re-extraction at $14.84+), but the
plan's cost estimates for entity-list questions need to be revised
upward by ~5-30× depending on output size.
