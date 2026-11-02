# External knowledge importers

The importers bring terminology and evidence from public sources into the clinical knowledge base
(E5). They only create **drafts** with provenance. A doctor with `conocimiento.validar` decides
what is published. An import never changes published knowledge.

## Sources and their responsibility

| Source | Responsibility | Destination | Credentials |
|---|---|---|---|
| LOINC (FHIR terminology) | Confirms analyte codes: active status and expected name. Terminology only: never ranges or diagnoses | `conocimiento_analitos.codigo_loinc` | `LOINC_USERNAME`, `LOINC_PASSWORD` |
| MedlinePlus Connect | Educational topics in Spanish for each LOINC code | `conocimiento_contenidos` (drafts), `conocimiento_fuentes` (MEDLINEPLUS) | none |
| PubMed (NCBI E-utilities) | Identity of the evidence behind a rule: PMID, title, journal | `conocimiento_fuentes`, `conocimiento_reglas_fuentes` | none |
| ICD-11 (WHO API) | Hypothesis code for a rule, only while the rule is a draft | `conocimiento_reglas.codigo_diagnostico` | `ICD11_CLIENT_ID`, `ICD11_CLIENT_SECRET` |

openFDA is excluded: it is an optional second phase and is not used for clinical decisions.

## Obtaining the credentials

- **LOINC:** create an account at loinc.org and use its username and password.
- **ICD-11:** register at https://icd.who.int/icdapi, log in, and open "View API access key(s)".
  The page shows the Client ID and the Client Secret.

Put the values in `.env` (the file is ignored by git). Never paste them into a chat, a commit or a
log. If a secret is exposed, regenerate it in the provider's portal.

Check an ICD-11 credential without printing it:

```powershell
$body = @{ grant_type='client_credentials'; scope='icdapi_access'; client_id=$env:ICD11_CLIENT_ID; client_secret=$env:ICD11_CLIENT_SECRET }
Invoke-RestMethod -Method Post -Uri https://icdaccessmanagement.who.int/connect/token -Body $body | Select-Object token_type, expires_in
```

## Configuration

`knowledge/sources.json` lists what to import:

- `analytes`: each analyte id, its LOINC code, the name the code must carry (`expected_display`),
  and whether to fetch MedlinePlus topics for it.
- `rules`: each rule code, an optional ICD-11 search term (`icd_search`), and the PubMed PMIDs that
  support the rule (`pubmed_pmids`). The PMID list is empty on purpose: choosing the literature
  that supports a rule is a clinical decision.

## Running it

```powershell
uv run python -m medicalbooking.workers.import_knowledge --dry-run   # fetch and report, write nothing
uv run python -m medicalbooking.workers.import_knowledge             # fetch, then write
```

## Guarantees

1. **Fetch before write.** Every source is consulted for every configured item before the first
   database write. If any source fails, the run stops and the database is untouched.
2. **One transaction for the write.** All statements of a run commit together. A failure halfway
   (for example a rule code that does not exist) rolls back every earlier statement, including the
   analyte codes and the drafts already inserted. Covered by an integration test.
3. **Idempotent.** A second run inserts nothing twice: existing sources, drafts and links are
   skipped, and an analyte that already has a LOINC code keeps it.
4. **Drafts only.** Contents are inserted with `estado = 'BORRADOR'`. An ICD-11 code is set only on
   rules that are still drafts; a published rule is counted as skipped, because published rules
   are immutable.
5. **No secrets in output.** Errors name the source and the failure kind (for example
   `ICD-11: http_401`). Response bodies and credentials are never printed.
6. **Standard library only.** The HTTP clients use `urllib`; the feature adds no runtime
   dependency.

## What the first real run produced

Run on 2026-11-02 against the local development database, after applying migrations to `0006`:

- LOINC: `718-7` (hemoglobin) and `2339-0` (glucose) confirmed as active with the expected names.
- MedlinePlus: 4 educational topics in Spanish, stored as drafts under the source `MEDLINEPLUS`.
- ICD-11: `3A9Z` ("Anemias y otros trastornos de los eritrocitos, sin especificación") proposed for
  `HB-BAJA-SINT`, **not written**: that rule is already validated and published rules are immutable.
- PubMed: nothing written, because no PMIDs are configured.
- A second run reported `loinc_codes_set=0, loinc_codes_kept=2, contents_added=0`, which confirms
  idempotency.

## Remaining work that needs people, not code

1. **ICD-11 for the hemoglobin rule:** a clinician creates a new draft version of `HB-BAJA-SINT`
   with the code, then validates it. The current published version stays as it is.
2. **PubMed evidence:** a clinician chooses the PMIDs that support each rule and adds them to
   `knowledge/sources.json`, then runs the importer again.
3. **Review of MedlinePlus drafts:** a clinician reviews each educational text before publication.
4. **Publication:** only a doctor with `conocimiento.validar` moves drafts to `VALIDADA`.

## Troubleshooting

| Message | Meaning | Action |
|---|---|---|
| `import not started: missing credentials: ...` | A key is empty in `.env` | Fill the named key |
| `import aborted before any write: LOINC code_mismatch` | The code is retired or no longer has the expected name | Review the code in `sources.json`; nothing was written |
| `import aborted before any write: ICD-11: http_401` | Client id or secret is wrong | Regenerate the key in the ICD API portal |
| `import rolled back, nothing kept: ImportFailed` | A rule named in the plan does not exist | Fix the rule code; nothing was written |
