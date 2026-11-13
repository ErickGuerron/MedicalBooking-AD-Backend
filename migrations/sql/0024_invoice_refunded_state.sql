-- Refunded invoices (payments, refunds v1). An invoice whose payment was refunded in full
-- moves from PAGADA to REEMBOLSADA. ANULADA stays a separate flow (not implemented).
-- Drops and recreates the check: existing rows (PENDIENTE, PAGADA, ANULADA) remain valid.
ALTER TABLE [facturas] DROP CONSTRAINT [CK_facturas_estado];
GO
ALTER TABLE [facturas] ADD CONSTRAINT [CK_facturas_estado]
  CHECK ([estado] IN ('PENDIENTE', 'PAGADA', 'ANULADA', 'REEMBOLSADA'));
GO