# AG Crypto Analyst — Session Handoff 2026-08-17

## Remote Runtime Store

v1.2.1 adds an optional SQLite-first PostgreSQL/Supabase mirror. Existing
runtime modules now use `database.runtime_store.connect`, which is compatible
with their current SQLite calls. With no remote URL the system remains
SQLite-only. When configured, a successful local commit is mirrored; remote
failure is warning-only and enters a short retry cooldown.

## Next verification

Install `requirements-runtime.txt`, supply `AGCA_REMOTE_DATABASE_URL` outside
the repository, run one staging cycle, and compare representative SQLite and
`agca_runtime` table counts. Do not place credentials in Git or promote remote
reads into product decisions as part of that smoke test.
