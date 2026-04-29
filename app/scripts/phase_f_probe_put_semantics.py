"""
Phase F commit 1 — probe TypeDB 3.8 `put` semantics.

Purpose: empirically determine what `put` does for each storage case
the Valence pipeline writes. Findings feed `docs/v4_storage_patterns.md`
and shape the upsert helpers Commit 1's storage-layer rewrite uses.

Probe sequence:
  1. Entity put with @key — second put with same key: match or duplicate?
  2. Attribute put on existing entity (single-valued attr): update or
     duplicate?
  3. Attribute put on existing entity (multi-valued attr): how does it
     behave?
  4. Relation put with same role players: match or duplicate?
  5. Relation put with same role players + different edge attrs: how
     does it behave?
  6. match-and-delete-then-insert pattern for attribute value update —
     baseline reference.

All probe entities use a `phase_f_probe_*` prefix and are deleted at
the end of each probe so the baseline graph is untouched.

Idempotency: re-runnable. Cleanup at start removes any stale probe
state from prior partial runs.

Usage:
    TYPEDB_DATABASE=valence_v4 \\
        C:/Users/olive/ValenceV3/.venv/Scripts/python.exe \\
        -m app.scripts.phase_f_probe_put_semantics
"""
from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

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
logger = logging.getLogger("phase_f_probe")

PROBE_PREFIX = "phase_f_probe_"


def _connect():
    return TypeDB.driver(
        os.environ["TYPEDB_ADDRESS"],
        Credentials(os.environ["TYPEDB_USERNAME"], os.environ["TYPEDB_PASSWORD"]),
        DriverOptions(),
    )


def cleanup_probes(driver, db: str) -> None:
    """Delete any prior probe state. Idempotent."""
    wtx = driver.transaction(db, TransactionType.WRITE)
    try:
        # Use rp_provision as our probe entity type since it exists in the
        # schema with provision_id @key.
        # Delete any provision whose id starts with the probe prefix.
        # Also delete any provision_has_answer relations they participate in.
        try:
            wtx.query(
                f'match $prov isa rp_provision, has provision_id $pid; '
                f'$pid contains "{PROBE_PREFIX}"; '
                f'delete $prov;'
            ).resolve()
        except Exception as e:
            logger.debug("cleanup_probes (provisions): %s", str(e).splitlines()[0][:120])
        wtx.commit()
    finally:
        try:
            if wtx.is_open():
                wtx.close()
        except Exception:
            pass


def count_entities(driver, db: str, entity_type: str, key_attr: str,
                    key_pattern: str) -> int:
    rtx = driver.transaction(db, TransactionType.READ)
    try:
        r = rtx.query(
            f'match $x isa {entity_type}, has {key_attr} $k; '
            f'$k contains "{key_pattern}"; '
            f'select $x;'
        ).resolve()
        return len(list(r.as_concept_rows()))
    finally:
        try:
            if rtx.is_open():
                rtx.close()
        except Exception:
            pass


def count_attribute_values(driver, db: str, entity_type: str,
                             key_attr: str, key_value: str,
                             attr_name: str) -> int:
    """Count the number of distinct values for `attr_name` on the entity
    identified by (key_attr, key_value)."""
    rtx = driver.transaction(db, TransactionType.READ)
    try:
        r = rtx.query(
            f'match $x isa {entity_type}, has {key_attr} "{key_value}", '
            f'has {attr_name} $v; '
            f'select $v;'
        ).resolve()
        return len(list(r.as_concept_rows()))
    finally:
        try:
            if rtx.is_open():
                rtx.close()
        except Exception:
            pass


def probe_1_entity_put_with_key(driver, db: str) -> dict:
    """First put creates entity; second put with same key matches existing
    rather than duplicating (because @key prevents duplicates)."""
    pid = f"{PROBE_PREFIX}entity_put_1"
    findings = {"probe": "entity put with @key", "pid": pid}

    # First put — should insert
    wtx = driver.transaction(db, TransactionType.WRITE)
    try:
        wtx.query(f'put $p isa rp_provision, has provision_id "{pid}";').resolve()
        wtx.commit()
        findings["first_put"] = "inserted"
    except Exception as e:
        findings["first_put_error"] = repr(e)[:300]
    finally:
        try:
            if wtx.is_open():
                wtx.close()
        except Exception:
            pass

    after_first = count_entities(driver, db, "rp_provision", "provision_id", pid)
    findings["count_after_first_put"] = after_first

    # Second put with same key — should match existing
    wtx = driver.transaction(db, TransactionType.WRITE)
    try:
        wtx.query(f'put $p isa rp_provision, has provision_id "{pid}";').resolve()
        wtx.commit()
        findings["second_put"] = "succeeded"
    except Exception as e:
        findings["second_put_error"] = repr(e)[:300]
    finally:
        try:
            if wtx.is_open():
                wtx.close()
        except Exception:
            pass

    after_second = count_entities(driver, db, "rp_provision", "provision_id", pid)
    findings["count_after_second_put"] = after_second
    findings["interpretation"] = (
        "put is idempotent for entities with @key — second put matches existing"
        if after_first == after_second == 1
        else f"put produced {after_second} entities (expected 1)"
    )
    return findings


def probe_2_attribute_put_single_valued(driver, db: str) -> dict:
    """Probe: entity already exists; put a single-valued attribute on it
    twice. Does the second put update or add a duplicate value?"""
    pid = f"{PROBE_PREFIX}attr_put_2"
    findings = {"probe": "attribute put on existing entity (single-valued)",
                "pid": pid}

    # Set up entity with no attribute initially
    wtx = driver.transaction(db, TransactionType.WRITE)
    try:
        wtx.query(f'insert $p isa rp_provision, has provision_id "{pid}";').resolve()
        wtx.commit()
    finally:
        try:
            if wtx.is_open():
                wtx.close()
        except Exception:
            pass

    # First put: add jcrew_pattern_detected attribute (boolean)
    wtx = driver.transaction(db, TransactionType.WRITE)
    try:
        wtx.query(
            f'match $p isa rp_provision, has provision_id "{pid}"; '
            f'put $p has jcrew_pattern_detected true;'
        ).resolve()
        wtx.commit()
        findings["first_put"] = "succeeded (value=true)"
    except Exception as e:
        findings["first_put_error"] = repr(e)[:300]
    finally:
        try:
            if wtx.is_open():
                wtx.close()
        except Exception:
            pass

    findings["count_after_first_put"] = count_attribute_values(
        driver, db, "rp_provision", "provision_id", pid, "jcrew_pattern_detected")

    # Second put with DIFFERENT value
    wtx = driver.transaction(db, TransactionType.WRITE)
    try:
        wtx.query(
            f'match $p isa rp_provision, has provision_id "{pid}"; '
            f'put $p has jcrew_pattern_detected false;'
        ).resolve()
        wtx.commit()
        findings["second_put"] = "succeeded (value=false)"
    except Exception as e:
        findings["second_put_error"] = repr(e)[:300]
    finally:
        try:
            if wtx.is_open():
                wtx.close()
        except Exception:
            pass

    after_second = count_attribute_values(
        driver, db, "rp_provision", "provision_id", pid, "jcrew_pattern_detected")
    findings["count_after_second_put_different_value"] = after_second

    # Read actual final value to confirm update vs no-op
    rtx = driver.transaction(db, TransactionType.READ)
    try:
        r = rtx.query(
            f'match $p isa rp_provision, has provision_id "{pid}", '
            f'has jcrew_pattern_detected $v; '
            f'select $v;'
        ).resolve()
        values = []
        for row in r.as_concept_rows():
            try:
                values.append(row.get("v").as_attribute().get_value())
            except Exception:
                pass
        findings["final_values"] = values
    finally:
        try:
            if rtx.is_open():
                rtx.close()
        except Exception:
            pass

    findings["second_put_interpretation"] = (
        "put updated the value" if after_second == 1 and values == [False]
        else f"put kept original (count={after_second}, values={values})"
        if after_second == 1
        else f"put added a duplicate (now {after_second} values: {values})"
    )
    return findings


def probe_3_relation_put_same_players(driver, db: str) -> dict:
    """Probe: insert a relation (provision, question) twice via put.
    Does the second put match or duplicate?"""
    pid = f"{PROBE_PREFIX}rel_put_3"
    findings = {"probe": "relation put with same role players", "pid": pid}

    # Set up a provision; reuse an existing question (rp_a1 should exist)
    wtx = driver.transaction(db, TransactionType.WRITE)
    try:
        wtx.query(f'insert $p isa rp_provision, has provision_id "{pid}";').resolve()
        wtx.commit()
    finally:
        try:
            if wtx.is_open():
                wtx.close()
        except Exception:
            pass

    # First put: relation
    wtx = driver.transaction(db, TransactionType.WRITE)
    try:
        wtx.query(
            f'match $p isa rp_provision, has provision_id "{pid}"; '
            f'$q isa ontology_question, has question_id "rp_a1"; '
            f'put (provision: $p, question: $q) isa provision_has_answer, '
            f'has answer_id "{PROBE_PREFIX}ans_3", has answer_string "first";'
        ).resolve()
        wtx.commit()
        findings["first_put"] = "succeeded"
    except Exception as e:
        findings["first_put_error"] = repr(e)[:300]
    finally:
        try:
            if wtx.is_open():
                wtx.close()
        except Exception:
            pass

    rtx = driver.transaction(db, TransactionType.READ)
    try:
        r = rtx.query(
            f'match (provision: $p, question: $q) isa provision_has_answer; '
            f'$p has provision_id "{pid}"; '
            f'$q has question_id "rp_a1"; '
            f'select $p;'
        ).resolve()
        findings["count_after_first_put"] = len(list(r.as_concept_rows()))
    finally:
        try:
            if rtx.is_open():
                rtx.close()
        except Exception:
            pass

    # Second put: same role players, different answer_id
    wtx = driver.transaction(db, TransactionType.WRITE)
    try:
        wtx.query(
            f'match $p isa rp_provision, has provision_id "{pid}"; '
            f'$q isa ontology_question, has question_id "rp_a1"; '
            f'put (provision: $p, question: $q) isa provision_has_answer, '
            f'has answer_id "{PROBE_PREFIX}ans_3b", has answer_string "second";'
        ).resolve()
        wtx.commit()
        findings["second_put"] = "succeeded (different answer_id)"
    except Exception as e:
        findings["second_put_error"] = repr(e)[:300]
    finally:
        try:
            if wtx.is_open():
                wtx.close()
        except Exception:
            pass

    rtx = driver.transaction(db, TransactionType.READ)
    try:
        r = rtx.query(
            f'match (provision: $p, question: $q) isa provision_has_answer; '
            f'$p has provision_id "{pid}"; '
            f'$q has question_id "rp_a1"; '
            f'select $p;'
        ).resolve()
        after_second = len(list(r.as_concept_rows()))
        findings["count_after_second_put"] = after_second
    finally:
        try:
            if rtx.is_open():
                rtx.close()
        except Exception:
            pass

    findings["interpretation"] = (
        "put matched existing relation (1 instance, possibly with merged attrs)"
        if after_second == 1
        else f"put created additional relation ({after_second} instances)"
    )
    return findings


def probe_4_match_delete_insert_attribute(driver, db: str) -> dict:
    """Probe: classic match-and-delete-then-insert pattern for updating an
    attribute value. Baseline reference."""
    pid = f"{PROBE_PREFIX}match_delete_4"
    findings = {"probe": "match-delete-then-insert attribute update", "pid": pid}

    # Set up entity with initial attr
    wtx = driver.transaction(db, TransactionType.WRITE)
    try:
        wtx.query(
            f'insert $p isa rp_provision, has provision_id "{pid}", '
            f'has jcrew_pattern_detected true;'
        ).resolve()
        wtx.commit()
    finally:
        try:
            if wtx.is_open():
                wtx.close()
        except Exception:
            pass

    # Match-delete-insert in one tx
    wtx = driver.transaction(db, TransactionType.WRITE)
    try:
        # Delete existing
        wtx.query(
            f'match $p isa rp_provision, has provision_id "{pid}", '
            f'has jcrew_pattern_detected $existing; '
            f'delete has $existing of $p;'
        ).resolve()
        # Insert new
        wtx.query(
            f'match $p isa rp_provision, has provision_id "{pid}"; '
            f'insert $p has jcrew_pattern_detected false;'
        ).resolve()
        wtx.commit()
        findings["match_delete_insert"] = "succeeded (true -> false)"
    except Exception as e:
        findings["match_delete_insert_error"] = repr(e)[:300]
    finally:
        try:
            if wtx.is_open():
                wtx.close()
        except Exception:
            pass

    # Read final value
    rtx = driver.transaction(db, TransactionType.READ)
    try:
        r = rtx.query(
            f'match $p isa rp_provision, has provision_id "{pid}", '
            f'has jcrew_pattern_detected $v; '
            f'select $v;'
        ).resolve()
        values = []
        for row in r.as_concept_rows():
            try:
                values.append(row.get("v").as_attribute().get_value())
            except Exception:
                pass
        findings["final_values"] = values
        findings["final_count"] = len(values)
    finally:
        try:
            if rtx.is_open():
                rtx.close()
        except Exception:
            pass

    findings["interpretation"] = (
        "match-delete-insert correctly replaces value"
        if findings.get("final_values") == [False]
        else f"unexpected final state: {findings.get('final_values')}"
    )
    return findings


def main() -> int:
    # Force UTF-8 stdout to avoid cp1252 encoding errors on Windows
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8',
                                    errors='replace')
    db = os.environ.get("TYPEDB_DATABASE", "valence_v4")
    logger.info("Target DB: %s", db)
    logger.info("Probe prefix: %s (cleaned up at start and end)", PROBE_PREFIX)

    driver = _connect()
    try:
        # Cleanup any stale probe state
        logger.info("Cleanup pre-probe...")
        cleanup_probes(driver, db)

        all_findings = []
        logger.info("Probe 1: entity put with @key")
        all_findings.append(probe_1_entity_put_with_key(driver, db))
        logger.info("Probe 2: attribute put on existing entity (single-valued)")
        all_findings.append(probe_2_attribute_put_single_valued(driver, db))
        logger.info("Probe 3: relation put with same role players")
        all_findings.append(probe_3_relation_put_same_players(driver, db))
        logger.info("Probe 4: match-delete-insert attribute update")
        all_findings.append(probe_4_match_delete_insert_attribute(driver, db))

        # Cleanup
        logger.info("Cleanup post-probe...")
        cleanup_probes(driver, db)

        print("\n" + "=" * 70)
        print("PHASE F COMMIT 1 — PROBE FINDINGS")
        print("=" * 70)
        for f in all_findings:
            print(f"\n## {f['probe']}")
            for k, v in f.items():
                if k == "probe":
                    continue
                print(f"  {k}: {v}")

        return 0
    finally:
        driver.close()


if __name__ == "__main__":
    sys.exit(main())
