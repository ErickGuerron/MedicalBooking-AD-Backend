-- =====================================================================
-- Synthetic fixture: the glucose reference is a fasting reference.
-- The synthetic glucose reference (70-99 mg/dL) was seeded without a sample context, so a
-- non-fasting sample (GLU-04) was classified against the fasting range. Setting AYUNO makes
-- an unknown context NO_EVALUABLE instead of ALTO. This row is SINTETICO test data, not a
-- clinical reference; it keeps its VALIDADA status only inside the test fixture.
-- Idempotent: updates only while the context is still empty.
-- =====================================================================
UPDATE [conocimiento_referencias]
   SET [contexto] = 'AYUNO'
 WHERE [id] = 'c0000000-0000-4000-8000-00000000d002' AND [contexto] IS NULL;
