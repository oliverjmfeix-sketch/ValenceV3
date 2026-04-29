# Phase E commit 3 — Q4 carveout extraction run

## Execution

```bash
TYPEDB_DATABASE=valence_v4 \
  C:/Users/olive/ValenceV3/.venv/Scripts/python.exe \
  -m app.services.extraction \
  --deal 6e76ed06 \
  --covenant-type RP \
  --question-ids rp_l24,rp_l25,rp_l26,rp_l27
```

## Results

| Metric | Value |
|---|---|
| Answers stored | 3 / 4 |
| Entities created | 0 (existing asset_sale_sweep extended) |
| Cost | $1.7273 |
| Latency | 15.3s |
| Errors | 0 |

## Per-attribute outcome on Duck Creek

| Attribute | Question | Gold | Extracted | Status |
|---|---|---|---|---|
| `permits_product_line_exemption_2_10_c_iv` | rp_l24 | true (2.10(c)(iv) exists) | `True` | ✓ |
| `product_line_2_10_c_iv_threshold` | rp_l25 | 6.25 | `null` | ✗ — LLM did not extract the threshold value |
| `permits_section_6_05_z_unlimited` | rp_l26 | true (6.05(z) exists) | `True` | ✓ |
| `section_6_05_z_threshold` | rp_l27 | 6.00 | `6.0` | ✓ |

3 of 4 attributes landed correctly. The single miss is the 2.10(c)(iv)
threshold — the question prompt asks for the FLLR threshold value
("E.g., if 'no greater than 6.25 to 1.00 on a Pro Forma Basis', answer
6.25"), but Duck Creek's Section 2.10(c)(iv) language may be phrased
in a way the LLM read as "no specific threshold stated" (only
no-worse). Phase F prompt iteration could likely close this gap.

## Cost reality-check (continued from commit 1)

Cost per scalar question: $1.73 / 4 = $0.43/question — much higher
than the Phase 1 estimate of $0.05. The extraction service uses the
full 446K-char universe per call; 4 questions cost as much as 35
questions would have at the Phase D extraction cost ratio.

This suggests the dynamic-batching scalar extractor doesn't actually
batch when only a few questions are filtered — each question runs as
a near-solo prompt against the full universe. For Phase E's tractable
budget this is fine ($1.73 + $1.84 commit 1 = $3.57 total Phase E
extraction), but Phase F should consider tightening the scalar
prompt's universe slice when running incremental questions.

## Synthesis impact

Stage 2 will see the 3 populated attributes via the existing
`provision_level_entities.by_type.asset_sale_sweep` block (Phase D2
commit 4 fetch path; the new attributes are picked up automatically
by `_all_attrs_of` since it walks all attributes on the entity).

The Phase D2 commit 5 synthesis_guidance for category L (Asset Sale
Proceeds & Sweeps) tells Stage 2 to enumerate sweep_tier entities and
de_minimis attrs from `asset_sale_sweep`. The new carveout flags
aren't yet referenced in the guidance — Stage 2 will see them in the
fetched payload but may not surface them in answers without an
additional guidance update.

For the Phase E commit 5 lawyer eval re-run: Q4 should now reference
6.05(z) and 2.10(c)(iv) IF Stage 2 surfaces these new attrs from the
payload. If it doesn't, a small Phase F-ish synthesis_guidance
extension could nudge it.
