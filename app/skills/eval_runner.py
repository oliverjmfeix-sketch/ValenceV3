"""
Eval Runner Skill — Interactive gold standard evaluation.

Usage:
    from app.skills.eval_runner import run_eval_interactive
    await run_eval_interactive()
"""
import asyncio
from typing import Optional


# Available gold standard sets
EVAL_SETS = {
    "1": {
        "id": "lawyer_dc_rp",
        "name": "Lawyer Q&A — Duck Creek RP",
        "questions": 6,
        "covenant": "RP",
        "deal": "Duck Creek",
    },
    "2": {
        "id": "xtract_dc_rp_mfn",
        "name": "Xtract Report — Duck Creek RP+MFN",
        "questions": 22,
        "covenant": "RP + MFN",
        "deal": "Duck Creek",
    },
    "3": {
        "id": "lawyer_acp_mfn",
        "name": "Lawyer Q&A — ACP Tara MFN",
        "questions": 11,
        "covenant": "MFN",
        "deal": "ACP Tara",
    },
    "4": {
        "id": "xtract_dc_di",
        "name": "Xtract Report — Duck Creek DI",
        "questions": 10,
        "covenant": "DI",
        "deal": "Duck Creek",
    },
    "5": {
        "id": "xtract_dc_balanced",
        "name": "Xtract Report — Duck Creek Balanced (RP+MFN+DI)",
        "questions": 15,
        "covenant": "RP + MFN + DI",
        "deal": "Duck Creek",
    },
}


def list_eval_sets() -> str:
    """Return formatted list of available eval sets."""
    lines = [
        "Available Gold Standard Eval Sets:",
        "=" * 50,
    ]
    for key, info in EVAL_SETS.items():
        lines.append(
            f"  [{key}] {info['name']}"
        )
        lines.append(
            f"      {info['questions']} questions | {info['covenant']} | {info['deal']}"
        )
        lines.append("")
    return "\n".join(lines)


def get_eval_set_id(choice: str) -> Optional[str]:
    """Get the eval set ID from user choice (1, 2, 3, or 4)."""
    if choice in EVAL_SETS:
        return EVAL_SETS[choice]["id"]
    # Also accept the full ID
    for info in EVAL_SETS.values():
        if info["id"] == choice:
            return choice
    return None


async def run_eval(eval_set_id: str) -> dict:
    """Run evaluation for the specified gold standard set."""
    from app.routers.graph_eval import run_graph_eval

    result = await run_graph_eval(eval_set_id, request=None)
    return result


# For CLI/interactive use
if __name__ == "__main__":
    print(list_eval_sets())
    choice = input("Select eval set (1/2/3/4/5): ").strip()
    eval_id = get_eval_set_id(choice)
    if eval_id:
        print(f"\nRunning eval: {eval_id}...")
        result = asyncio.run(run_eval(eval_id))
        print(f"\nCompleted in {result['elapsed_seconds']}s")
        print(f"Results: {result['results_files']}")
    else:
        print("Invalid choice")
