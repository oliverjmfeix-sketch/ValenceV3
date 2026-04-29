"""
Phase E commit 2 — add Q4 upstream carveout attributes to asset_sale_sweep.

Idempotent schema migration. Defines four new attributes and grants
asset_sale_sweep ownership of each:

- permits_product_line_exemption_2_10_c_iv (boolean) — captures whether
  Section 2.10(c)(iv) provides a product-line / line-of-business sale
  exemption from the mandatory prepayment sweep.
- product_line_2_10_c_iv_threshold (double) — the leverage threshold
  for the 2.10(c)(iv) exemption (typically 6.25x for Duck Creek).
- permits_section_6_05_z_unlimited (boolean) — captures whether
  Section 6.05(z) provides an unlimited basket carveout subject to a
  leverage ratio test.
- section_6_05_z_threshold (double) — the leverage threshold for the
  6.05(z) carveout (typically 6.00x for Duck Creek).

Both carveouts are gated on a "ratio at or below threshold OR pro
forma no-worse" depth-2 condition; the no-worse test branch is
captured at the operations layer rather than as a separate attribute
on the entity (existing convention — see audit at
docs/v4_known_gaps.md).

TypeDB 3.x `define` is idempotent — re-running on a schema that
already contains the additions is a no-op.

Usage:
    TYPEDB_DATABASE=valence_v4 \\
        C:/Users/olive/ValenceV3/.venv/Scripts/python.exe \\
        -m app.scripts.phase_e_add_asset_sale_carveout_attrs
"""
from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

# override=False so a CLI-supplied TYPEDB_DATABASE wins over .env defaults.
_main_env = Path("C:/Users/olive/ValenceV3/.env")
if _main_env.exists():
    load_dotenv(_main_env, override=False)
load_dotenv(REPO_ROOT / ".env", override=False)

from typedb.driver import (  # noqa: E402
    TypeDB, Credentials, DriverOptions, TransactionType,
)

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s  %(levelname)-7s  %(message)s",
                    datefmt="%H:%M:%S")
logger = logging.getLogger("phase_e_carveout_attrs")


SCHEMA_DEFINE = """
define
  attribute permits_product_line_exemption_2_10_c_iv, value boolean;
  attribute product_line_2_10_c_iv_threshold, value double;
  attribute permits_section_6_05_z_unlimited, value boolean;
  attribute section_6_05_z_threshold, value double;
  entity asset_sale_sweep, owns permits_product_line_exemption_2_10_c_iv;
  entity asset_sale_sweep, owns product_line_2_10_c_iv_threshold;
  entity asset_sale_sweep, owns permits_section_6_05_z_unlimited;
  entity asset_sale_sweep, owns section_6_05_z_threshold;
"""


def main() -> int:
    db = os.environ.get("TYPEDB_DATABASE", "valence_v4")
    logger.info("Target DB: %s", db)

    driver = TypeDB.driver(
        os.environ["TYPEDB_ADDRESS"],
        Credentials(os.environ["TYPEDB_USERNAME"], os.environ["TYPEDB_PASSWORD"]),
        DriverOptions(),
    )
    try:
        tx = driver.transaction(db, TransactionType.SCHEMA)
        try:
            tx.query(SCHEMA_DEFINE).resolve()
            tx.commit()
            logger.info("Defined 4 carveout attributes + ownership on asset_sale_sweep.")
        except Exception:
            if tx.is_open():
                tx.close()
            raise

        # Verify
        rtx = driver.transaction(db, TransactionType.READ)
        try:
            for attr in (
                "permits_product_line_exemption_2_10_c_iv",
                "product_line_2_10_c_iv_threshold",
                "permits_section_6_05_z_unlimited",
                "section_6_05_z_threshold",
            ):
                # Use try{} to avoid errors when no asset_sale_sweep instances
                # have the attribute set yet.
                q = (
                    f'match $s isa asset_sale_sweep; '
                    f'try {{ $s has {attr} $v; }}; '
                    f'select $s;'
                )
                r = rtx.query(q).resolve()
                count = len(list(r.as_concept_rows()))
                logger.info("  %s queryable on %d asset_sale_sweep instance(s).",
                            attr, count)
        finally:
            try:
                if rtx.is_open():
                    rtx.close()
            except Exception:
                pass
        return 0
    finally:
        driver.close()


if __name__ == "__main__":
    sys.exit(main())
