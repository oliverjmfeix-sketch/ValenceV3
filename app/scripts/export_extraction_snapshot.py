"""
Export v3 extraction artifact from valence_v4 to a TQL snapshot file.

The extracted Duck Creek RP data is a $12.95 artifact committed to cloud
TypeDB. Cloud DBs are a single point of failure — if the cluster is
deleted / corrupted / misconfigured, the artifact is gone unless there's
a local copy.

This script dumps the v3 extraction state (deal, provision, baskets,
blocker + exceptions, sweep_tiers, investment_pathways, and the scalar
answers that drive projection) as a TQL file suitable for restore via
`restore_extraction_snapshot.py` or manual loading.

Usage:
    py -3.12 -m app.scripts.export_extraction_snapshot --deal 6e76ed06
    # writes to app/data/extraction_snapshots/6e76ed06.tql

Scope of the snapshot:
    * deal + rp_provision entities
    * Every v3 RP basket subtype instance (9 concrete types) owned by the
      provision, with all attributes including the four rp_v4_* answers
      (capacity_composition, capacity_aggregation_function,
      object_class_multiselect, partial_applicability)
    * jcrew_blocker + all 5 blocker_exception subtypes
    * sweep_tier instances, investment_pathway instances
    * provision_has_basket, provision_has_extracted_entity,
      blocker_has_exception relations to wire the entities together

Intentionally NOT in the snapshot:
    * v4 projection output (norms, conditions, defeaters, scope edges) —
      regenerable via `deontic_projection --deal <id>`
    * Per-deal party instances — regenerable via seed
    * Schema / functions / seed data — regenerable via init_schema_v4
    * provision_has_answer relations (216 scalar Q&A answers) — not
      consumed by v4 projection; would add ~400 statements to the file
      with no projection-path benefit. Add a --with-answers flag later
      if a future prompt needs them.
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(REPO_ROOT))

from dotenv import load_dotenv
_main_env = Path("C:/Users/olive/ValenceV3/.env")
if _main_env.exists():
    load_dotenv(_main_env, override=True)
load_dotenv(REPO_ROOT / ".env", override=True)

from app.config import settings
from typedb.driver import TypeDB, Credentials, DriverOptions, TransactionType

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)-7s  %(message)s", datefmt="%H:%M:%S")
logger = logging.getLogger("export_snapshot")

SNAPSHOTS_DIR = REPO_ROOT / "app" / "data" / "extraction_snapshots"

# Concrete entity types to snapshot. Abstract parents (rp_basket,
# blocker_exception, rp_provision) get their instances via concrete-type
# iteration here. One row per entity; attributes follow.
_BASKET_TYPES = [
    "builder_basket", "ratio_basket", "general_rp_basket",
    "management_equity_basket", "tax_distribution_basket",
    "holdco_overhead_basket", "equity_award_basket",
    "unsub_distribution_basket", "general_investment_basket",
    "general_rdp_basket", "refinancing_rdp_basket", "ratio_rdp_basket",
    "builder_rdp_basket", "equity_funded_rdp_basket",
]
_EXCEPTION_TYPES = [
    "nonexclusive_license_exception", "intercompany_exception",
    "immaterial_ip_exception", "fair_value_exception",
    "ordinary_course_exception",
]
# Non-basket extracted entity types (other than blocker + exceptions).
_OTHER_ENTITY_TYPES = ["sweep_tier", "investment_pathway"]


def _tq_string(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n") + '"'


def _format_attr_value(val) -> str:
    """Serialize an attribute value to TQL literal form."""
    if isinstance(val, bool):
        return "true" if val else "false"
    if isinstance(val, (int,)) and not isinstance(val, bool):
        return str(val)
    if isinstance(val, float):
        return str(val)
    return _tq_string(str(val))


def _fetch_all_attrs(tx, concept) -> dict[str, object]:
    """Given an entity concept, return all its owned attributes as a dict."""
    iid = concept.get_iid()
    out: dict[str, object] = {}
    try:
        q = f'match $e iid {iid}; $e has $attr; $attr isa $atype; select $attr, $atype;'
        result = tx.query(q).resolve()
        for row in result.as_concept_rows():
            atype = row.get("atype").get_label()
            val = row.get("attr").as_attribute().get_value()
            if atype not in out:
                out[atype] = val
    except Exception as exc:  # noqa: BLE001
        logger.warning("attr fetch failed for %s: %s", iid, str(exc)[:120])
    return out


def _id_attr_for(entity_type: str) -> str:
    """Return the @key attribute name for a concrete entity type.

    Hardcoded per v3 schema conventions. A missed type is reported as
    a warning and skipped rather than silently passing through.
    """
    mapping = {
        "deal": "deal_id",
        "rp_provision": "provision_id",
        "builder_basket": "basket_id",
        "ratio_basket": "basket_id",
        "general_rp_basket": "basket_id",
        "management_equity_basket": "basket_id",
        "tax_distribution_basket": "basket_id",
        "holdco_overhead_basket": "basket_id",
        "equity_award_basket": "basket_id",
        "unsub_distribution_basket": "basket_id",
        "general_investment_basket": "basket_id",
        "general_rdp_basket": "basket_id",
        "refinancing_rdp_basket": "basket_id",
        "ratio_rdp_basket": "basket_id",
        "builder_rdp_basket": "basket_id",
        "equity_funded_rdp_basket": "basket_id",
        "jcrew_blocker": "blocker_id",
        "nonexclusive_license_exception": "exception_id",
        "intercompany_exception": "exception_id",
        "immaterial_ip_exception": "exception_id",
        "fair_value_exception": "exception_id",
        "ordinary_course_exception": "exception_id",
        "sweep_tier": "tier_id",
        "investment_pathway": "pathway_id",
    }
    return mapping[entity_type]


def _emit_entity_inserts(tx, entity_type: str, out_lines: list[str]) -> int:
    """Query all instances of a concrete entity type and emit insert
    statements. Each statement is standalone (`insert $e isa type, has a v, ...;`).
    """
    count = 0
    id_attr = _id_attr_for(entity_type)
    try:
        result = tx.query(f'match $e isa! {entity_type}; select $e;').resolve()
        concepts = [row.get("e") for row in result.as_concept_rows()]
    except Exception as exc:  # noqa: BLE001
        logger.warning("entity fetch for %s failed: %s", entity_type, str(exc)[:120])
        return 0

    for concept in concepts:
        attrs = _fetch_all_attrs(tx, concept)
        key_val = attrs.get(id_attr)
        if key_val is None:
            logger.warning("entity %s missing %s attribute; skipping", entity_type, id_attr)
            continue
        clauses = [f"has {name} {_format_attr_value(v)}" for name, v in sorted(attrs.items())]
        out_lines.append(f"insert $e isa {entity_type},")
        out_lines.append("    " + ",\n    ".join(clauses) + ";")
        out_lines.append("")
        count += 1
    return count


def _emit_relation_inserts(tx, out_lines: list[str]) -> dict[str, int]:
    """Emit match-insert statements for the RP-related relations that wire
    extracted entities together. Each statement is a standalone pair that
    looks up the role-players by their key attribute and creates the relation.

    Relations covered:
      * deal_has_provision  (deal, provision)
      * provision_has_basket  (provision, basket) — polymorphic over rp_basket subtypes
      * provision_has_extracted_entity subtypes (for jcrew, sweep_tier, pathway)
      * blocker_has_exception  (blocker, exception)
    """
    counts: dict[str, int] = {}

    # 1. deal_has_provision
    rows = list(tx.query('''
        match
          (deal: $d, provision: $p) isa deal_has_provision;
          $d has deal_id $did;
          $p has provision_id $pid;
        select $did, $pid;
    ''').resolve().as_concept_rows())
    for r in rows:
        did = r.get("did").as_attribute().get_value()
        pid = r.get("pid").as_attribute().get_value()
        out_lines.append("match")
        out_lines.append(f'  $d isa deal, has deal_id {_tq_string(did)};')
        out_lines.append(f'  $p isa rp_provision, has provision_id {_tq_string(pid)};')
        out_lines.append("insert")
        out_lines.append("  (deal: $d, provision: $p) isa deal_has_provision;")
        out_lines.append("")
    counts["deal_has_provision"] = len(rows)

    # 2. provision_has_basket — each RP basket (concrete type) linked to
    #    rp_provision. Polymorphic query on the abstract basket role.
    rows = list(tx.query('''
        match
          (provision: $p, basket: $b) isa provision_has_basket;
          $p has provision_id $pid;
          $b has basket_id $bid;
        select $pid, $bid;
    ''').resolve().as_concept_rows())
    for r in rows:
        pid = r.get("pid").as_attribute().get_value()
        bid = r.get("bid").as_attribute().get_value()
        out_lines.append("match")
        out_lines.append(f'  $p isa rp_provision, has provision_id {_tq_string(pid)};')
        out_lines.append(f'  $b isa rp_basket, has basket_id {_tq_string(bid)};')
        out_lines.append("insert")
        out_lines.append("  (provision: $p, basket: $b) isa provision_has_basket;")
        out_lines.append("")
    counts["provision_has_basket"] = len(rows)

    # 3. blocker_has_exception
    rows = list(tx.query('''
        match
          (blocker: $b, exception: $e) isa blocker_has_exception;
          $b has blocker_id $bid;
          $e has exception_id $eid;
        select $bid, $eid;
    ''').resolve().as_concept_rows())
    for r in rows:
        bid = r.get("bid").as_attribute().get_value()
        eid = r.get("eid").as_attribute().get_value()
        out_lines.append("match")
        out_lines.append(f'  $b isa jcrew_blocker, has blocker_id {_tq_string(bid)};')
        out_lines.append(f'  $e isa blocker_exception, has exception_id {_tq_string(eid)};')
        out_lines.append("insert")
        out_lines.append("  (blocker: $b, exception: $e) isa blocker_has_exception;")
        out_lines.append("")
    counts["blocker_has_exception"] = len(rows)

    # 4. Concrete sub-relations of provision_has_extracted_entity for
    #    non-basket entities (jcrew_blocker, sweep_tier, investment_pathway)
    #    and for RDP baskets (separate relation from provision_has_basket).
    #    Sub-relation names per v3 schema; note role name != peer type in
    #    some cases (provision_has_sweep_tier → role is `tier`, not `sweep_tier`).
    for rel_name, role_b, peer_type, peer_key in [
        ("provision_has_blocker",     "blocker",    "jcrew_blocker",      "blocker_id"),
        ("provision_has_sweep_tier",  "tier",       "sweep_tier",          "tier_id"),
        ("provision_has_pathway",     "pathway",    "investment_pathway",  "pathway_id"),
        ("provision_has_rdp_basket",  "rdp_basket", "rdp_basket",          "basket_id"),
    ]:
        try:
            rows = list(tx.query(f'''
                match
                  (provision: $p, {role_b}: $x) isa {rel_name};
                  $p has provision_id $pid;
                  $x has {peer_key} $xid;
                select $pid, $xid;
            ''').resolve().as_concept_rows())
        except Exception as exc:  # noqa: BLE001
            logger.debug("%s not present in schema: %s", rel_name, str(exc)[:80])
            continue
        for r in rows:
            pid = r.get("pid").as_attribute().get_value()
            xid = r.get("xid").as_attribute().get_value()
            out_lines.append("match")
            out_lines.append(f'  $p isa rp_provision, has provision_id {_tq_string(pid)};')
            out_lines.append(f'  $x isa {peer_type}, has {peer_key} {_tq_string(xid)};')
            out_lines.append("insert")
            out_lines.append(f"  (provision: $p, {role_b}: $x) isa {rel_name};")
            out_lines.append("")
        counts[rel_name] = len(rows)

    return counts


def export_deal_snapshot(driver, db_name: str, deal_id: str, output_path: Path) -> dict:
    """Dump the v3 extraction artifact for a given deal to a TQL file."""
    out_lines: list[str] = []

    # Header
    out_lines.append("# " + "═" * 76)
    out_lines.append(f"# v3 Extraction Snapshot — deal {deal_id}")
    out_lines.append(f"# Exported: {datetime.now(timezone.utc).isoformat()}")
    out_lines.append(f"# Source: {db_name} on {settings.normalized_typedb_address}")
    out_lines.append("#")
    out_lines.append("# Restore via `py -3.12 -m app.scripts.restore_extraction_snapshot`")
    out_lines.append("# against a freshly-initialized valence_v4 (schema + seeds loaded,")
    out_lines.append("# no extraction). Deal + provision are re-inserted idempotently; all")
    out_lines.append("# extracted entities follow; relations wire them together last.")
    out_lines.append("# " + "═" * 76)
    out_lines.append("")

    entity_counts: dict[str, int] = {}
    tx = driver.transaction(db_name, TransactionType.READ)
    try:
        # 1. deal + rp_provision
        out_lines.append("# ─── §1 deal + rp_provision ─────────────────────────────────────────────")
        out_lines.append("")
        for t in ("deal", "rp_provision"):
            entity_counts[t] = _emit_entity_inserts(tx, t, out_lines)

        # 2. RP baskets (all concrete subtypes)
        out_lines.append("# ─── §2 RP basket entities ──────────────────────────────────────────────")
        out_lines.append("")
        for t in _BASKET_TYPES:
            entity_counts[t] = _emit_entity_inserts(tx, t, out_lines)

        # 3. J.Crew blocker
        out_lines.append("# ─── §3 J.Crew blocker + exceptions ─────────────────────────────────────")
        out_lines.append("")
        entity_counts["jcrew_blocker"] = _emit_entity_inserts(tx, "jcrew_blocker", out_lines)
        for t in _EXCEPTION_TYPES:
            entity_counts[t] = _emit_entity_inserts(tx, t, out_lines)

        # 4. Other extracted entities
        out_lines.append("# ─── §4 sweep_tiers + investment_pathways ───────────────────────────────")
        out_lines.append("")
        for t in _OTHER_ENTITY_TYPES:
            entity_counts[t] = _emit_entity_inserts(tx, t, out_lines)

        # 5. Relations
        out_lines.append("# ─── §5 Relations wiring entities together ──────────────────────────────")
        out_lines.append("")
        rel_counts = _emit_relation_inserts(tx, out_lines)
    finally:
        tx.close()

    # Summary footer
    out_lines.append("# " + "═" * 76)
    out_lines.append("# Snapshot summary")
    out_lines.append("#")
    total_entities = sum(entity_counts.values())
    out_lines.append(f"#   entities: {total_entities}")
    for t, n in sorted(entity_counts.items()):
        if n:
            out_lines.append(f"#     {t}: {n}")
    total_relations = sum(rel_counts.values())
    out_lines.append(f"#   relations: {total_relations}")
    for r, n in sorted(rel_counts.items()):
        out_lines.append(f"#     {r}: {n}")
    out_lines.append("# " + "═" * 76)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(out_lines), encoding="utf-8", newline="\n")
    logger.info("wrote %s (%d bytes)", output_path, output_path.stat().st_size)
    logger.info("  entities: %d across %d types", total_entities, len([1 for n in entity_counts.values() if n]))
    logger.info("  relations: %d", total_relations)

    return {
        "entity_counts": entity_counts,
        "relation_counts": rel_counts,
        "output_path": str(output_path),
        "total_entities": total_entities,
        "total_relations": total_relations,
    }


def main() -> int:
    p = argparse.ArgumentParser(description="Dump v3 extraction for a deal to a TQL snapshot.")
    p.add_argument("--deal", required=True, help="deal_id in valence_v4")
    p.add_argument("--database", default=None, help="override source database")
    p.add_argument("--output", default=None, help="override output path")
    args = p.parse_args()

    db_name = args.database or settings.typedb_database
    output = Path(args.output) if args.output else SNAPSHOTS_DIR / f"{args.deal}.tql"

    driver = TypeDB.driver(
        settings.normalized_typedb_address,
        Credentials(settings.typedb_username, settings.typedb_password),
        DriverOptions(),
    )
    try:
        report = export_deal_snapshot(driver, db_name, args.deal, output)
        print(f"Snapshot written: {report['output_path']}")
        print(f"  {report['total_entities']} entities, {report['total_relations']} relations")
    finally:
        try:
            driver.close()
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
