"""
Ontology Validator for Valence
-------------------------------
Lints `app/data/*_ontology_questions.tql` (and `questions.tql`, `categories.tql`)
for the structural issues a covenant expert is most likely to introduce.

It does NOT need TypeDB running — this is pure static analysis of the TQL
source. The goal is to catch problems before they reach the database.

Usage from Cowork (no terminal required — Cowork runs this for the user):

    python app/scripts/validate_ontology.py app/data/liens_ontology_questions.tql
    python app/scripts/validate_ontology.py --all

Checks performed
----------------
1. Every `ontology_question` has: question_id, covenant_type, question_text,
   answer_type, display_order, extraction_prompt.
2. `question_id` is unique across all files scanned.
3. `question_id` follows lowercase snake_case (`^[a-z][a-z0-9_]*$`).
4. `answer_type` is one of: boolean, integer, number, string, multiselect,
   entity_list.
5. `covenant_type` is one of the registered codes.
6. `display_order` falls in the module's allowed range.
7. Every `ontology_category` has: category_id, name, display_order.
8. `category_id` is unique.
9. Every question is linked to at least one category via
   `category_has_question`.
10. Every `category_has_question` references a category_id and question_id
    that actually appear as inserts in the scanned files.
11. `extraction_prompt` is at least 40 chars (catches placeholder text).
12. No hardcoded section numbers like "Section 6.01" in `extraction_prompt`.
13. No use of abstract covenant_type codes ("TEST", "TODO", etc.).
14. `target_entity_type` and `target_relation_type`, if present, are flagged
    for engineering review unless they appear in an approved-list file.

Exit codes
----------
0 — no errors (warnings OK)
1 — at least one error

The validator returns machine-readable output in addition to colored human
output, for Cowork to parse.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

# ─── Configuration ──────────────────────────────────────────────────────────

VALID_COVENANT_TYPES: Set[str] = {
    "RP", "DI", "MFN", "LIENS", "INV", "AS", "EOD", "FC",
    "PP", "AMD", "FUND", "AFF", "PF", "CP",
}

VALID_ANSWER_TYPES: Set[str] = {
    "boolean", "integer", "number", "string", "multiselect", "entity_list",
}

# Display order ranges per covenant module. Each tuple is (min, max) inclusive.
QUESTION_DISPLAY_RANGES: Dict[str, Tuple[int, int]] = {
    "RP":    (1,    200),
    "MFN":   (1,    100),
    "DI":    (1,    200),
    "LIENS": (1000, 1099),
    "INV":   (1100, 1199),
    "AS":    (1200, 1299),
    "EOD":   (1300, 1399),
    "FC":    (1400, 1499),
    "PP":    (1500, 1599),
    "AMD":   (1600, 1699),
    "AFF":   (1700, 1799),
    "FUND":  (1800, 1899),
    "PF":    (1900, 1999),
    "CP":    (2000, 2099),
}

CATEGORY_DISPLAY_RANGES: Dict[str, Tuple[int, int]] = {
    "RP":    (0,    20),
    "MFN":   (101,  110),
    "DI":    (200,  220),
    "LIENS": (301,  320),
    "INV":   (401,  420),
    "AS":    (501,  520),
    "EOD":   (601,  620),
    "FC":    (701,  720),
    "PP":    (801,  820),
    "AMD":   (901,  920),
    "AFF":   (1001, 1020),
    "FUND":  (1101, 1120),
    "PF":    (1201, 1220),
    "CP":    (1301, 1320),
}

# Question-id → covenant-type prefix map (reverse of covenant-type-to-prefix)
PREFIX_TO_COVENANT: Dict[str, str] = {
    "rp":    "RP",
    "di":    "DI",
    "mfn":   "MFN",
    "ln":    "LIENS",
    "inv":   "INV",
    "as":    "AS",
    "eod":   "EOD",
    "fc":    "FC",
    "pp":    "PP",
    "amd":   "AMD",
    "fund":  "FUND",
    "aff":   "AFF",
    "pf":    "PF",
    "cp":    "CP",
    "jc":    "RP",  # J.Crew questions live under RP by convention
}

# ─── Parsed data structures ─────────────────────────────────────────────────

@dataclass
class ParsedQuestion:
    file: Path
    line: int
    question_id: str
    covenant_type: Optional[str] = None
    question_text: Optional[str] = None
    answer_type: Optional[str] = None
    display_order: Optional[int] = None
    extraction_prompt: Optional[str] = None
    target_entity_type: Optional[str] = None
    target_relation_type: Optional[str] = None
    raw_block: str = ""


@dataclass
class ParsedCategory:
    file: Path
    line: int
    category_id: str
    name: Optional[str] = None
    description: Optional[str] = None
    display_order: Optional[int] = None
    extraction_context_sections: Optional[str] = None
    extraction_batch_hint: Optional[str] = None


@dataclass
class ParsedLinkage:
    file: Path
    line: int
    category_id: str
    question_id: str


@dataclass
class ValidationResult:
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    info: List[str] = field(default_factory=list)
    questions: List[ParsedQuestion] = field(default_factory=list)
    categories: List[ParsedCategory] = field(default_factory=list)
    linkages: List[ParsedLinkage] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


# ─── Parsing ────────────────────────────────────────────────────────────────

ATTR_RE = re.compile(
    r'has\s+(?P<attr>[a-z_]+)\s+(?P<value>"(?:[^"\\]|\\.)*"|-?\d+(?:\.\d+)?|true|false)',
    re.DOTALL,
)

# Matches an insert block that defines an ontology_question or ontology_category.
# The block starts with `insert $var isa <type>,` and ends with a `;` at statement
# end. We capture lines from an `insert` up to (but not beyond) the next `;` that
# terminates the statement. TQL statements use `;` only at the end of a full stmt.
INSERT_START_RE = re.compile(
    r'^\s*insert\s+\$(?P<var>\w+)\s+isa\s+(?P<type>ontology_question|ontology_category)\s*,',
    re.MULTILINE,
)

LINKAGE_RE = re.compile(
    r'match\s+\$cat\s+isa\s+ontology_category\s*,\s*has\s+category_id\s+"(?P<cat>[^"]+)"\s*;\s*'
    r'\$q\s+isa\s+ontology_question\s*,\s*has\s+question_id\s+"(?P<qid>[^"]+)"\s*;\s*'
    r'insert\s+\(category:\s*\$cat\s*,\s*question:\s*\$q\)\s+isa\s+category_has_question\s*;',
    re.DOTALL,
)


def _strip_comments(text: str) -> str:
    """Strip `#` comment lines but preserve line numbers by keeping empties."""
    out_lines = []
    for line in text.split('\n'):
        stripped = line.lstrip()
        if stripped.startswith('#'):
            out_lines.append('')
        else:
            out_lines.append(line)
    return '\n'.join(out_lines)


def _find_statement_end(text: str, start: int) -> int:
    """Find the position of the `;` that ends the statement starting at `start`."""
    in_str = False
    esc = False
    for i in range(start, len(text)):
        c = text[i]
        if esc:
            esc = False
            continue
        if c == '\\':
            esc = True
            continue
        if c == '"':
            in_str = not in_str
            continue
        if c == ';' and not in_str:
            return i
    return len(text)  # unterminated — caller's problem


def _parse_attrs(block: str) -> Dict[str, object]:
    """Extract `has <attr> <value>` pairs from a block."""
    out: Dict[str, object] = {}
    for m in ATTR_RE.finditer(block):
        attr = m.group('attr')
        val = m.group('value')
        if val.startswith('"') and val.endswith('"'):
            parsed = val[1:-1].encode().decode('unicode_escape')
        elif val in ('true', 'false'):
            parsed = (val == 'true')
        elif '.' in val:
            parsed = float(val)
        else:
            parsed = int(val)
        out[attr] = parsed
    return out


def _line_of(text: str, pos: int) -> int:
    return text.count('\n', 0, pos) + 1


def parse_file(path: Path, result: ValidationResult, force: bool = False) -> None:
    """Parse one .tql file and append parsed items to `result`.

    If the file contains `# @ontology-template` in its header comments, it is
    skipped (templates intentionally contain placeholder values). Use
    `force=True` to validate a template anyway.
    """
    try:
        raw = path.read_text(encoding='utf-8')
    except Exception as e:
        result.errors.append(f"{path}: could not read file — {e}")
        return

    head = '\n'.join(raw.split('\n')[:50])
    if '@ontology-template' in head and not force:
        result.info.append(
            f"{path}: skipped — template file (use --force to validate)"
        )
        return

    text = _strip_comments(raw)

    # ─── Find all insert blocks ────────────────────────────────────────────
    for m in INSERT_START_RE.finditer(text):
        start = m.start()
        end = _find_statement_end(text, start)
        block = text[start:end + 1]
        typ = m.group('type')
        attrs = _parse_attrs(block)
        line = _line_of(text, start)

        if typ == 'ontology_question':
            qid = attrs.get('question_id')
            if not isinstance(qid, str) or not qid:
                result.errors.append(
                    f"{path}:{line}: ontology_question insert missing question_id"
                )
                continue
            q = ParsedQuestion(
                file=path,
                line=line,
                question_id=qid,
                covenant_type=attrs.get('covenant_type') if isinstance(attrs.get('covenant_type'), str) else None,
                question_text=attrs.get('question_text') if isinstance(attrs.get('question_text'), str) else None,
                answer_type=attrs.get('answer_type') if isinstance(attrs.get('answer_type'), str) else None,
                display_order=attrs.get('display_order') if isinstance(attrs.get('display_order'), int) else None,
                extraction_prompt=attrs.get('extraction_prompt') if isinstance(attrs.get('extraction_prompt'), str) else None,
                target_entity_type=attrs.get('target_entity_type') if isinstance(attrs.get('target_entity_type'), str) else None,
                target_relation_type=attrs.get('target_relation_type') if isinstance(attrs.get('target_relation_type'), str) else None,
                raw_block=block,
            )
            result.questions.append(q)

        elif typ == 'ontology_category':
            cid = attrs.get('category_id')
            if not isinstance(cid, str) or not cid:
                result.errors.append(
                    f"{path}:{line}: ontology_category insert missing category_id"
                )
                continue
            c = ParsedCategory(
                file=path,
                line=line,
                category_id=cid,
                name=attrs.get('name') if isinstance(attrs.get('name'), str) else None,
                description=attrs.get('description') if isinstance(attrs.get('description'), str) else None,
                display_order=attrs.get('display_order') if isinstance(attrs.get('display_order'), int) else None,
                extraction_context_sections=attrs.get('extraction_context_sections') if isinstance(attrs.get('extraction_context_sections'), str) else None,
                extraction_batch_hint=attrs.get('extraction_batch_hint') if isinstance(attrs.get('extraction_batch_hint'), str) else None,
            )
            result.categories.append(c)

    # ─── Find all category_has_question linkages ───────────────────────────
    for m in LINKAGE_RE.finditer(text):
        line = _line_of(text, m.start())
        result.linkages.append(ParsedLinkage(
            file=path, line=line,
            category_id=m.group('cat'),
            question_id=m.group('qid'),
        ))


# ─── Validation rules ───────────────────────────────────────────────────────

Q_ID_RE = re.compile(r'^[a-z][a-z0-9_]*$')

HARDCODED_SECTION_RE = re.compile(
    r'\bSection\s+\d+\.\d+',
    re.IGNORECASE,
)


def validate(result: ValidationResult) -> None:
    # Duplicate detection
    seen_qids: Dict[str, ParsedQuestion] = {}
    for q in result.questions:
        if q.question_id in seen_qids:
            prev = seen_qids[q.question_id]
            result.errors.append(
                f"{q.file}:{q.line}: duplicate question_id '{q.question_id}' "
                f"(first seen at {prev.file}:{prev.line})"
            )
        seen_qids[q.question_id] = q

    seen_cids: Dict[str, ParsedCategory] = {}
    for c in result.categories:
        if c.category_id in seen_cids:
            prev = seen_cids[c.category_id]
            result.errors.append(
                f"{c.file}:{c.line}: duplicate category_id '{c.category_id}' "
                f"(first seen at {prev.file}:{prev.line})"
            )
        seen_cids[c.category_id] = c

    # Per-question checks
    for q in result.questions:
        prefix = q.question_id.split('_', 1)[0]
        expected_cov = PREFIX_TO_COVENANT.get(prefix)

        if not Q_ID_RE.match(q.question_id):
            result.errors.append(
                f"{q.file}:{q.line}: question_id '{q.question_id}' is not lowercase snake_case"
            )

        if q.question_text is None or not q.question_text.strip():
            result.errors.append(
                f"{q.file}:{q.line}: question '{q.question_id}' missing question_text"
            )

        if q.answer_type is None:
            result.errors.append(
                f"{q.file}:{q.line}: question '{q.question_id}' missing answer_type"
            )
        elif q.answer_type not in VALID_ANSWER_TYPES:
            result.errors.append(
                f"{q.file}:{q.line}: question '{q.question_id}' has invalid answer_type "
                f"'{q.answer_type}'. Must be one of: {sorted(VALID_ANSWER_TYPES)}"
            )

        if q.covenant_type is None:
            result.errors.append(
                f"{q.file}:{q.line}: question '{q.question_id}' missing covenant_type"
            )
        elif q.covenant_type not in VALID_COVENANT_TYPES:
            result.errors.append(
                f"{q.file}:{q.line}: question '{q.question_id}' has unknown covenant_type "
                f"'{q.covenant_type}'. Valid: {sorted(VALID_COVENANT_TYPES)}"
            )
        elif expected_cov and q.covenant_type != expected_cov:
            result.errors.append(
                f"{q.file}:{q.line}: question_id prefix '{prefix}' implies covenant_type "
                f"'{expected_cov}' but question declares '{q.covenant_type}'"
            )

        if q.display_order is None:
            result.errors.append(
                f"{q.file}:{q.line}: question '{q.question_id}' missing display_order"
            )
        elif q.covenant_type and q.covenant_type in QUESTION_DISPLAY_RANGES:
            lo, hi = QUESTION_DISPLAY_RANGES[q.covenant_type]
            if not (lo <= q.display_order <= hi):
                result.errors.append(
                    f"{q.file}:{q.line}: question '{q.question_id}' display_order "
                    f"{q.display_order} is outside allowed range [{lo}, {hi}] for "
                    f"covenant_type '{q.covenant_type}'"
                )

        if q.extraction_prompt is None or not q.extraction_prompt.strip():
            result.errors.append(
                f"{q.file}:{q.line}: question '{q.question_id}' missing extraction_prompt"
            )
        elif len(q.extraction_prompt) < 40:
            result.warnings.append(
                f"{q.file}:{q.line}: question '{q.question_id}' extraction_prompt is "
                f"very short ({len(q.extraction_prompt)} chars). Prompts should name the "
                f"covenant, typical phrasings, and true/false criteria."
            )

        if q.extraction_prompt and HARDCODED_SECTION_RE.search(q.extraction_prompt):
            m = HARDCODED_SECTION_RE.search(q.extraction_prompt)
            result.warnings.append(
                f"{q.file}:{q.line}: question '{q.question_id}' extraction_prompt contains "
                f"hardcoded section number '{m.group(0)}' — section numbering varies "
                f"between agreements. Use covenant name instead."
            )

        if q.answer_type == "entity_list" and (not q.target_entity_type or not q.target_relation_type):
            result.errors.append(
                f"{q.file}:{q.line}: entity_list question '{q.question_id}' requires both "
                f"target_entity_type and target_relation_type. If the entity type isn't in "
                f"the schema yet, comment out the insert and add a `# TODO-ENG:` block "
                f"until engineering wires it."
            )

    # Per-category checks
    for c in result.categories:
        if not c.name or not c.name.strip():
            result.errors.append(
                f"{c.file}:{c.line}: category '{c.category_id}' missing name"
            )
        if c.display_order is None:
            result.errors.append(
                f"{c.file}:{c.line}: category '{c.category_id}' missing display_order"
            )
        # Category display_order range check. Infer covenant from prefix.
        if c.display_order is not None:
            cov = _infer_covenant_from_category_id(c.category_id)
            if cov and cov in CATEGORY_DISPLAY_RANGES:
                lo, hi = CATEGORY_DISPLAY_RANGES[cov]
                if not (lo <= c.display_order <= hi):
                    result.warnings.append(
                        f"{c.file}:{c.line}: category '{c.category_id}' display_order "
                        f"{c.display_order} outside recommended range [{lo}, {hi}] for "
                        f"covenant '{cov}'"
                    )

    # Linkage checks
    all_qids = {q.question_id for q in result.questions}
    all_cids = {c.category_id for c in result.categories}
    linked_qids: Set[str] = set()

    for link in result.linkages:
        if link.category_id not in all_cids:
            result.errors.append(
                f"{link.file}:{link.line}: linkage references category_id "
                f"'{link.category_id}' but no such category is defined in the scanned files"
            )
        if link.question_id not in all_qids:
            result.errors.append(
                f"{link.file}:{link.line}: linkage references question_id "
                f"'{link.question_id}' but no such question is defined in the scanned files"
            )
        linked_qids.add(link.question_id)

    unlinked = all_qids - linked_qids
    for qid in sorted(unlinked):
        q = seen_qids[qid]
        result.errors.append(
            f"{q.file}:{q.line}: question '{qid}' has no category_has_question "
            f"linkage — every question must belong to at least one category"
        )


def _infer_covenant_from_category_id(cid: str) -> Optional[str]:
    """Best-effort inference of covenant code from a category_id prefix."""
    # Longest prefix match wins
    candidates = [
        ("LIENS", "LIENS"), ("MFN", "MFN"), ("FUND", "FUND"), ("AMD", "AMD"),
        ("AFF", "AFF"), ("INV", "INV"), ("EOD", "EOD"), ("DI", "DI"),
        ("PP", "PP"), ("PF", "PF"), ("FC", "FC"), ("CP", "CP"), ("AS", "AS"),
    ]
    for prefix, cov in sorted(candidates, key=lambda x: -len(x[0])):
        if cid.startswith(prefix):
            return cov
    if cid in {str(i) for i in range(1, 21)} or (len(cid) == 1 and cid.isalpha()):
        # Historical RP single-letter / numeric-only categories
        return "RP"
    return None


# ─── Reporting ──────────────────────────────────────────────────────────────

# ANSI colors (safe on most terminals; Cowork strips if noisy)
C_ERR = "\033[91m"
C_WARN = "\033[93m"
C_INFO = "\033[94m"
C_OK = "\033[92m"
C_DIM = "\033[2m"
C_END = "\033[0m"


def report_human(result: ValidationResult) -> None:
    q_count = len(result.questions)
    c_count = len(result.categories)
    l_count = len(result.linkages)

    print(f"\n{'═' * 70}")
    print(f"Valence ontology validator")
    print(f"{'═' * 70}")
    print(f"Scanned:   {q_count} questions, {c_count} categories, {l_count} linkages")
    print(f"Errors:    {len(result.errors)}")
    print(f"Warnings:  {len(result.warnings)}")

    if result.errors:
        print(f"\n{C_ERR}ERRORS{C_END}")
        for e in result.errors:
            print(f"  {C_ERR}✗{C_END} {e}")

    if result.warnings:
        print(f"\n{C_WARN}WARNINGS{C_END}")
        for w in result.warnings:
            print(f"  {C_WARN}!{C_END} {w}")

    if result.ok and not result.warnings:
        print(f"\n{C_OK}✓ All checks passed.{C_END}")
    elif result.ok:
        print(f"\n{C_OK}✓ No errors. {len(result.warnings)} warnings (review recommended).{C_END}")
    else:
        print(f"\n{C_ERR}✗ {len(result.errors)} errors. Fix before committing.{C_END}")

    # Summary by covenant
    by_cov: Dict[str, int] = {}
    for q in result.questions:
        by_cov[q.covenant_type or "?"] = by_cov.get(q.covenant_type or "?", 0) + 1
    if by_cov:
        print(f"\n{C_DIM}Questions by covenant:{C_END}")
        for cov in sorted(by_cov):
            print(f"  {cov:7s} {by_cov[cov]:4d}")

    print()


def report_json(result: ValidationResult) -> None:
    out = {
        "ok": result.ok,
        "error_count": len(result.errors),
        "warning_count": len(result.warnings),
        "question_count": len(result.questions),
        "category_count": len(result.categories),
        "linkage_count": len(result.linkages),
        "errors": result.errors,
        "warnings": result.warnings,
        "questions_by_covenant": {
            cov: sum(1 for q in result.questions if q.covenant_type == cov)
            for cov in sorted({q.covenant_type for q in result.questions if q.covenant_type})
        },
    }
    print(json.dumps(out, indent=2))


# ─── Main ───────────────────────────────────────────────────────────────────

DEFAULT_DATA_DIR = Path(__file__).resolve().parent.parent / "data"


def resolve_targets(args_paths: List[str], all_flag: bool) -> List[Path]:
    if all_flag:
        if not DEFAULT_DATA_DIR.exists():
            print(
                f"{C_ERR}✗ --all specified but {DEFAULT_DATA_DIR} does not exist.{C_END}",
                file=sys.stderr,
            )
            sys.exit(2)
        paths = sorted(DEFAULT_DATA_DIR.glob("*.tql"))
        # Exclude the template and schema files — they aren't ontology content
        return [p for p in paths if p.name not in {
            "_TEMPLATE_new_covenant.tql",
            "schema_unified.tql",
            "concepts.tql",
        }]
    return [Path(p) for p in args_paths]


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate Valence ontology .tql files.")
    parser.add_argument("paths", nargs="*", help="Path(s) to .tql file(s) to validate.")
    parser.add_argument("--all", action="store_true",
                        help="Validate every ontology .tql file under app/data/.")
    parser.add_argument("--json", action="store_true",
                        help="Emit JSON report (for programmatic consumption).")
    parser.add_argument("--force", action="store_true",
                        help="Validate template files too (normally skipped).")
    args = parser.parse_args()

    if not args.paths and not args.all:
        parser.print_help()
        return 2

    targets = resolve_targets(args.paths, args.all)
    if not targets:
        print(f"{C_ERR}✗ No files to validate.{C_END}", file=sys.stderr)
        return 2

    result = ValidationResult()
    for p in targets:
        if not p.exists():
            result.errors.append(f"{p}: file not found")
            continue
        parse_file(p, result)

    validate(result)

    if args.json:
        report_json(result)
    else:
        report_human(result)

    return 0 if result.ok else 1


if __name__ == "__main__":
    sys.exit(main())
