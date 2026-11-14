# Clinical knowledge (E5)

Deterministic clinical knowledge without drugs. It classifies laboratory results against validated
reference ranges, evaluates declarative rules, produces a preliminary proposal per completed order,
and records one human decision on that proposal. The system never states a diagnosis.

The detailed decisions and their verification history are in `odd/tasks/clinical-knowledge-e5.md`.

## Concepts

| Concept | Meaning | Table |
|---|---|---|
| Analyte | A measurable quantity (for example hemoglobin) | `conocimiento_analitos` |
| Test type | A requested test made of analytes | `tipos_prueba_laboratorio` |
| Test-analyte link | Which analytes belong to a test, and whether each is mandatory | `conocimiento_pruebas_analitos` |
| Reference range | A validated interval for an analyte, with its applicability (unit, sex, age, method, sample, context, validity dates) | `conocimiento_referencias` |
| Declarative rule | Criteria over analytes combined with `TODOS` or `CUALQUIERA`, plus a message | `conocimiento_reglas`, `conocimiento_reglas_criterios` |
| Proposal | The preliminary output for one completed order, with its findings and evidence | `propuestas_clinicas`, `propuestas_hallazgos`, `propuestas_hallazgos_evidencias` |
| Medical decision | `ACEPTAR`, `CORREGIR` or `RECHAZAR`, recorded once per proposal | `propuestas_revisiones` (append-only) |

## How a result is classified

When a result is recorded with an analyte, the service looks for validated references for that
analyte, in the same unit, valid today. Each reference is checked against what is known about the
patient and the sample:

- A reference applies when every constraint it sets is known to match.
- If a constraint is unknown (for example the patient's sex is missing and the reference is
  sex-specific), the result is `NO_EVALUABLE`. No category is invented.
- Exactly one applicable reference is required. Zero or several give `NO_EVALUABLE`.
- Boundaries are inclusive: `min <= value <= max` is `DENTRO_REFERENCIA`. An open bound is unbounded.

The reference applied, its version and its limits are stored on the result row at registration
time. Later reads never recalculate the category.

## Overlap rule for validation

Two references overlap when some patient could match both in the same moment. `None` in a field is
a wildcard: a sex-neutral reference overlaps a sex-specific one. Validating a draft that overlaps a
validated reference is refused with 409. The validation locks the analyte row, so two validations
for the same analyte cannot both pass.

## Declarative rules

- Operators: `LT`, `LE`, `GT`, `GE`, `EQ` on numeric values; `FUERA_REFERENCIA` and
  `DENTRO_REFERENCIA` on the stored classification.
- A numeric criterion must carry the unit of its threshold. Without a unit it never runs.
- A rule runs only when validated and within its validity dates.
- `TODOS` fires only when every input is available and satisfied. `CUALQUIERA` fires on at least one
  satisfied input. A missing input never counts as satisfied.
- Each finding records the rule version and the evidence: one row per result, with the observed value
  and unit.
- No code is stored or evaluated from the database. Rules are data.

## Proposals

- Created once per order, and only when every test of the order is complete (see the mandatory
  analyte rule below). A second evaluation returns 409; a database unique index enforces it as well.
- State: `PENDIENTE` when something was classified or a rule fired; `SIN_EVIDENCIA` when neither
  happened.
- The proposal text says it is preliminary and not a diagnosis.

## Medical decision

- Only the ordering doctor may decide, with `revisiones.registrar`.
- One decision per proposal. `CORREGIR` requires the corrected conclusion; `RECHAZAR` requires a
  reason.
- The proposal content (summary, findings, explanations, rule versions, evidence) is never rewritten.
  The decision goes to the append-only history; the review-state columns (state, reviewer, review
  time, conclusion and the findings' review state) are updated, as the DBML defines.

## Completeness rules that protect the proposal

These rules were added after verification, because a proposal built on a partial panel or on a
result filed under the wrong test would be clinically wrong:

- A result must belong to the test it is entered under (`conocimiento_pruebas_analitos`).
  Otherwise the result is refused with 422 and nothing is stored.
- A test completes only when every mandatory analyte (`obligatorio = 1`) has a result. Each analyte is
  reported once per test (409 otherwise). A result without an analyte does not complete a test that
  has mandatory analytes.
- The order completes when all its tests are complete.

## Permissions

| Permission | Who | Allows |
|---|---|---|
| `conocimiento.leer` | doctor, laboratory staff, administrator | Read analytes and validated references |
| `conocimiento.administrar` | administrator | Create analytes and drafts of references and rules |
| `conocimiento.validar` | doctor | Validate references and rules |
| `resultados.leer_con_acceso` | doctor | Evaluate and read the proposal of an own order |
| `revisiones.registrar` | doctor | Record the medical decision on an own proposal |

Denials are audited with their outcome, including a missing permission. Missing and foreign
resources answer the same 403.

## Synthetic seed

Migration `0006` seeds synthetic knowledge only, marked `SINTETICO`: two analytes linked to the
synthetic test types, one validated reference per analyte, two validated rules (`HB-BAJA-SINT`,
`GLU-FUERA-SINT`), a source, and a validator user without credentials. These rows are test fixtures,
not clinical references. Real references come through the drafts-and-validation process described in
`knowledge-import.md` and in the clinical review.

## Endpoints

See the README (section Endpoints) and the OpenAPI document (`docs/api/openapi.json`).

## Known limits

- Knowledge-administration denials are not audited (accepted: the audit requirement covers clinical
  records).
- A numeric criterion without a unit is refused on creation; comparisons across units never happen.
- The deterministic engine covers the operators above only. Complex clinical logic must be modelled and
  tested before it is added.
- Clinical content must be reviewed by a clinician before publication. The system enforces the
  process; it does not replace the review.
