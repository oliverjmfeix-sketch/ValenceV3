# v4 Diagnostic — `cap_usd` A4 Mismatches Classified

Date: 2026-04-24
Status: diagnostic, findings-only; no edits made this prompt beyond the
companion Fix 1 already committed.

---

## Prompt-09 report inaccuracy

Prompt 09's report attributed 16 A4 mismatches across action_scope (8),
cap_usd (4), and source_page (4). On re-reading `round_trip_check` in
`app/services/validation_harness.py` lines 350-377, the mismatch check
only ever compares `action_scope` — there is no `cap_usd` or
`source_page` comparison branch. The 16 reported mismatches were all
action_scope false-positives from the Prompt 10 Fix 1 GT-fetch bug (GT
graph read was missing the action_scope attribute, so every comparison
landed at gt=None vs extracted=X).

After Prompt 10 Fix 1 resolved the GT-fetch bug + aligned projection's
sub-source action_scope with GT, A4 mismatched drops to **0**.

## Direct cap_usd comparison (diagnostic performed)

Independent of A4, compared `cap_usd` (and `cap_grower_pct`) values
between `valence_v4` (extracted) and `valence_v4_ground_truth` (GT),
matched by (norm_kind, modality) — the tightest join that works without
pulling full structural tuples.

### cap_usd: no mismatches

Every norm with a cap_usd value on both sides agrees:

| norm_kind | extracted | GT |
|---|---|---|
| builder_source_starter | 130_000_000.0 | 130_000_000.0 |
| general_rp_basket_permission | 130_000_000.0 | 130_000_000.0 |
| management_equity_basket_permission | 20_000_000.0 | 20_000_000.0 |
| general_rdp_basket_permission | 130_000_000.0 | 130_000_000.0 |

Zero `cap_usd` mismatches on matched norms.

### cap_grower_pct: scale convention mismatch (3 cases)

| norm_kind | extracted | GT |
|---|---|---|
| general_rp_basket_permission | **1.0** | **100.0** |
| management_equity_basket_permission | **0.15** | **15.0** |
| general_rdp_basket_permission | **1.0** | **100.0** |

**Consistent pattern: extracted stores fractions (0.15, 1.0) where GT
stores percentages (15, 100). 100× scale difference.**

No single case is "defensible as a different reading of operative text" —
the underlying agreement values are identical (100% of EBITDA, 15% of
EBITDA). It's a convention mismatch between v3 extraction output and GT
authoring.

## Per-row classification

Using the prompt's (a)/(b)/(c) taxonomy from the Fix 4 spec:

| Row | Classification |
|---|---|
| general_rp_basket_permission (1.0 vs 100.0) | **(a) projection / extraction convention bug** |
| management_equity_basket_permission (0.15 vs 15.0) | **(a) projection / extraction convention bug** |
| general_rdp_basket_permission (1.0 vs 100.0) | **(a) projection / extraction convention bug** |

All three are Category (a): projection emits fractions where GT authors
percentages. The root cause is v3 extraction's convention for
`cap_grower_pct` — Duck Creek baskets authored as "greater of $X and
Y% of EBITDA" get `basket_grower_pct` stored as fractions (1.0 for
100%). Projection copies this value verbatim to `cap_grower_pct` on
the norm, preserving the fraction.

## Existing precedent

Prompt 08 Fix 5 (`c54bc8f`) already applied a scale coercion inside
`_project_builder_sub_sources`:

```python
if cap_grower is not None and cap_grower <= 5.0:
    cap_grower = cap_grower * 100.0
```

That coercion normalizes builder sub-source cap_grower_pct values to
the percentage convention. It does NOT fire for top-level basket
projection (builder_basket, ratio_basket, general_rp_basket, etc.)
because the main `project_entity` path doesn't apply it.

Evidence: in the output table above, `builder_source_starter` shows
**cap_grower=100.0** (coerced correctly) while
`general_rp_basket_permission` shows **cap_grower=1.0** (not coerced)
for the same underlying agreement concept (100% of EBITDA).

## Recommendation

**Cheap fix, fits this prompt's scope if the user wants it now:** move
the scale-coercion snippet out of `_project_builder_sub_sources` into a
shared helper and apply it at the main `project_entity` emission site
too. Same heuristic: `value ≤ 5.0 → multiply by 100`. Safe: real
grower-pct values in covenant agreements are 1–200% (0.01–2.00 in
fraction form); legitimate percentage values are ≥ 5.0 so the heuristic
doesn't double-scale anything real. Three norms flip to the GT scale
(100.0 / 15.0 / 100.0). No schema changes, no GT edits.

**Deferrable to Prompt 11+:** the ideal fix is v3 extraction storing
`cap_grower_pct` as percentage (0-100) not fraction (0-1), aligned with
GT's convention. Requires re-extraction to re-populate the value, which
is out of scope here. Mark as a known gap in v4_known_gaps.md.

For Prompt 10, since the diagnostic explicitly scoped "fix if (a)
projection bug, simple and mechanical" — this IS case (a) and IS
simple. Proposing a 5-line follow-up within Prompt 10.

## Caveat — extraction output vs projection emission

The extracted rp_basket entities store `basket_grower_pct` as fraction
(v3 convention). Projection currently copies to `cap_grower_pct` on the
norm verbatim. The fix lives in projection (attribute transformation),
not in extraction (value re-store). No re-extraction needed.

## cap_usd verdict

**No action needed on cap_usd.** The name on the Prompt 09 report was a
mis-attribution; there are no cap_usd mismatches. The real issue is
cap_grower_pct scale, documented above.
