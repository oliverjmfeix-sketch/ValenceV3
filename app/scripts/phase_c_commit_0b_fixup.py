"""
Phase C Commit 0b — one-time fixup of existing valence_v4 v3 data.

Applies the same scale-coercion normalization that Commit 0a wired into
extraction.py (post-extraction), but to data already in valence_v4 from the
$12.95 Duck Creek extraction (which predates the normalization step).

After this script runs:
- Every fractional grower-pct on v3 baskets is rewritten as a percentage
- Every modified basket is marked with cleaned_by_phase_c_commit_0=true
- Pre/post snapshots saved to docs/v4_phase_c_commit_0b/ for audit

Idempotent: re-running is a no-op (values already >= 5.0 are skipped).

Usage:
    py -m app.scripts.phase_c_commit_0b_fixup --deal 6e76ed06
    py -m app.scripts.phase_c_commit_0b_fixup --deal 6e76ed06 --dry-run
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(REPO_ROOT))

_main_env = Path("C:/Users/olive/ValenceV3/.env")
if _main_env.exists():
    load_dotenv(_main_env, override=False)
load_dotenv(REPO_ROOT / ".env", override=False)

from app.config import settings  # noqa: E402
from app.services.v3_data_normalization import (  # noqa: E402
    _SCALE_COERCION_ATTRS,
    _FRACTION_THRESHOLD,
    _normalize_v3_data,
)
from typedb.driver import TypeDB, Credentials, DriverOptions, TransactionType  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("commit_0b_fixup")

SNAPSHOT_DIR = REPO_ROOT / "docs" / "v4_phase_c_commit_0b"


def connect():
    return TypeDB.driver(
        settings.normalized_typedb_address,
        Credentials(settings.typedb_username, settings.typedb_password),
        DriverOptions(),
    )


def snapshot_grower_attrs(driver, db: str, deal_id: str) -> list[dict]:
    """Read every (basket_id, attr, value) tuple for the deal's
    scale-coercion-eligible attributes. Used for pre/post audit."""
    rows: list[dict] = []
    tx = driver.transaction(db, TransactionType.READ)
    try:
        for attr in _SCALE_COERCION_ATTRS:
            # Polymorphic match via provision_has_extracted_entity parent
            # catches both rp_baskets and rdp_baskets.
            q = (
                f'match\n'
                f'    $d isa deal, has deal_id "{deal_id}";\n'
                f'    (deal: $d, provision: $p) isa deal_has_provision;\n'
                f'    (provision: $p, extracted: $b) isa provision_has_extracted_entity;\n'
                f'    $b has basket_id $bid;\n'
                f'    $b has {attr} $v;\n'
                f'select $bid, $v;\n'
            )
            try:
                result = tx.query(q).resolve()
                for r in result.as_concept_rows():
                    rows.append({
                        "basket_id": r.get("bid").as_attribute().get_value(),
                        "attribute": attr,
                        "value": r.get("v").as_attribute().get_value(),
                    })
            except Exception as exc:
                # Attribute type not in schema; skip.
                logger.debug(f"snapshot: skip {attr} ({str(exc).splitlines()[0][:80]})")
    finally:
        try:
            if tx.is_open():
                tx.close()
        except Exception:
            pass
    return rows


def _lookup_basket_type(driver, db: str, basket_id: str) -> str | None:
    """Return the concrete type label for a basket given its basket_id."""
    rtx = driver.transaction(db, TransactionType.READ)
    try:
        try:
            r = rtx.query(
                f'match $b has basket_id "{basket_id}"; select $b;'
            ).resolve()
            for row in r.as_concept_rows():
                return row.get("b").get_type().get_label()
        except Exception as exc:
            logger.warning(f"type lookup failed for {basket_id}: {str(exc)[:100]}")
    finally:
        try:
            if rtx.is_open():
                rtx.close()
        except Exception:
            pass
    return None


def mark_baskets(driver, db: str, basket_ids: set[str]) -> int:
    """Mark each modified basket with cleaned_by_phase_c_commit_0=true.
    Returns count successfully marked.

    TypeDB 3.x type inference requires the match to constrain $b to a
    type that owns the marker (INF4 fires otherwise on `match $b has
    basket_id "..."; insert $b has cleaned_by_phase_c_commit_0 true;`
    because $b could resolve to any basket subtype, including ones that
    don't own the marker). Pre-query the concrete type per basket, then
    issue a typed insert.
    """
    if not basket_ids:
        return 0
    marked = 0
    for bid in basket_ids:
        type_label = _lookup_basket_type(driver, db, bid)
        if type_label is None:
            logger.warning(f"mark: type unknown for {bid}; skipping")
            continue
        wtx = driver.transaction(db, TransactionType.WRITE)
        try:
            q = (
                f'match\n'
                f'    $b isa {type_label}, has basket_id "{bid}";\n'
                f'insert $b has cleaned_by_phase_c_commit_0 true;\n'
            )
            try:
                wtx.query(q).resolve()
                wtx.commit()
                marked += 1
                logger.debug(f"marked {bid} (isa {type_label})")
            except Exception as exc:
                if wtx.is_open():
                    wtx.close()
                logger.warning(
                    f"mark basket {bid} (isa {type_label}) failed: {str(exc).splitlines()[0][:120]}"
                )
        except Exception:
            if wtx.is_open():
                wtx.close()
            raise
    return marked


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--deal", required=True, help="Deal ID (e.g. 6e76ed06)")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Snapshot pre-fixup state only; don't modify data",
    )
    args = parser.parse_args()

    if settings.typedb_database != "valence_v4":
        logger.error(
            "Refusing to run: settings.typedb_database is %r (expected 'valence_v4'). "
            "Set TYPEDB_DATABASE=valence_v4 in env.",
            settings.typedb_database,
        )
        return 2

    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    driver = connect()
    try:
        # Pre-snapshot
        logger.info("Snapshotting pre-fixup state for deal %s", args.deal)
        pre = snapshot_grower_attrs(driver, "valence_v4", args.deal)
        pre_path = SNAPSHOT_DIR / f"snapshot_pre_{args.deal}_{timestamp}.json"
        pre_path.write_text(json.dumps(pre, indent=2), encoding="utf-8")
        fractional = [r for r in pre if r["value"] <= _FRACTION_THRESHOLD]
        logger.info(
            "Pre-fixup: %d grower-pct values total, %d fractional (need fixup)",
            len(pre), len(fractional),
        )
        for r in fractional:
            logger.info(
                "  %s.%s = %s -> %s",
                r["basket_id"], r["attribute"], r["value"], r["value"] * 100.0,
            )

        if args.dry_run:
            logger.info("--dry-run: skipping rewrite + marker")
            return 0

        if not fractional:
            logger.info("No fractional values found; nothing to fix.")
            return 0

        # Apply normalization (reuses extraction.py function — single
        # source of truth for the heuristic).
        # _normalize_v3_data reads typedb_client.database; ensure it sees
        # valence_v4. The settings env is already set, but the
        # typedb_client global may have been initialized against a
        # different DB. Re-initialize defensively.
        from app.services.typedb_client import typedb_client
        if typedb_client.driver is None or typedb_client.database != "valence_v4":
            typedb_client.database = "valence_v4"
            typedb_client.driver = driver

        logger.info("Applying scale-coercion normalization")
        rewrites, modified = _normalize_v3_data(args.deal)
        logger.info(
            "Rewrote %d values across %d baskets: %s",
            rewrites, len(modified), sorted(modified),
        )

        # Mark touched baskets
        marked = mark_baskets(driver, "valence_v4", modified)
        logger.info("Marked %d baskets with cleaned_by_phase_c_commit_0=true", marked)

        # Post-snapshot
        logger.info("Snapshotting post-fixup state")
        post = snapshot_grower_attrs(driver, "valence_v4", args.deal)
        post_path = SNAPSHOT_DIR / f"snapshot_post_{args.deal}_{timestamp}.json"
        post_path.write_text(json.dumps(post, indent=2), encoding="utf-8")
        post_fractional = [r for r in post if r["value"] <= _FRACTION_THRESHOLD]
        logger.info(
            "Post-fixup: %d total, %d still fractional (should be 0)",
            len(post), len(post_fractional),
        )

        if post_fractional:
            logger.error("FIXUP INCOMPLETE — some fractional values remain:")
            for r in post_fractional:
                logger.error(f"  {r}")
            return 1

        logger.info("Fixup complete. Snapshots: %s, %s", pre_path.name, post_path.name)
        return 0
    finally:
        driver.close()


if __name__ == "__main__":
    sys.exit(main())
