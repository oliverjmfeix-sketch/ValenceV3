# Cowork Setup for Valence Ontology Authoring

**Audience:** Covenant expert joining the Valence team. You will not need to
open a terminal.

**Estimated time:** 30 minutes, one-time.

---

## 1. Install Claude Cowork

1. Open <https://claude.com/download> and install the Claude desktop app for
   your OS (macOS or Windows).
2. Install the Claude mobile app on your phone and sign in with the same
   account. This is required by Cowork for device pairing.
3. On first launch of the desktop app, you'll see three tabs at the top:
   **Chat**, **Cowork**, **Code**. Click **Cowork**.
4. Follow the pairing flow. Your desktop must stay awake while Cowork is
   running; sleep kills the session.

You need a paid Claude plan (Pro, Max, Team, or Enterprise). For daily
ontology work, Max tier is recommended — Pro's limits will cut short any
session that touches more than a handful of `.tql` files.

**Do not enable Cowork for HIPAA, FedRAMP, or FSI-regulated workloads.**
Valence's *source code and own sample data* don't fall under that — you're safe
to use it for everything in `app/data/`. But you must not drop a regulated
customer's production credit agreements into your Cowork folder without
clearing it with Valence's security lead first.

---

## 2. Get the Valence repo onto your desktop

Ask engineering to run this on your machine once (or to walk you through
it — it's a two-click action in GitHub Desktop):

```
git clone https://github.com/<org>/valence-backend.git ~/Work/valence-backend
```

You should end up with a folder at `~/Work/valence-backend/` (or
`C:\Users\<you>\Work\valence-backend\` on Windows) containing the
`app/`, `docs/`, `README.md` you see in the kit.

You will **never** need to type `git` yourself. Cowork will handle commits and
PRs through the GitHub connector (set up in step 5 below).

---

## 3. Create a Cowork Project

Cowork Projects scope one area of work: files, memory, skills, and
instructions, all isolated from other projects.

1. In the desktop app, Cowork tab, click **New Project**.
2. Name it: `Valence Ontology`.
3. **Allowed folder:** `~/Work/valence-backend/` (accept the permission prompt
   when it appears).
4. **Project instructions** (paste this into the instructions box):

```
You are helping me expand the Valence covenant-intelligence ontology.

The only files I edit are:
  - app/data/*_ontology_questions.tql   (categories + questions + linkage)
  - app/data/_TEMPLATE_new_covenant.tql (reference only, do not modify)

Before suggesting any edit, read the relevant existing file (e.g. for a new
Restricted Payments question, read app/data/questions.tql first; for a new
Debt Incurrence question, read app/data/di_ontology_questions.tql first).

Follow the patterns in docs/cowork/skills/valence-ontology/SKILL.md exactly.
Do NOT invent new TypeDB entity types or attributes — if a question needs a
new entity_list type, flag it as TODO-ENG and I will pass it to engineering.

Always run `python app/scripts/validate_ontology.py app/data/<file>.tql`
after every edit and report the result. If validation fails, fix it before
reporting complete.

When I ask you to draft questions from an Xtract report or a credit agreement,
produce 5-10 questions at a time, let me review, then continue. Do not dump
50 questions in one response.

I will never ask you to push to main. For any commit, open a branch named
`ontology/<module>-<short-desc>` and open a draft PR. Engineering will merge.
```

5. **Import the skill.** Click **Skills** in the project sidebar →
   **Add skill** → **From folder** → select
   `~/Work/valence-backend/docs/cowork/skills/valence-ontology/`.

   This teaches Cowork the Valence ontology patterns. You only do this once.

---

## 4. Connect GitHub (so you can open PRs from Cowork)

1. In Cowork settings → **Connectors** → **GitHub**.
2. Sign in with the account that has access to the Valence repo.
3. Grant access to **only** the `valence-backend` repo.
4. Back in your project instructions, confirm the line
   `I will never ask you to push to main` is there.

Cowork will now be able to: create branches, commit files, open draft PRs,
comment on PRs. It will still ask you before each of those actions.

---

## 5. Optional: Connect Google Drive (for Xtract reports)

Xtract Research reports usually arrive as PDFs in a Drive folder. If engineering
has granted you access:

1. Cowork settings → **Connectors** → **Google Drive**.
2. Restrict access to the `Valence / Xtract Reports` shared folder only.

Cowork can then read an Xtract report directly and help you diff it against
the current ontology to find gaps.

---

## 6. Your first session — smoke test

Open the Cowork Project. Paste this prompt:

> Read app/data/di_ontology_questions.tql and app/data/_TEMPLATE_new_covenant.tql.
> Then summarize the pattern I should follow when I add new questions, in your
> own words, in 6 bullet points.

If the response is sensible and references category inserts, question inserts,
`extraction_prompt`, and `category_has_question` relations, you're ready. If
not, flag it — likely the skill didn't load.

Next, try:

> Run `python app/scripts/validate_ontology.py --all` and tell me the current
> state of the ontology.

You should get back a clean summary of categories, questions per module, and
any existing issues.

---

## What Cowork will *not* do for you

- Modify `app/data/schema_unified.tql` (engineering only)
- Modify any `.py` file under `app/` (engineering only)
- Push directly to `main` (draft PRs only)
- Run the Valence extraction pipeline against real customer PDFs
- Deploy to Railway

If you find yourself wanting any of those, stop and Slack engineering. The
whole point of this setup is that you stay in the ontology layer and they
stay in the plumbing layer.

---

## Troubleshooting

**Cowork says it can't find a file.** You probably typed a path relative to
`app/data/` — give it the full path from the project root, e.g.
`app/data/liens_ontology_questions.tql`.

**The validator complains about a category_id that "doesn't exist".** You
added a question to a category before inserting the category row. Move the
`$cat_X isa ontology_category, ...` insert above the `category_has_question`
inserts.

**Claude suggests defining a new entity type.** Stop it. Say:
> Flag this as TODO-ENG instead of inventing a new entity type. Draft the
> question as a scalar (boolean/integer/string) for now.

**My edits collide with another branch's display_order.** The validator
catches this. If it happens, just renumber — `display_order` is for UI sort
only, not a stable ID.
