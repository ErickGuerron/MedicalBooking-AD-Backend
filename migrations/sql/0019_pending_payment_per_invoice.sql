-- At most one pending checkout attempt per invoice (billing S8). The application reuses the
-- pending attempt; this filtered unique index is the last guard against two concurrent requests.
-- Duplicates that already exist would make the index impossible, so the migration stops with a
-- clear message instead of a generic index error.
IF NOT EXISTS (
  SELECT 1 FROM sys.indexes WHERE name = N'UX_pagos_factura_pendiente' AND object_id = OBJECT_ID(N'dbo.pagos')
)
BEGIN
  IF EXISTS (
    SELECT 1 FROM pagos WHERE estado = 'PENDIENTE' GROUP BY factura_id HAVING COUNT(*) > 1
  )
    THROW 51000, 'Migration 0019: an invoice has more than one pending payment attempt. Resolve them first.', 1;

  CREATE UNIQUE INDEX [UX_pagos_factura_pendiente] ON [pagos] ([factura_id]) WHERE [estado] = 'PENDIENTE';
END
GO
