-- =====================================================================
-- Laboratory review of result interpretations (before confirming an order).
--
-- The system classifies each result when it is registered (resultados_laboratorio.interpretacion)
-- and that snapshot is never changed. The laboratory reads every result before confirming: it either
-- confirms the system's reading or sets another one with a reason. Each reading is an append-only row,
-- so the history of who corrected what, and when, is kept. The current reading is the latest row.
--
-- vw_correcciones_interpretacion lists every correction for the knowledge base curators. Curators
-- propose new reference versions through the normal review; nothing changes automatically.
-- =====================================================================

IF OBJECT_ID(N'[resultados_laboratorio_revisiones]', N'U') IS NULL
BEGIN
  CREATE TABLE [resultados_laboratorio_revisiones] (
    [id] uniqueidentifier NOT NULL PRIMARY KEY,
    [resultado_id] uniqueidentifier NOT NULL,
    -- Snapshot of the system's reading at the time of the review (null: never classified).
    [interpretacion_sistema] varchar(25) NULL,
    [interpretacion_final] varchar(25) NOT NULL,
    [corregida] bit NOT NULL,
    [motivo] nvarchar(1000) NULL,
    [revisado_por] uniqueidentifier NOT NULL,
    [revisado_en] datetime2 NOT NULL,
    CONSTRAINT [FK_revisiones_resultado] FOREIGN KEY ([resultado_id]) REFERENCES [resultados_laboratorio] ([id]),
    CONSTRAINT [CK_revisiones_final] CHECK ([interpretacion_final] IN ('BAJO', 'DENTRO_REFERENCIA', 'ALTO', 'NO_EVALUABLE')),
    CONSTRAINT [CK_revisiones_sistema] CHECK ([interpretacion_sistema] IS NULL OR [interpretacion_sistema] IN ('BAJO', 'DENTRO_REFERENCIA', 'ALTO', 'NO_EVALUABLE')),
    CONSTRAINT [CK_revisiones_motivo] CHECK ([corregida] = 0 OR [motivo] IS NOT NULL)
  );
  CREATE INDEX [IX_revisiones_resultado_fecha] ON [resultados_laboratorio_revisiones] ([resultado_id], [revisado_en]);
END
GO

CREATE OR ALTER VIEW [vw_correcciones_interpretacion] AS
SELECT
  v.[id] AS [revision_id],
  r.[id] AS [resultado_id],
  r.[analito_id],
  r.[nombre_analito],
  r.[valor_numerico],
  r.[unidad],
  r.[referencia_aplicada_id],
  r.[referencia_version],
  r.[referencia_min],
  r.[referencia_max],
  v.[interpretacion_sistema],
  v.[interpretacion_final],
  v.[motivo],
  v.[revisado_en]
FROM [resultados_laboratorio_revisiones] v
JOIN [resultados_laboratorio] r ON r.[id] = v.[resultado_id]
WHERE v.[corregida] = 1
GO
