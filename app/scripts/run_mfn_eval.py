"""
Standalone runner for MFN expert eval.
Does NOT modify any existing application files.

Usage (on Railway):
  railway ssh --service ValenceV3 -- python run_mfn_eval.py

Or deploy and hit the endpoint:
  curl -X POST https://YOUR_URL/api/mfn-eval/b6209000
"""
import asyncio
import json
import sys


async def main():
    # Import after module-level setup
    from app.routers.mfn_eval import run_mfn_eval

    deal_id = sys.argv[1] if len(sys.argv) > 1 else "b6209000"
    skip_extraction = "--skip-extraction" in sys.argv
    force_rebuild = "--force-rebuild" in sys.argv

    print(f"Running MFN expert eval for deal {deal_id}")
    print(f"  skip_extraction={skip_extraction}")
    print(f"  force_rebuild_universe={force_rebuild}")
    print()

    result = await run_mfn_eval(
        deal_id=deal_id,
        force_rebuild_universe=force_rebuild,
        skip_extraction=skip_extraction,
    )
    output = result.model_dump()

    outfile = f"mfn_eval_expert_results.json"
    with open(outfile, "w") as f:
        json.dump(output, f, indent=2)

    print(f"\nDone: {result.num_questions} questions in {result.total_eval_seconds}s")
    print(f"Extraction: {result.extraction_time_seconds}s, {result.extraction_answers_count} answers")
    print(f"Universe: {result.mfn_universe_chars} chars")
    print(f"Saved to {outfile}")

    # Quick summary
    for r in output["results"]:
        print(f"\n--- {r['id']}: {r['short']} ---")
        print(f"  Raw ({r['raw_time_seconds']}s): {r['raw_answer'][:120]}...")
        print(f"  TDB ({r['typedb_time_seconds']}s): {r['typedb_answer'][:120]}...")


if __name__ == "__main__":
    asyncio.run(main())
