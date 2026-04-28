"""
Phase C — v3 data normalization.

Heuristics that compensate for v3 extraction inconsistency. Lives in its
own module (decoupled from extraction.py's Anthropic SDK dependency) so
the one-time fixup script in app/scripts/phase_c_commit_0b_fixup.py can
reuse the function without pulling in anthropic.

Currently handles scale coercion (fraction -> percentage) for grower-pct
family attributes. Phase D's extraction prompt updates aim to make these
heuristics unnecessary; once extraction emits canonical values directly,
this module becomes dead code.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


# Attributes that v3 extraction sometimes returns as fractions (0.15) and
# sometimes as percentages (15.0). Real covenant grower-pct values span
# 1-200% (0.01-2.00 in fraction form); legitimate percentages are >= 5.0,
# so a value <= 5.0 reliably identifies a fraction needing 100x up-scale.
_SCALE_COERCION_ATTRS = (
    "basket_grower_pct",       # general_rp_basket, general_investment_basket, general_rdp_basket
    "annual_cap_pct_ebitda",   # management_equity_basket
    "starter_ebitda_pct",      # builder_basket
    "cni_percentage",          # builder_basket
    "ebitda_fc_multiplier",    # builder_basket
    "equity_proceeds_pct",     # builder_basket
)

# Threshold below which a value is interpreted as a fraction needing scaling.
_FRACTION_THRESHOLD = 5.0


def _normalize_v3_data(deal_id: str) -> tuple[int, set[str]]:
    """Walk v3 entities for the deal and apply data-quality normalization.

    Currently handles scale coercion (fraction -> percentage) for
    grower-pct family attributes.

    Returns (rewrites_count, modified_basket_ids).

    Idempotent: re-running on already-normalized data is a no-op (values
    >= _FRACTION_THRESHOLD are skipped).

    Phase C Commit 0a: this function lives here so future extractions
    auto-normalize via extraction.py calling it after store_extraction.
    Phase C Commit 0b calls the same function via a one-time fixup
    script against existing valence_v4 data.
    """
    from typedb.driver import TransactionType
    from app.services.typedb_client import typedb_client

    rewrites = 0
    modified: set[str] = set()
    db = typedb_client.database
    driver = typedb_client.driver
    if driver is None:
        logger.warning("typedb driver unavailable; skipping post-extraction normalization")
        return 0, modified

    for attr_name in _SCALE_COERCION_ATTRS:
        # Read fractional values for this attr scoped to the deal.
        # Path: deal -> deal_has_provision -> provision -> provision_has_extracted_entity -> entity
        # Uses the abstract `provision_has_extracted_entity` parent so we
        # catch both rp_baskets (via provision_has_basket) and rdp_baskets
        # (via provision_has_rdp_basket); attribute filter ensures only
        # entities owning the target attr match.
        rtx = driver.transaction(db, TransactionType.READ)
        rows: list[tuple[str, float]] = []
        try:
            q = (
                f'match\n'
                f'    $d isa deal, has deal_id "{deal_id}";\n'
                f'    (deal: $d, provision: $p) isa deal_has_provision;\n'
                f'    (provision: $p, extracted: $b) isa provision_has_extracted_entity;\n'
                f'    $b has basket_id $bid;\n'
                f'    $b has {attr_name} $v;\n'
                f'    $v < {_FRACTION_THRESHOLD};\n'
                f'select $bid, $v;\n'
            )
            try:
                result = rtx.query(q).resolve()
                rows = [
                    (
                        r.get("bid").as_attribute().get_value(),
                        r.get("v").as_attribute().get_value(),
                    )
                    for r in result.as_concept_rows()
                ]
            except Exception as exc:
                # Common: attribute type not in schema (covenant not yet
                # extracted, etc.). Quiet skip.
                logger.debug(f"normalize: skip {attr_name} ({str(exc).splitlines()[0][:80]})")
                rows = []
        finally:
            try:
                if rtx.is_open():
                    rtx.close()
            except Exception:
                pass

        if not rows:
            continue

        # Rewrite via single match-delete-insert per fix. Keeping match,
        # delete, and insert in one query is required: the match's
        # `has $attr_name` clause narrows $b's type so the insert's
        # ownership constraint type-checks (TypeDB 3.x INF4 fires
        # otherwise — splitting across two queries loses the type
        # constraint on $b).
        wtx = driver.transaction(db, TransactionType.WRITE)
        try:
            for bid, old_v in rows:
                new_v = old_v * 100.0
                fix_q = (
                    f'match\n'
                    f'    $b has basket_id "{bid}", has {attr_name} $old;\n'
                    f'    $old == {old_v};\n'
                    f'delete has $old of $b;\n'
                    f'insert $b has {attr_name} {new_v};\n'
                )
                try:
                    wtx.query(fix_q).resolve()
                    rewrites += 1
                    modified.add(bid)
                    logger.debug(f"normalized {attr_name} on {bid}: {old_v} -> {new_v}")
                except Exception as exc:
                    logger.warning(
                        f"normalize {bid}.{attr_name} {old_v}->{new_v} failed: {str(exc).splitlines()[0][:120]}"
                    )
            wtx.commit()
        except Exception:
            if wtx.is_open():
                wtx.close()
            raise

    return rewrites, modified
