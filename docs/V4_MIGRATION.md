# V4 Migration — Deontic Refactor

## Fork point

- **Date of fork:** 2026-04-22
- **Frozen v3 backup path:** `C:\Users\olive\valence-backend-v3-frozen\` (sibling of the `ValenceV3` repo root)
- **Git tag of frozen state:** `v3.0-final` (points to commit `7e49c37` on `main`)
- **v4 development branch:** `v4-deontic` (forked from `main` at `v3.0-final`)

## TypeDB databases

| Version | Database name | Location |
| --- | --- | --- |
| v3 (frozen) | `valence_v3` | `C:\Users\olive\valence-backend-v3-frozen\app\config.py` default |
| v4 (active) | `valence_v4` | `app/config.py` default on `v4-deontic` branch |

Both databases live on the same TypeDB Cloud instance (`ip654h-0.cluster.typedb.com:80`).
They do not share data — v3 writes do not affect v4, and vice versa.

## Running the frozen v3 snapshot

The frozen snapshot is configured not to auto-deploy to Railway (deploy block in
`railway.toml` is commented out). To run it locally for comparison testing:

1. `cd C:\Users\olive\valence-backend-v3-frozen` (or whatever relative path leads there from your shell)
2. Copy `.env.example` to `.env` (or reuse the main repo's `.env` with `TYPEDB_DATABASE=valence_v3`)
3. `pip install -r requirements.txt`
4. `uvicorn app.main:app --host 0.0.0.0 --port 8001` (pick a port that does not clash with v4)

The frozen snapshot must not be pushed to the production Railway project.

## Comparing v3 and v4

With v4 running on Railway (production) against `valence_v4` and v3 running locally
against `valence_v3`, the same document can be extracted into both and the two
backends queried independently. Eval gold-standard sets live under
`app/data/gold_standard/*.json` in both trees.
