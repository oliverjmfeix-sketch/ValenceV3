"""
Integrity check for state_predicate_id composite-key contract.

Runs post-seed-load to verify every state_predicate instance's stored id
matches what construct_state_predicate_id() would produce from its
structural tuple (label, threshold_value_double, operator_comparison,
reference_predicate_label), per architecture §4.5.1.1.

Drift between seed authoring, loader, projection, and query code causes
silent lookup failures — this check fails loudly at a known verification
point instead.
"""
from __future__ import annotations

from typing import NamedTuple

from typedb.driver import TransactionType

from app.services.predicate_id import construct_state_predicate_id


class PredicateIntegrityError(RuntimeError):
    """Raised when stored state_predicate_id values diverge from the
    construction rule defined in app/services/predicate_id.py.
    """


class PredicateIdDrift(NamedTuple):
    stored_id: str
    label: str
    threshold_value_double: float | None
    operator_comparison: str | None
    reference_predicate_label: str | None
    reconstructed_id: str


def _attr_or_none(row, key: str):
    """Read an optional attribute concept out of a ConceptRow, returning
    its Python value or None when the variable is unbound.
    """
    concept = row.get(key)
    if concept is None:
        return None
    return concept.as_attribute().get_value()


def verify_state_predicate_ids(driver, database_name: str) -> list[PredicateIdDrift]:
    """Query every state_predicate, reconstruct its id from structural fields,
    compare to the stored id. Return the list of drifts (empty list when all
    instances match the construction rule).
    """
    drifts: list[PredicateIdDrift] = []

    tx = driver.transaction(database_name, TransactionType.READ)
    try:
        query = """
            match
                $p isa state_predicate,
                    has state_predicate_id $sid,
                    has state_predicate_label $label;
                try { $p has threshold_value_double $t; };
                try { $p has operator_comparison $op; };
                try { $p has reference_predicate_label $ref; };
            select $sid, $label, $t, $op, $ref;
        """
        result = tx.query(query).resolve()
        for row in result.as_concept_rows():
            stored_id = row.get("sid").as_attribute().get_value()
            label = row.get("label").as_attribute().get_value()
            threshold = _attr_or_none(row, "t")
            op = _attr_or_none(row, "op")
            ref = _attr_or_none(row, "ref")

            reconstructed = construct_state_predicate_id(
                label=label,
                threshold_value_double=threshold,
                operator_comparison=op,
                reference_predicate_label=ref,
            )

            if stored_id != reconstructed:
                drifts.append(PredicateIdDrift(
                    stored_id=stored_id,
                    label=label,
                    threshold_value_double=threshold,
                    operator_comparison=op,
                    reference_predicate_label=ref,
                    reconstructed_id=reconstructed,
                ))
    finally:
        try:
            if tx.is_open():
                tx.close()
        except Exception:  # noqa: BLE001
            pass

    return drifts


def assert_state_predicate_ids_consistent(driver, database_name: str) -> None:
    """Raise PredicateIntegrityError if any state_predicate id diverges from
    what construct_state_predicate_id() would produce for the same structural
    tuple.
    """
    drifts = verify_state_predicate_ids(driver, database_name)
    if not drifts:
        return

    lines = [
        f"state_predicate_id drift detected in database {database_name!r}: "
        f"{len(drifts)} predicate(s) do not match construct_state_predicate_id() output.",
    ]
    for d in drifts:
        lines.append(
            f"  stored='{d.stored_id}' reconstructed='{d.reconstructed_id}' "
            f"(label={d.label}, threshold={d.threshold_value_double}, "
            f"op={d.operator_comparison}, ref={d.reference_predicate_label})"
        )
    lines.append(
        "Check that state_predicates_seed.tql ids are generated via the same "
        "construction rule defined in §4.5.1.1 of the architecture doc."
    )
    raise PredicateIntegrityError("\n".join(lines))
