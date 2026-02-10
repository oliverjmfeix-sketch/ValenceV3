"""
DEPRECATED — Superseded by TQL seed files.

All ontology questions are now seeded via TQL files loaded by init_schema.py:
  - app/data/questions.tql              (RP base questions)
  - app/data/ontology_expanded.tql      (RP expanded questions)
  - app/data/ontology_category_m.tql    (RP Category M)
  - app/data/jcrew_questions_seed.tql   (J.Crew deep analysis)
  - app/data/mfn_ontology_questions.tql (MFN 42 questions)

Run `python -m app.scripts.init_schema` to seed everything.

This file is kept for reference only. Do not run it.
"""
