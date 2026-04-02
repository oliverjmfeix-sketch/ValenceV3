---
description: Run gold standard eval against the live Railway backend
user-invocable: true
---

# /eval — Run Gold Standard Evaluation

The user wants to run an eval. Present the available eval sets, let them pick a deal to run against, then run it.

## Available Eval Sets

| # | ID | Description | Questions |
|---|---|---|---|
| 1 | `lawyer_dc_rp` | Lawyer Q&A — Duck Creek RP | 6 |
| 2 | `xtract_dc_rp_mfn` | Xtract Report — Duck Creek RP+MFN | 22 |
| 3 | `lawyer_acp_mfn` | Lawyer Q&A — ACP Tara MFN | 11 |
| 4 | `xtract_dc_di` | Xtract Report — Duck Creek DI | 10 |
| 5 | `xtract_dc_balanced` | Xtract Report — Duck Creek Balanced (RP+MFN+DI) | 15 |

## Instructions

1. **Fetch available deals from the backend:**
   ```
   curl -s https://valencev3-production.up.railway.app/api/deals
   ```
   This returns the list of deals currently in TypeDB with their deal_id and deal_name.

2. **Show eval sets and ask which to run.** Use AskUserQuestion with one option per eval set, plus "All (sequential)". If the user provided an argument (e.g. `/eval 4`, `/eval xtract_dc_di`), skip the question and map directly.

3. **Ask which deal to run against.** Use AskUserQuestion showing all available deals from step 1. The gold standard file has a default `resolve_deal_id`, but the user may want to run the same questions against a different deal. Show the default deal as "(Recommended)". If only one deal exists, skip the question and use it.

4. **Ask whether to include scalar comparison.** Use AskUserQuestion with two options:
   - "Graph only (Recommended)" — faster, half the cost, passes `skip_scalar: true`
   - "Graph + Scalar" — runs both pipelines for side-by-side comparison

   Default to graph-only since scalar is rarely needed.

5. **Run the eval** by calling the Railway backend. Always pass a JSON body with `skip_scalar` (and `override_deal_id` if the user picked a different deal):
   ```bash
   # Graph only, default deal
   curl -s -X POST https://valencev3-production.up.railway.app/api/graph-eval/{eval_set_id} \
     -H "Content-Type: application/json" \
     -d '{"skip_scalar": true}' \
     --max-time 600

   # Graph + scalar, override deal
   curl -s -X POST https://valencev3-production.up.railway.app/api/graph-eval/{eval_set_id} \
     -H "Content-Type: application/json" \
     -d '{"skip_scalar": false, "override_deal_id": "{deal_id}"}' \
     --max-time 600
   ```
   **IMPORTANT:** Evals can take 5-10 minutes. Use `--max-time 600` and run in the background.

6. Parse the JSON response. Show a clean summary table:
   - For each question: status (OK/FAIL based on whether graph_answer is non-empty and not an error), question_id, first 60 chars of question, cost
   - Total: questions passed, total cost, elapsed time, deal_id used

7. **Always report result file paths.** The JSON response includes `results_files` with `railway` and `local` paths:
   - Show the Railway paths (e.g. `/app/uploads/eval_results/eval_{id}_{timestamp}_summary.txt`)
   - Remind the user: Railway filesystem is ephemeral (wiped on next deploy). To download results:
     ```
     curl -s https://valencev3-production.up.railway.app/api/eval-results/{eval_set_id}
     ```
   - If results should be preserved, download the `_full.json` and `_summary.txt` files before next deploy.

8. If any questions failed, offer to show the full trace for those questions.

## Argument Mapping

- `1` or `lawyer_dc_rp` → Duck Creek RP (6 questions)
- `2` or `xtract_dc_rp_mfn` → Duck Creek RP+MFN (22 questions)
- `3` or `lawyer_acp_mfn` → ACP Tara MFN (11 questions)
- `4` or `xtract_dc_di` → Duck Creek DI (10 questions)
- `5` or `xtract_dc_balanced` → Duck Creek Balanced RP+MFN+DI (15 questions)
- `all` → run all five sequentially
