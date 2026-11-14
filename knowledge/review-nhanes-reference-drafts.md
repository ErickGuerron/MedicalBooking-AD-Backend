# Clinical review: NHANES reference drafts (glucose and hemoglobin)

Status: **draft for clinician review. Nothing here is published.** The seven references are
stored as `BORRADOR` in `conocimiento_referencias`. They do not apply to patient results until a
doctor with `conocimiento.validar` publishes them.

This document is for the reviewing clinician. It lists what was computed, how, and the decisions
that only a clinician can take.

## 1. What is being reviewed

| Id | Analyte | Sex | Age (days) | Sample | Context | Range | Unit |
|----|---------|-----|------------|--------|---------|-------|------|
| c1000000-0000-4000-8000-000000000001 | Glucose in fasting plasma | both | 6570 to open | plasma | AYUNO | 82.0 to 123.0 | mg/dL |
| c1000000-0000-4000-8000-000000000004 | Hemoglobin, whole blood | both | 365 to 2189 (1 to 5 y) | whole blood | none | 10.6 to 14.1 | g/dL |
| c1000000-0000-4000-8000-000000000005 | Hemoglobin, whole blood | both | 2190 to 4379 (6 to 11 y) | whole blood | none | 11.2 to 14.7 | g/dL |
| c1000000-0000-4000-8000-000000000006 | Hemoglobin, whole blood | M | 4380 to 6569 (12 to 17 y) | whole blood | none | 12.2 to 16.6 | g/dL |
| c1000000-0000-4000-8000-000000000007 | Hemoglobin, whole blood | F | 4380 to 6569 (12 to 17 y) | whole blood | none | 9.7 to 14.7 | g/dL |
| c1000000-0000-4000-8000-000000000002 | Hemoglobin, whole blood | M | 6570 to open (adults) | whole blood | none | 12.0 to 17.0 | g/dL |
| c1000000-0000-4000-8000-000000000003 | Hemoglobin, whole blood | F | 6570 to open (adults) | whole blood | none | 10.3 to 15.2 | g/dL |

Analytes: `a1000000-0000-4000-8000-000000000001` (glucose) and `a1000000-0000-4000-8000-000000000002`
(hemoglobin). Both have no LOINC code yet.

## 2. Source and method

- **Source**: NHANES, United States National Center for Health Statistics, cycle 08/2021 to 08/2023
  (cycle L). Public files: `GLU_L`, `GHB_L`, `CBC_L`, `DIQ_L`, `DEMO_L`, from
  `https://wwwn.cdc.gov/Nchs/Data/Nhanes/Public/2021/DataFiles/`.
- **Glucose**: fasting subsample (`LBXGLU`, weight `WTSAF2YR`), adults 18 years and older.
  Excluded: diagnosed diabetes (`DIQ010` = 1) and HbA1c `LBXGH` at or above 6.5 %.
  3329 adults had a fasting value, 546 were excluded, 2783 remain.
- **Hemoglobin**: `LBXHGB` with the examination weight `WTMEC2YR`. No exclusions. Adults by sex;
  children by age band; sex-specific only from 12 years.
- **Estimator**: weighted 2.5th and 97.5th percentiles (interpolated on the cumulative weight).
- **Sample sizes** per stratum (unweighted): glucose 2783; hemoglobin adult male 2703, adult female
  3301, age 1 to 5 years 308 (male 159, female 149), age 6 to 11 years 564, age 12 to 17 years 717
  (male 351, female 366). All strata are at or above 120, the minimum commonly cited for reference
  intervals (CLSI C28).

## 3. Cross-check with the previous cycle (08/2017 to 03/2018, cycle J)

Same method, same exclusions. Values differ by 1 to 2 units. No range was changed because of it.

| Stratum | Cycle L (2021-2023) | Cycle J (2017-2018) |
|---|---|---|
| Glucose, healthy adults (mg/dL) | 82.0 to 123.0 | 84.0 to 125.0 |
| Hemoglobin adult male (g/dL) | 12.0 to 17.0 | 12.5 to 17.1 |
| Hemoglobin adult female (g/dL) | 10.3 to 15.2 | 10.6 to 15.5 |
| Hemoglobin 1 to 5 y (g/dL) | 10.6 to 14.1 | 10.8 to 13.9 |
| Hemoglobin 6 to 11 y (g/dL) | 11.2 to 14.7 | 11.4 to 14.7 |
| Hemoglobin 12 to 17 y male (g/dL) | 12.2 to 16.6 | 12.2 to 16.8 |
| Hemoglobin 12 to 17 y female (g/dL) | 9.7 to 14.7 | 10.4 to 14.8 |

## 4. Decisions only the clinician can take

1. **Glucose exclusion rule.** Is excluding diagnosed diabetes and HbA1c at or above 6.5 % the right
   population? In cycle J, the same adults without the exclusions had an upper limit of about
   200 mg/dL (cycle L was not computed without exclusions). The current upper limit of 123 mg/dL is
   close to the prediabetes threshold, so it may be too high for a "normal" range. Please decide
   whether to keep it, lower it, or use a different reference population.
2. **Fasting definition.** Confirm that the NHANES fasting subsample is acceptable for an "AYUNO"
   context. Check the exact fasting criterion in the cycle's Laboratory Methods document before
   relying on it; it was not checked in this review.
3. **Pediatric hemoglobin (case HB-09).** A 6-year-old with hemoglobin 11.0 g/dL falls in the 6 to 11
   band, whose lower limit is 11.2, so it is flagged as low. Decide whether the age bands should be
   split more finely, or whether a single pediatric band is clinically safer.
4. **Sex-specific adult glucose.** Male and female limits differ by only 2 to 4 mg/dL. The current
   draft uses one range for both sexes. Confirm.
5. **Adult age boundary.** The draft starts adult bands at 18 years (6570 days). Confirm whether the
   adult range should start at a different age, and how to treat pregnancy and older adults.
6. **Analyte codes.** Assign LOINC codes to the real analytes, or confirm they remain without code.
   The synthetic analytes currently hold `2339-0` and `718-7`, which are unique in the database.

## 5. Limitations

- Population percentiles of one US sample. They are not diagnostic thresholds.
- Laboratory methods differ between countries and between laboratories. Each laboratory must check
  these ranges against its own assay before using them.
- The age bands are coarse and the percentiles are not smoothed. A finer model may change the limits.
- Only two NHANES cycles were used. The 2025 to 2027 cycle is not published yet.

## 6. How the clinician validates a draft

Through the API, with a user holding `conocimiento.validar` (a doctor):

```
POST /api/v1/knowledge/references/{id}/validate
```

A validation that overlaps a validated range returns 409. Once validated, a reference is immutable.
Change a range by creating a new version, never by editing a validated row.

## 7. Reproduction

The values come from the public files above. The SQL that loads the drafts is
`knowledge/candidate-sources.sql`, and it is idempotent. Source provenance is in
`conocimiento_fuentes` (codigo `NHANES`).
