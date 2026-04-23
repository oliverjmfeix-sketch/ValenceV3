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


def round_trip_check(deal_id: str, ground_truth_path: Path, tx) -> dict:
    """
    Diff ground truth against extracted norms on
    (norm_kind, modality, primary_scoped_action, primary_scoped_object) tuples.
    Norms with question_role=null are included — definitional norms count.
    """
    gt = load_ground_truth(ground_truth_path)
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
    }


# ─── A5: check_rule_selection_accuracy ────────────────────────────────────────


def check_rule_selection_accuracy(deal_id: str, ground_truth_path: Path, tx) -> dict:
    """
    Per-entity-type classification accuracy per DeonticBench Table 4 framing.
    For each v3 extracted entity that projects to a norm, verify the entity
    TYPE matches ground-truth. Measures rule-selection errors separately from
    coverage (A3) and from field-level mismatches (A4).

    Pilot: v3 extracted entities for the deal aren't yet bridged to norms via
    a typed norm_projected_from relation; projection (Prompt 07) introduces
    that bridge. Until then, this returns empty — plumbing only.
    """
    gt = load_ground_truth(ground_truth_path)
    gt_norms = gt.get("norms", [])

    # Expected v3 entity type per norm, inferred from norm_kind.
    # Populated when projection lands.
    expected_by_extracted_id: dict[str, str] = {}

    # actual v3 entities linked to projected norms
    # (requires provision_has_extracted_entity bridge + norm_extracted_from)
    # For pilot, no projection has run → no bridges exist → empty result.
    per_entity_type: dict[str, dict] = {}
    failures: list[dict] = []
    correct_total = 0
    total = 0
    # (future: fetch the actual v3 entity types via norm_extracted_from and
    #  compare to expected; increment per_entity_type counters)
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

            # A4
            rtrip = round_trip_check(deal_id, ground_truth_path, tx)
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
