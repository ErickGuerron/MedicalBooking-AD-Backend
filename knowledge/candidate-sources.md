# Candidate evidence sources (pending clinical validation)

Status: **candidate**. No value below is validated, and no reference range is
recorded here. Values enter `references.csv` only with a PMID or an official
guideline citation and clinician sign-off through `conocimiento.validar`.

This file does not change the `sources.json` schema consumed by the importers.

## Sources

| Id | Source | Role | Provides | Access | Limits | Status |
|----|--------|------|----------|--------|--------|--------|
| S-NHANES | CDC/NCHS, National Health and Nutrition Examination Survey (US) | Primary for population reference intervals | Lab results by survey cycle with age, sex, and a fasting subsample (needed for glucose) | Public datasets | US population; assays and units change between cycles; match methods before use | To verify: variable names and fasting weights per cycle |
| S-MIMIC-IV | PhysioNet, MIMIC-IV (BIDMC) | Integration and rule testing only | Hospital lab results with the laboratory's own reference ranges (`labevents`, to confirm in the data dictionary) | Credentialed access: CITI training and data use agreement | ED and ICU patients, not a healthy population; not a source for normal ranges | To verify: reference range columns |
| S-PUBMED | NCBI PubMed (E-utilities) | Traceability layer | PMIDs and abstracts that support a value | Free API | Gives citations, not values | Ready for use |
| S-ADA | American Diabetes Association, Standards of Care in Diabetes | Diagnostic thresholds (glucose, HbA1c) | Guideline criteria | Published document, no API; load manually with DOI or PMID | Thresholds are not population ranges | To verify: current edition and the exact table |
| S-MSD | Merck Manual Professional, Laboratory Reference Ranges | Secondary cross-check only | Points to ABIM for the full list; drug level tables | Public web | Does not give the analytes we need; no PMID | Not a primary source |
| S-ABIM | American Board of Internal Medicine, Laboratory Test Reference Ranges (Jan 2026 PDF) | Secondary verification | Conventional-unit ranges | Public PDF (abim.org) | Per-value citations unknown; PDF text not yet extracted | To verify: glucose and hemoglobin rows |
| S-FHIR | HL7 FHIR R4, Observation.referenceRange | Import format, not a source | Data shape: low, high, appliesTo, age, text | Public specification | Values come from the sending laboratory | Use as import design reference |

## Excluded

- VOLO Health (VOLO Modeled Range): proprietary model, explicitly "not clinical
  or laboratory reference ranges"; no citable derivation.
- LabVals and unofficial copies of the ABIM PDF (Scribd, studylib): not official.

## Analyte to source plan

| Analyte / need | Candidate primary | Secondary | Clinician question |
|----------------|-------------------|-----------|--------------------|
| Glucose, fasting (GLU-04) | S-NHANES fasting subsample | S-ABIM | Fasting condition and cut-offs |
| Hemoglobin, pediatric (HB-09) | S-NHANES (age bands) | S-ABIM | Age bands and sex |
| Hemoglobin by sex | S-NHANES | S-ABIM | Reference population |
| Cholesterol | S-ADA / lipid guideline (thresholds) | S-NHANES (distribution) | Guideline or distribution |

## Next steps

1. Verify NHANES variables and fasting weights for the latest cycle that has glucose and hemoglobin.
2. Extract the ABIM glucose and hemoglobin rows (PDF text).
3. Confirm the MIMIC-IV reference range columns in the data dictionary.
4. For each candidate value: record PMID or guideline citation, unit, age and sex bands, and sample condition, then send it for clinician validation.
