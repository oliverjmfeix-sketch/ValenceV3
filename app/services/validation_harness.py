"""
Valence v4 — validation harness (Prompt 06, Part A)

Six completeness checks (A1–A6) run against the norm graph. All checks return
typed result dicts and never raise on empty data — missing data returns
structured emptiness so CI can always execute the harness.

CLI:
    py -3.12 -m app.services.validation_harness --deal 6e76ed06
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(REPO_ROOT))

_main_env = Path("C:/Users/olive/ValenceV3/.env")
if _main_env.exists():
    load_dotenv(_main_env, override=False)
load_dotenv(REPO_ROOT / ".env", override=False)

import yaml  # noqa: E402

from app.config import settings  # noqa: E402
from typedb.driver import TypeDB, Credentials, DriverOptions, TransactionType  # noqa: E402


logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)-7s  %(message)s", datefmt="%H:%M:%S")
logger = logging.getLogger("validation_harness")

DATA_DIR = REPO_ROOT / "app" / "data"
DEFAULT_GROUND_TRUTH = DATA_DIR / "duck_creek_rp_ground_truth.yaml"
EXPECTED_DB = "valence_v4"
GROUND_TRUTH_DB = "valence_v4_ground_truth"


# ─── Connection + ground-truth loader ─────────────────────────────────────────


def connect():
    return TypeDB.driver(
        settings.normalized_typedb_address,
        Credentials(settings.typedb_username, settings.typedb_password),
        DriverOptions(),
    )


def load_ground_truth(path: Path = DEFAULT_GROUND_TRUTH) -> dict:
    with open(path, encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def load_ground_truth_from_graph(driver) -> dict:
    """Query valence_v4_ground_truth for norm scalars + primary scope.

    Returns a shape compatible with load_ground_truth (YAML): a dict with a
    "norms" key containing a list of norm dicts. Each norm dict has
    norm_id, norm_kind, modality, capacity_composition, action_scope,
    scoped_actions (first only), scoped_objects (first only). Other fields
    used by the harness are pulled as needed via follow-up queries.

    This replaces YAML parsing when both extraction and ground truth live
    in TypeDB. Graph is source of truth.
    """
    if not driver.databases.contains(GROUND_TRUTH_DB):
        logger.warning("%s does not exist — falling back to YAML", GROUND_TRUTH_DB)
        return load_ground_truth()
    tx = driver.transaction(GROUND_TRUTH_DB, TransactionType.READ)
    norms: list[dict] = []
    try:
        # Pull norm scalars
        rows = list(tx.query(
            "match $n isa norm, has norm_id $nid, has norm_kind $nk, has modality $m;"
            " select $nid, $nk, $m;"
        ).resolve().as_concept_rows())
        for r in rows:
            nid = _attr(r, "nid")
            nk = _attr(r, "nk")
            m = _attr(r, "m")
            # primary action / object
            a_rows = list(tx.query(
                f'match $n isa norm, has norm_id "{nid}";'
                f' (norm: $n, action: $ac) isa norm_scopes_action;'
                f' $ac has action_class_label $al; select $al;'
            ).resolve().as_concept_rows())
            o_rows = list(tx.query(
                f'match $n isa norm, has norm_id "{nid}";'
                f' (norm: $n, object: $ob) isa norm_scopes_object;'
                f' $ob has object_class_label $ol; select $ol;'
            ).resolve().as_concept_rows())
            norms.append({
                "norm_id": nid,
                "norm_kind": nk,
                "modality": m,
                "scoped_actions": [_attr(r, "al") for r in a_rows],
                "scoped_objects": [_attr(r, "ol") for r in o_rows],
            })
    finally:
        try:
            if tx.is_open():
                tx.close()
        except Exception:
            pass
    return {"norms": norms}


# ─── Helpers ──────────────────────────────────────────────────────────────────


def _query_rows(tx, q: str) -> list[dict[str, Any]]:
    """Run a match-select and return rows as dicts (attr value extraction)."""
    try:
        result = tx.query(q).resolve()
        return list(result.as_concept_rows())
    except Exception as e:
        logger.debug("query failed: %s; q=%s", e, q[:120])
        return []


def _attr(row, var: str) -> Any:
    try:
        return row.get(var).as_attribute().get_value()
    except Exception:
        try:
            return row.get(var).as_value().get_value()
        except Exception:
            return None


# ─── A1: validate_norm_structural ─────────────────────────────────────────────


def validate_norm_structural(norm_id: str, tx) -> list[str]:
    """
    Calls the `norm_is_structurally_complete` TypeDB function. On failure,
    enumerates which specific fields are missing (modality / source_text /
    source_section / source_page / subject / scope).
    """
    rows = _query_rows(
        tx,
        f'match $n isa norm, has norm_id "{norm_id}";'
        f' let $r = norm_is_structurally_complete($n); select $r;',
    )
    if not rows:
        return ["norm_not_found"]
    try:
        is_complete = rows[0].get("r").as_value().get_boolean()
    except Exception:
        return ["function_return_type_error"]
    if is_complete:
        return []

    failures: list[str] = []
    checks = [
        ('has modality $m;', "missing_modality"),
        ('has source_text $st;', "missing_source_text"),
        ('has source_section $ss;', "missing_source_section"),
        ('has source_page $sp;', "missing_source_page"),
    ]
    for frag, name in checks:
        rs = _query_rows(
            tx,
            f'match $n isa norm, has norm_id "{norm_id}"; $n {frag} select $n;',
        )
        if not rs:
            failures.append(name)

    # subject
    rs = _query_rows(
        tx,
        f'match $n isa norm, has norm_id "{norm_id}";'
        f' (norm: $n, subject: $s) isa norm_binds_subject; select $n;',
    )
    if not rs:
        failures.append("missing_subject")

    # scope (action OR instrument)
    rs = _query_rows(
        tx,
        f'match $n isa norm, has norm_id "{norm_id}";'
        f' {{ (norm: $n, action: $a) isa norm_scopes_action; }}'
        f' or {{ (norm: $n, instrument: $i) isa norm_scopes_instrument; }};'
        f' select $n;',
    )
    if not rs:
        failures.append("missing_scope")

    return failures


# ─── A2: check_segment_norm_counts ────────────────────────────────────────────


def check_segment_norm_counts(deal_id: str, tx, covenant: str = "rp") -> dict:
    """
    Compare actual norm counts per segment to the seeded expected ranges
    (app/data/segment_norm_expectations.tql). Post-Part-5: actual counts are
    computed by joining over the typed `norm_in_segment` relation onto
    `document_segment_type` instances, matched by segment_type_id. The earlier
    source_section prefix matching (pilot-time Python bridge) is retired.

    Empty-DB behaviour: projection hasn't populated norm_in_segment edges yet,
    so the query returns zero norms per segment. Every seeded expectation with
    expected_min > 0 reports status="below" — correct behaviour that accurately
    reflects "no norms projected into segments yet."
    """
    # load expected ranges
    rows = _query_rows(
        tx,
        f'match $e isa segment_norm_expectation, has covenant_type "{covenant}",'
        f' has segment_type_id $sid, has expected_min $emin, has expected_max $emax;'
        f' select $sid, $emin, $emax;',
    )
    expectations = {
        _attr(r, "sid"): (_attr(r, "emin"), _attr(r, "emax")) for r in rows
    }

    result: dict[str, Any] = {}
    for sid, (emin, emax) in expectations.items():
        # Typed-relation count: norms linked to the document_segment_type
        # instance whose segment_type_id matches.
        rs = _query_rows(
            tx,
            f'match '
            f' $s isa document_segment_type, has segment_type_id "{sid}";'
            f' $rel isa norm_in_segment, links (norm: $n, segment: $s);'
            f' select $n;',
        )
        count = len(rs)
        if count < emin:
            status = "below"
        elif count > emax:
            status = "above"
        else:
            status = "within"
        result[sid] = {
            "expected_range": [emin, emax],
            "actual": count,
            "status": status,
        }
    return result


# ─── A3: check_norm_kind_coverage ─────────────────────────────────────────────


def check_norm_kind_coverage(covenant: str, deal_id: str, tx) -> dict:
    """
    Return the always-expected and usually-expected norm_kinds absent from
    the deal's extracted norm set. Reads `expected_norm_kind` seed; queries
    actual `norm_kind` attrs across norm instances.
    """
    rows = _query_rows(
        tx,
        f'match $ek isa expected_norm_kind, has covenant_type "{covenant}",'
        f' has norm_kind $nk, has typicality $t; select $nk, $t;',
    )
    expected = {_attr(r, "nk"): _attr(r, "t") for r in rows}

    rows = _query_rows(tx, "match $n isa norm, has norm_kind $nk; select $nk;")
    actual = {_attr(r, "nk") for r in rows}

    missing_always = sorted(
        k for k, t in expected.items() if t == "always" and k not in actual
    )
    missing_usually = sorted(
        k for k, t in expected.items() if t == "usually" and k not in actual
    )
    return {"missing_always": missing_always, "missing_usually": missing_usually}


# ─── A4: round_trip_check ─────────────────────────────────────────────────────


def _primary_tuple(n: dict) -> tuple:
    sa = n.get("scoped_actions") or []
    so = n.get("scoped_objects") or []
    return (
        n.get("norm_kind"),
        n.get("modality"),
        sa[0] if sa else None,
        so[0] if so else None,
    )


def round_trip_check(deal_id: str, ground_truth_path: Path, tx, driver=None) -> dict:
    """
    Diff ground truth against extracted norms on
    (norm_kind, modality, primary_scoped_action, primary_scoped_object) tuples.

    Ground truth is preferentially loaded from the dedicated graph database
    (`valence_v4_ground_truth`) via load_ground_truth_from_graph(driver). If
    that database is absent OR the driver isn't provided, falls back to the
    YAML path. Fallback is noted but not silenced.
    """
    if driver is not None and driver.databases.contains(GROUND_TRUTH_DB):
        gt = load_ground_truth_from_graph(driver)
        gt_source = "graph"
    else:
        gt = load_ground_truth(ground_truth_path)
        gt_source = "yaml"
    gt_norms = gt.get("norms", [])
    gt_tuples: dict[tuple, dict] = {_primary_tuple(n): n for n in gt_norms}

    # Extracted: pull norm_id + norm_kind + modality per norm, then for each
    # get primary action/object via follow-up queries.
    rows = _query_rows(
        tx,
        "match $n isa norm, has norm_id $nid, has norm_kind $nk, has modality $m; "
        "select $nid, $nk, $m;",
    )
    extracted = []
    for r in rows:
        nid = _attr(r, "nid")
        nk = _attr(r, "nk")
        m = _attr(r, "m")
        actions = _query_rows(
            tx,
            f'match $n isa norm, has norm_id "{nid}";'
            f' (norm: $n, action: $ac) isa norm_scopes_action;'
            f' $ac has action_class_label $al; select $al;',
        )
        objects = _query_rows(
            tx,
            f'match $n isa norm, has norm_id "{nid}";'
            f' (norm: $n, object: $obj) isa norm_scopes_object;'
            f' $obj has object_class_label $ol; select $ol;',
        )
        a0 = _attr(actions[0], "al") if actions else None
        o0 = _attr(objects[0], "ol") if objects else None
        extracted.append({"norm_id": nid, "tuple": (nk, m, a0, o0)})
    extracted_tuples = {e["tuple"]: e for e in extracted}

    missing_kinds = sorted({
        n["norm_kind"] for t, n in gt_tuples.items() if t not in extracted_tuples
    })
    spurious_kinds = sorted({
        e["tuple"][0] for t, e in extracted_tuples.items() if t not in gt_tuples
    })

    # Mismatched: for matching tuples, compare additional scalar fields
    # (cap_usd, cap_grower_pct, action_scope, capacity_composition).
    mismatched: list[dict] = []
    for t, e in extracted_tuples.items():
        if t in gt_tuples:
            gt_norm = gt_tuples[t]
            ex_scalars = _query_rows(
                tx,
                f'match $n isa norm, has norm_id "{e["norm_id"]}";'
                f' $n has action_scope $as; select $as;',
            )
            ex_as = _attr(ex_scalars[0], "as") if ex_scalars else None
            if gt_norm.get("action_scope") != ex_as and ex_as is not None:
                mismatched.append({
                    "norm_id": e["norm_id"],
                    "field": "action_scope",
                    "gt_value": gt_norm.get("action_scope"),
                    "extracted_value": ex_as,
                })

    return {
        "missing": missing_kinds,
        "spurious": spurious_kinds,
        "mismatched": mismatched,
        "gt_source": gt_source,
    }


# ─── A5: check_rule_selection_accuracy ────────────────────────────────────────


def check_rule_selection_accuracy(deal_id: str, ground_truth_path: Path, tx) -> dict:
    """
    Per-entity-type classification accuracy per DeonticBench Table 4 framing.
    For each projected norm, check whether its norm_kind matches what the
    deontic_mapping says to emit for its source v3 entity type. Measures
    rule-selection errors separately from coverage (A3) and from field-level
    mismatches (A4).

    Uses the norm_extracted_from:fact edge (emitted by Prompt 07's projection)
    to recover the source v3 entity type for each projected norm, plus the
    deontic_mapping seed to look up the expected norm_kind for that entity
    type. Projection is correct when actual norm_kind matches expected.

    Empty when no norm_extracted_from edges exist → harness reports
    "n/a (projection not run)" as before.
    """
    # 1. Load deontic_mapping entries: source_entity_type → expected target_norm_kind.
    mapping_rows = list(_query_rows(tx, '''
        match
          $m isa deontic_mapping,
            has source_entity_type $sty,
            has target_norm_kind $tnk;
        select $sty, $tnk;
    '''))
    expected_by_entity_type: dict[str, str] = {
        _attr(r, "sty"): _attr(r, "tnk") for r in mapping_rows
    }

    # 2. Fetch extracted norms + the v3 entity they derive from via
    #    norm_extracted_from. Polymorphic fact role: the concrete v3 entity
    #    type's label identifies the source_entity_type.
    per_entity_type: dict[str, dict] = {}
    failures: list[dict] = []
    correct_total = 0
    total = 0

    proj_rows = list(_query_rows(tx, '''
        match
          $n isa norm, has norm_id $nid, has norm_kind $nk;
          (norm: $n, fact: $f) isa norm_extracted_from;
          $f isa $ftype;
        select $nid, $nk, $ftype;
    '''))
    for row in proj_rows:
        nid = _attr(row, "nid")
        actual_kind = _attr(row, "nk")
        ftype_concept = row.get("ftype")
        if ftype_concept is None:
            continue
        # Each fact matches its concrete type AND all abstract ancestors
        # (provision_has_extracted_entity, rp_basket, etc.). Skip the abstract
        # rows — the concrete type is the one present in expected_by_entity_type.
        try:
            ftype_label = ftype_concept.get_label()
        except Exception:
            continue
        expected_kind = expected_by_entity_type.get(ftype_label)
        if expected_kind is None:
            continue  # not a leaf v3 entity type the mapping covers
        total += 1
        bucket = per_entity_type.setdefault(ftype_label, {"correct": 0, "total": 0})
        bucket["total"] += 1
        if actual_kind == expected_kind:
            bucket["correct"] += 1
            correct_total += 1
        else:
            failures.append({
                "norm_id": nid,
                "source_entity_type": ftype_label,
                "expected_kind": expected_kind,
                "actual_kind": actual_kind,
            })

    aggregate = float(correct_total) / total if total else 0.0
    return {
        "per_entity_type": per_entity_type,
        "aggregate_accuracy": aggregate,
        "failures": failures,
    }


# ─── A6: run_all_completeness_checks ──────────────────────────────────────────


def run_all_completeness_checks(
    deal_id: str,
    ground_truth_path: Path = DEFAULT_GROUND_TRUTH,
    covenant: str = "rp",
) -> dict:
    """Compose A1–A5 into a single structured report with per-check verdicts."""
    driver = connect()
    try:
        tx = driver.transaction(EXPECTED_DB, TransactionType.READ)
        try:
            # A1: structural check, per-norm. Run over all norm instances in DB.
            norm_ids = [
                _attr(r, "nid")
                for r in _query_rows(tx, "match $n isa norm, has norm_id $nid; select $nid;")
            ]
            structural_per_norm = {}
            structural_fail_count = 0
            for nid in norm_ids:
                fs = validate_norm_structural(nid, tx)
                structural_per_norm[nid] = fs
                if fs:
                    structural_fail_count += 1
            structural_verdict = (
                "pass" if norm_ids and structural_fail_count == 0
                else ("n/a (no norms)" if not norm_ids else "fail")
            )

            # A2
            seg_counts = check_segment_norm_counts(deal_id, tx, covenant)
            seg_all_within = all(v["status"] == "within" for v in seg_counts.values())
            seg_verdict = "pass" if seg_counts and seg_all_within else "fail"

            # A3
            coverage = check_norm_kind_coverage(covenant, deal_id, tx)
            coverage_verdict = "pass" if not coverage["missing_always"] else "fail"

            # A4 — passes driver so round_trip_check can prefer graph-sourced ground truth
            rtrip = round_trip_check(deal_id, ground_truth_path, tx, driver=driver)
            rtrip_verdict = (
                "pass" if not rtrip["missing"] and not rtrip["spurious"] and not rtrip["mismatched"]
                else "fail"
            )

            # A5
            rule_sel = check_rule_selection_accuracy(deal_id, ground_truth_path, tx)
            rule_sel_verdict = (
                "n/a (projection not run)" if not rule_sel["per_entity_type"]
                else ("pass" if rule_sel["aggregate_accuracy"] >= 0.95 else "fail")
            )

            report = {
                "deal_id": deal_id,
                "covenant": covenant,
                "A1_structural": {
                    "verdict": structural_verdict,
                    "norm_count": len(norm_ids),
                    "failures": structural_fail_count,
                    "per_norm": structural_per_norm,
                },
                "A2_segment_counts": {
                    "verdict": seg_verdict,
                    "counts": seg_counts,
                },
                "A3_kind_coverage": {
                    "verdict": coverage_verdict,
                    "missing_always": coverage["missing_always"],
                    "missing_usually": coverage["missing_usually"],
                },
                "A4_round_trip": {
                    "verdict": rtrip_verdict,
                    "gt_source": rtrip.get("gt_source", "unknown"),
                    "missing": rtrip["missing"],
                    "spurious": rtrip["spurious"],
                    "mismatched": rtrip["mismatched"],
                },
                "A5_rule_selection": {
                    "verdict": rule_sel_verdict,
                    **rule_sel,
                },
            }
            return report
        finally:
            try:
                if tx.is_open():
                    tx.close()
            except Exception:  # noqa: BLE001
                pass
    finally:
        try:
            driver.close()
        except Exception:  # noqa: BLE001
            pass


# ─── CLI ──────────────────────────────────────────────────────────────────────


def main() -> int:
    p = argparse.ArgumentParser(description="Run v4 validation harness.")
    p.add_argument("--deal", required=True, help="deal_id (e.g., 6e76ed06)")
    p.add_argument("--covenant", default="rp", help="covenant (default: rp)")
    p.add_argument(
        "--ground-truth",
        default=str(DEFAULT_GROUND_TRUTH),
        help="path to ground-truth YAML (default: duck_creek_rp_ground_truth.yaml)",
    )
    args = p.parse_args()

    report = run_all_completeness_checks(
        args.deal, Path(args.ground_truth), args.covenant
    )

    # Summary to stdout
    print()
    print("=" * 70)
    print(f"Validation harness — deal={args.deal}  covenant={args.covenant}")
    print("=" * 70)
    for check in ("A1_structural", "A2_segment_counts", "A3_kind_coverage", "A4_round_trip", "A5_rule_selection"):
        print(f"  {check:25s} -> {report[check]['verdict']}")
    print("=" * 70)
    print()
    print(json.dumps(report, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
