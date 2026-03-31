---
description: Run gold standard eval against the live Railway backend
user-invocable: true
---

# /eval — Run Gold Standard Evaluation

The user wants to run an eval. Present the available eval sets and let them pick, then run it.

## Available Eval Sets

| # | ID | Description | Questions |
|---|---|---|---|
| 1 | `lawyer_dc_rp` | Lawyer Q&A — Duck Creek RP | 6 |
| 2 | `xtract_dc_rp_mfn` | Xtract Report — Duck Creek RP+MFN | 22 |
| 3 | `lawyer_acp_mfn` | Lawyer Q&A — ACP Tara MFN | 11 |

## Instructions

1. **ALWAYS start by showing the user which eval sets are available.** Call the Railway backend to get the live list:
   ```
   curl -s https://valencev3-production.up.railway.app/api/eval-sets
   ```
   Display the results as a numbered list showing: name, covenant type, source, question count.

2. Then use AskUserQuestion to ask which eval set to run. Options should be built from the API response (one per eval set, plus "All (sequential)"). If the user provided an argument (e.g. `/eval 1`, `/eval lawyer_dc_rp`), skip the question and map directly.

3. Run the eval by calling the Railway backend:
   ```
   curl -s -X POST https://valencev3-production.up.railway.app/api/graph-eval/{eval_set_id} | python -m json.tool
   ```

4. Parse the JSON response. Show a clean summary table:
   - For each question: status (OK/FAIL based on whether graph_answer is non-empty and not an error), question_id, first 60 chars of question, cost
   - Total: questions passed, total cost, elapsed time, execution mode

5. **Always report result file paths.** The JSON response includes `results_files` with `railway` and `local` paths:
   - Show the Railway paths (e.g. `/app/uploads/eval_results/eval_{id}_{timestamp}_summary.txt`)
   - Remind the user: Railway filesystem is ephemeral (wiped on next deploy). To download results:
     ```
     curl -s https://valencev3-production.up.railway.app/api/eval-results/{eval_set_id}
     ```
   - If results should be preserved, download the `_full.json` and `_summary.txt` files before next deploy.

6. If any questions failed, offer to show the full trace for those questions.

## Argument Mapping

- `1` or `lawyer_dc_rp` → Duck Creek RP (6 questions)
- `2` or `xtract_dc_rp_mfn` → Duck Creek RP+MFN (22 questions)
- `3` or `lawyer_acp_mfn` → ACP Tara MFN (11 questions)
- `all` → run all three sequentially
