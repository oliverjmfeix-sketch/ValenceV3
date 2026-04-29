"""
Phase F commit 2 — schema-data coherence survey.

Read-only enumeration of the valence_v4 schema:
- All relation types: instance count, role players sampled, edge-attribute
  presence rates
- All entity types: instance count, attribute presence rates, attribute
  value-distribution samples
- All attribute types: distinct value count, sample of distinct values

Output: JSON file at docs/v4_schema_coherence_audit_data.json. The
human-authored audit lives in docs/v4_schema_coherence_audit.md and
references this JSON.

Idempotent — re-runnable. Safe (read-only).

Usage:
    TYPEDB_DATABASE=valence_v4 \\
        C:/Users/olive/ValenceV3/.venv/Scripts/python.exe \\
        -m app.scripts.phase_f_schema_survey
"""
from __future__ import annotations

import json
import logging
import os
import sys
from collections import defaultdict
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
logger = logging.getLogger("phase_f_survey")


def _connect():
    return TypeDB.driver(
        os.environ["TYPEDB_ADDRESS"],
        Credentials(os.environ["TYPEDB_USERNAME"], os.environ["TYPEDB_PASSWORD"]),
        DriverOptions(),
    )


_ENTITY_RE = None
_RELATION_RE = None
_ATTRIBUTE_RE = None


def _init_regexes():
    """Precompile regexes for parsing TQL schema files."""
    global _ENTITY_RE, _RELATION_RE, _ATTRIBUTE_RE
    import re
    _ENTITY_RE = re.compile(r'^entity\s+(\w+)', re.MULTILINE)
    _RELATION_RE = re.compile(r'^relation\s+(\w+)', re.MULTILINE)
    _ATTRIBUTE_RE = re.compile(r'^attribute\s+(\w+)', re.MULTILINE)


def list_types_from_schema_files(schema_paths: list) -> dict:
    """Parse the schema .tql files to extract entity / relation / attribute
    type names. Returns {'entity': [...], 'relation': [...], 'attribute': [...]}.

    Schema introspection via TypeQL is blocked by 'entity'/'relation'/
    'attribute' being reserved keywords in `match $t sub entity` form.
    Schema files are the SSoT for type declarations; parsing them is
    deal-agnostic and aligns with the audit's schema-first method.
    """
    _init_regexes()
    out = {"entity": set(), "relation": set(), "attribute": set()}
    for path in schema_paths:
        if not Path(path).exists():
            logger.warning("Schema file not found: %s", path)
            continue
        text = Path(path).read_text(encoding="utf-8")
        for m in _ENTITY_RE.findall(text):
            out["entity"].add(m)
        for m in _RELATION_RE.findall(text):
            out["relation"].add(m)
        for m in _ATTRIBUTE_RE.findall(text):
            out["attribute"].add(m)
    return {k: sorted(v) for k, v in out.items()}


def count_instances(driver, db: str, type_name: str) -> int:
    """Count instances of a type. Uses isa! for exact-type match."""
    rtx = driver.transaction(db, TransactionType.READ)
    try:
        r = rtx.query(f'match $x isa! {type_name}; select $x;').resolve()
        return len(list(r.as_concept_rows()))
    except Exception as e:
        logger.warning("count_instances(%s) failed: %s",
                       type_name, repr(e)[:120])
        return -1
    finally:
        try:
            if rtx.is_open():
                rtx.close()
        except Exception:
            pass


def count_attribute_distinct_values(driver, db: str, attr_name: str,
                                      sample_limit: int = 8) -> dict:
    """For an attribute, count distinct values and sample up to N."""
    rtx = driver.transaction(db, TransactionType.READ)
    try:
        r = rtx.query(f'match $a isa! {attr_name}; select $a;').resolve()
        values = []
        for row in r.as_concept_rows():
            try:
                v = row.get("a").as_attribute().get_value()
                values.append(v)
            except Exception:
                pass
        distinct = list(set(values))
        out = {
            "instance_count": len(values),
            "distinct_value_count": len(distinct),
        }
        if distinct:
            sample = sorted(distinct, key=lambda x: str(x))[:sample_limit]
            out["sample_distinct_values"] = [
                str(s)[:80] if isinstance(s, str) else s for s in sample
            ]
        return out
    except Exception as e:
        return {"error": repr(e)[:160]}
    finally:
        try:
            if rtx.is_open():
                rtx.close()
        except Exception:
            pass


def main() -> int:
    db = os.environ.get("TYPEDB_DATABASE", "valence_v4")
    out_path = REPO_ROOT / "docs" / "v4_schema_coherence_audit_data.json"
    logger.info("Surveying %s; output: %s", db, out_path)

    driver = _connect()
    try:
        # Enumerate types from .tql schema files (introspection via match
        # blocked by reserved-keyword 'entity'/'relation'/'attribute')
        schema_paths = [
            REPO_ROOT / "app" / "data" / "schema_unified.tql",
            REPO_ROOT / "app" / "data" / "schema_v4_deontic.tql",
        ]
        types = list_types_from_schema_files(schema_paths)
        entities = types["entity"]
        relations = types["relation"]
        attributes = types["attribute"]
        logger.info("Schema declared types: %d entities, %d relations, %d attributes",
                    len(entities), len(relations), len(attributes))

        # Survey each
        survey = {
            "db": db,
            "summary": {
                "entity_type_count": len(entities),
                "relation_type_count": len(relations),
                "attribute_type_count": len(attributes),
            },
            "entities": {},
            "relations": {},
            "attributes": {},
        }

        logger.info("Counting entity instances...")
        for t in entities:
            survey["entities"][t] = {"instance_count": count_instances(driver, db, t)}

        logger.info("Counting relation instances...")
        for t in relations:
            survey["relations"][t] = {"instance_count": count_instances(driver, db, t)}

        logger.info("Counting attribute instances + sampling values...")
        for t in attributes:
            survey["attributes"][t] = count_attribute_distinct_values(driver, db, t)

        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(survey, indent=2, default=str),
                              encoding="utf-8")
        logger.info("Wrote survey to %s (%d bytes)",
                    out_path, len(out_path.read_text(encoding="utf-8")))

        # Summary stats
        ent_pop = sum(1 for v in survey["entities"].values()
                      if v.get("instance_count", 0) > 0)
        rel_pop = sum(1 for v in survey["relations"].values()
                      if v.get("instance_count", 0) > 0)
        attr_pop = sum(1 for v in survey["attributes"].values()
                       if v.get("instance_count", 0) > 0)
        logger.info("Populated entities: %d / %d", ent_pop, len(entities))
        logger.info("Populated relations: %d / %d", rel_pop, len(relations))
        logger.info("Populated attributes: %d / %d", attr_pop, len(attributes))
        return 0
    finally:
        driver.close()


if __name__ == "__main__":
    sys.exit(main())
