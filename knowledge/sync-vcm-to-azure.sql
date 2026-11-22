-- Minimal sync: VCM (MCV) reference, local MedicalBooking -> Azure SQL Medilo.
-- Adds only the source MEDLINEPLUS-CSC-2024 and its VALIDADA reference for analyte 787-2 (fL).
-- Idempotent, additive, one transaction. Nothing else is touched.
-- Run on Azure SQL (Medilo): sqlcmd -b -i sync-vcm-to-azure.sql
-- Effect: the engine can classify MCV (BAJO/DENTRO/ALTO) and the microcytosis signal stops being NO_EVALUABLE.
SET NOCOUNT ON;
SET XACT_ABORT ON;

-- Original validator (medico1@synthetic.test) does not exist in Azure: remapped to this account.
DECLARE @reviewer_email nvarchar(255) = N'medico1@demo.test';
DECLARE @reviewer uniqueidentifier = (SELECT TOP 1 [id] FROM [usuarios] WHERE [email] = @reviewer_email);
IF @reviewer IS NULL THROW 50001, 'Reviewer email not found in Azure usuarios. Edit @reviewer_email.', 1;
IF NOT EXISTS (SELECT 1 FROM [conocimiento_analitos] WHERE [id] = N'A3000000-0000-4000-8000-000000000001')
  THROW 50002, 'MCV analyte (787-2) is missing in Azure.', 1;
IF EXISTS (SELECT 1 FROM [conocimiento_fuentes] WHERE [codigo] = N'MEDLINEPLUS-CSC-2024' AND [id] <> N'F0000000-0000-4000-8000-00000000000B')
  THROW 50003, 'Source codigo already exists in Azure with a different id.', 1;

BEGIN TRANSACTION;

INSERT INTO [conocimiento_fuentes] ([id], [codigo], [nombre], [organizacion], [url], [identificador_publicacion], [version_publicacion], [fecha_publicacion], [fecha_consulta], [licencia], [vigente])
SELECT N'F0000000-0000-4000-8000-00000000000B', N'MEDLINEPLUS-CSC-2024', N'Conteo sanguineo completo (CSC) prueba de sangre', N'U.S. National Library of Medicine, MedlinePlus (version en espanol)', N'https://medlineplus.gov/spanish/ency/article/003642.htm', NULL, N'Revision 2024-10-14', CAST('2024-10-14' AS date), CAST('2026-11-09 00:00:00' AS datetime2), NULL, 1
WHERE NOT EXISTS (SELECT 1 FROM [conocimiento_fuentes] WHERE [id] = N'F0000000-0000-4000-8000-00000000000B');

INSERT INTO [conocimiento_referencias] ([id], [analito_id], [fuente_id], [version], [unidad], [sexo_aplicable], [edad_min_dias], [edad_max_dias], [tipo_muestra], [metodo_analitico], [contexto], [referencia_min], [referencia_max], [valor_texto_esperado], [fecha_inicio], [fecha_fin], [estado], [validado_por], [validado_en])
SELECT N'3C8E0B16-A122-4FE2-A3FF-58974901B5A2', N'A3000000-0000-4000-8000-000000000001', N'F0000000-0000-4000-8000-00000000000B', 1, N'fL', NULL, NULL, NULL, NULL, NULL, NULL, 80.000000, 100.000000, NULL, CAST('2024-10-14' AS date), NULL, N'VALIDADA', @reviewer, CAST('2026-11-10 03:22:32.542418' AS datetime2)
WHERE NOT EXISTS (SELECT 1 FROM [conocimiento_referencias] WHERE [id] = N'3C8E0B16-A122-4FE2-A3FF-58974901B5A2');

-- Validation: must return 0 ERROR rows, otherwise ROLLBACK.
CREATE TABLE #val ([severidad] varchar(8), [chequeo] varchar(40), [detalle] nvarchar(200));
INSERT INTO #val SELECT 'ERROR','RANGO_INVALIDO',CAST([id] AS varchar(40)) FROM [conocimiento_referencias]
 WHERE [id] = N'3C8E0B16-A122-4FE2-A3FF-58974901B5A2' AND ([referencia_min] IS NULL OR [referencia_min] > [referencia_max] OR [unidad] <> 'fL' OR [estado] <> 'VALIDADA');
INSERT INTO #val SELECT 'ERROR','FUENTE_NO_VIGENTE',[codigo] FROM [conocimiento_fuentes] WHERE [id] = N'F0000000-0000-4000-8000-00000000000B' AND [vigente] = 0;
INSERT INTO #val SELECT 'ERROR','VALIDADAS_SOLAPADAS',CONCAT(a.[id],' <-> ',b.[id])
  FROM [conocimiento_referencias] a JOIN [conocimiento_referencias] b ON a.[analito_id]=b.[analito_id] AND a.[id]<b.[id]
 WHERE a.[analito_id] = N'A3000000-0000-4000-8000-000000000001' AND a.[estado]='VALIDADA' AND b.[estado]='VALIDADA'
   AND (a.[sexo_aplicable] IS NULL OR b.[sexo_aplicable] IS NULL OR a.[sexo_aplicable]=b.[sexo_aplicable])
   AND (a.[contexto] IS NULL OR b.[contexto] IS NULL OR a.[contexto]=b.[contexto])
   AND ISNULL(a.[edad_min_dias],0) <= ISNULL(b.[edad_max_dias],2147483647)
   AND ISNULL(b.[edad_min_dias],0) <= ISNULL(a.[edad_max_dias],2147483647)
   AND a.[fecha_inicio] <= ISNULL(b.[fecha_fin],'9999-12-31') AND b.[fecha_inicio] <= ISNULL(a.[fecha_fin],'9999-12-31');
SELECT * FROM #val;
SELECT [referencia_min], [referencia_max], [unidad], [estado], [validado_por] FROM [conocimiento_referencias] WHERE [id] = N'3C8E0B16-A122-4FE2-A3FF-58974901B5A2';

IF EXISTS (SELECT 1 FROM #val WHERE [severidad]='ERROR')
BEGIN ROLLBACK TRANSACTION; PRINT 'ROLLBACK: validation errors. Nothing written.'; END
ELSE
BEGIN COMMIT TRANSACTION; PRINT 'COMMIT: VCM reference synchronized and validated.'; END
DROP TABLE #val;
