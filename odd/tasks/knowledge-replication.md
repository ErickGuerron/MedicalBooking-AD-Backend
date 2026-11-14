Objective: make the clinical knowledge base (reference ranges, rules, evidence, population validation) reproducible across the team, so every developer's local database holds the same knowledge and the same diagnostic output. Knowledge lives in versioned files in the repository, is applied by a deterministic loader, and is verified by a digest.
Branch: feature/knowledge-importers (worktree C:\Users\erick\AppData\Local\Temp\mb_kb_wt). Delivery: ask-on-risk. Test runner: uv run pytest (integration needs SQL Server localhost,1433). Risk: pending closure (migration 0017 is additive; no published row changes).

## Specs
- S1: The source of truth for knowledge is versioned files under knowledge/bundle/ (CSV or JSON, UTF-8, LF), never one developer's database. A manifest knowledge/bundle/manifest.json lists every file with its SHA-256 and a bundle version (semver string). The manifest digest is the bundle digest.
- S2: A deterministic loader applies a bundle to any migrated database. It is idempotent: applying the same bundle twice inserts nothing new. It runs in one transaction: all of it or nothing.
- S3: The loader refuses to apply when a file hash differs from the manifest, when a referenced analyte, source or rule code is unknown, or when a row would change a published (VALIDADA) row. Errors name the file and line; nothing is written.
- S4: Published references and rules stay immutable (spec 5.4). A bundle change that alters a published row is a new version row, never an update.
- S5: Every application is recorded in an append-only table knowledge_bundle_aplicaciones (bundle version, digest, applied_at, applied_by, outcome). Applying an identical digest again records nothing new.
- S6: A verify command reads the database and reports the digest of the knowledge it holds, computed from the same canonical export as the bundle. Output shows bundle version, digest, counts per table, and OK or DRIFT. It writes nothing.
- S7: Documents and evidence are first-class data: conocimiento_documentos (title, DOI or PMID, year, country, SHA-256 of the file when local, use: CONTEXTO, RANGO, REGLA, VALIDACION_EXTERNA or DESCARTADO, and the reason). conocimiento_evidencias links a document to a reference or rule with a locator (table, page, recommendation) and a short citation of at most 25 words. No full copyrighted text is stored.
- S8: No absolute local paths, usernames, credentials or personal data appear in the bundle, the manifest, the docs or the database. Credentials stay in .env (already ignored).
- S9: Population validation results (CHMS percentiles, NHANES comparisons, sensitivity and specificity) are reproducible: each has a script under scripts/validation/ that downloads the public input by URL, records its SHA-256, and writes the result CSV under knowledge/validation/. A teammate re-runs the script and must obtain the same numbers.
- S10: Each proposal records the bundle version and digest in effect when it was evaluated, next to the existing rule version and reference id and version snapshots (additive nullable column; old rows keep NULL).
- S11: ASSUMPTION (pending user decision D1): when the database digest differs from the digest the application was built with, evaluation either refuses (409) or proceeds and records the mismatch. Default in this plan: refuse with 409 and a clear message, because two developers must not get different findings from the same order. Blocked until the user chooses.
- S12: docs/knowledge-replication.md is the runbook a teammate follows from a clean clone: install, migrate, apply bundle, verify, expected output. It names the exact command and the expected digest line.
- S13: The seeded synthetic rows of migration 0006 are kept and labelled SINTETICO; the bundle marks synthetic content with a flag so production and test knowledge never mix.
- S14: Tests: dry-run writes nothing; apply is idempotent; a hash mismatch is refused; a failure mid-apply rolls back; verify reports DRIFT after a manual change; two databases loaded from the same bundle report the same digest. RED observed before each behaviour.
- S15: Existing command and option behaviour stays unchanged, including the importers in docs/knowledge-import.md; a differential check runs before finishing.
- S16: Clinical thresholds still living in code (domain/iron.py, domain/anemia.py, domain/hb_adjustments.py) stay authoritative in this feature. A test asserts that each threshold in code equals its row in the bundle. Moving thresholds into the bundle is a later feature (0019).

## Tasks
- T1 | S1, S8 | inline | pending | bundle layout knowledge/bundle/ and manifest with SHA-256 and version; export the current local validated knowledge (analytes, references, rules, sources) into it. Commit: pending
- T2 | S7, S10, S5 | pending | migration 0017: conocimiento_documentos, conocimiento_evidencias, knowledge_bundle_aplicaciones (append-only), nullable version columns on propuestas_clinicas. RED: migration test for constraints. Commit: pending
- T3 | S2, S3, S4, S14 | pending | loader: parse, validate, dry-run, one transaction, idempotent, published immutability. RED first. Commit: pending
- T4 | S6, S14 | pending | verify command and canonical digest; drift test. Commit: pending
- T5 | S9 | partial (NHANES anaemia vs ferritin done: scripts/validation/nhanes_anemia_regla_combinada.py, protocolo_muestreo.json with URLs and SHA-256, output knowledge/validation/nhanes_anemia_regla_combinada.csv; CHMS, roc.py, tsat.py and the diabetes evaluation still live outside the repository) | scripts/validation/ for CHMS and NHANES with input URLs and SHA-256 recorded; knowledge/validation/ CSV outputs; re-run reproduces numbers. Commit: pending
- T6 | S11 | blocked on D1 | evaluation check against the bundle digest; RED first. Commit: pending
- T7 | S12, S13 | pending | docs/knowledge-replication.md runbook and synthetic flag. Commit: pending
- T8 | S15, S16 | pending | threshold-equals-bundle test; differential check of knowledge-import and clinical commands; full suite. Commit: pending

Route: all inline for the first pass. Writer delegation only if T5 and T3 run in parallel with disjoint surfaces.

## Log
- L1 (user, verbatim): "si y ten en cuenta que todo ello tine que ser documentado y replicable para los demas miembros del equipo, osea que la base de conocimeintos actual mia en local sea replicable apra los demas y no se produscan incosistencias en los diagnosticos de cada uno"
- L2 (evidence): the repository already has an importer (knowledge/sources.json, docs/knowledge-import.md, workers/import_knowledge.py) and a CSV reader for reference ranges (knowledge/references.csv, workers/import_references.py). The CSV currently holds one row. Local validated content is only in the local database, so it is not reproducible yet.
- L3 (evidence): migration 0016_shadow_edits.sql is the latest; the next migration is 0017.
- L4 (evidence): the clinical constants live in code (iron.py: WHO 2020 table and AGA 45 ug/L; snapshot.py: RULE_VERSION sombra-hallazgos-2). They are reproducible through git but not as data (S16).
- L5 (decision needed): D1, digest mismatch behaviour (S11): refuse with 409 (recommended) or warn and record.
- L6 (evidence): first S9 script added. It loads domain/anemia.py and domain/hb_adjustments.py by path, so the numbers follow the code. It stops when an input hash differs from the protocol. Not committed.
