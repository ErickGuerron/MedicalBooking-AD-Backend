Objective: when a payment is confirmed, email the patient a personalised invoice (own PDF, built at send time, never stored), and add the missing cita.cancelada design. Branch: feature/payment-invoice-emails (from feature/health-machine-name). Delivery: ask-on-risk. Test runner: uv run pytest tests/unit. Risk: none (notifications and PDF rendering; no schema change, no contract change).

## Specs
- S1: User decision: invoices must be personalised ("quisiera facturas personalizadas"); the PDF is our own, same method as the results PDF (not Stripe invoices).
- S2: Trigger: pago.actualizado with pago.estado = CONFIRMADO (the webhook confirmed it). Other states send nothing.
- S3: Subject and copy in Spanish, neutral and warm; the patient's first name greets them; the invoice number and total appear in the body.
- S4: The PDF lists the invoice number, issue date, patient name, document masked to the last four digits, each detail line (descripcion, cantidad, precio unitario, subtotal), subtotal, impuestos, total with currency, and the payment date.
- S5: The invoice is built from the database at send time and never stored.
- S6: ASSUMPTION: the document number is masked as the lab results PDF does.

## Tasks
- T1 | S4, S5, S6 | inline | done | invoice data read (SqlInvoiceStore, integration test) and invoice PDF builder (build_invoice_pdf). RED observed (collection error), then GREEN (3 unit tests); integration test_invoice_store.py 2 passed.
- T2 | S2, S3 | inline | done | notification: template PAGO_COMPLETADO, handler maps pago.actualizado only for CONFIRMADO (RED observed: 1 failed, then GREEN), contact lookup for pago, payment_content with escaped HTML (RED: import error, then GREEN), paid_invoice on SqlNotificationStore, PDF attachment in the worker.
- T3 | S3 | inline | done | cita.cancelada designed template (cancellation_content with doctor, date and hour in Ecuador time; content_for reuses cita_reminder). RED observed (collection error), then GREEN (3 unit tests). Decision by the user: designed, for patient confidence. Commit c5bc46c.
- L4 (preview): visual previews written outside the repo to C:\Users\erick\Documents\medilo-previews (invoice PDF, both emails) with render_previews.py to regenerate after a redesign.

## Log
- L1: "si" (user confirmed to start with the invoices) after: "quisiera facturas personalizadas", "no olvidar de la parte de los email que faltan por implementar y generacion de factura".
- L2 (evidence): pago.actualizado payload carries pago.estado, pago.monto, pago.moneda, factura.id, factura.paciente_id. Notifications claim_due resolves aggregate_id to the pago id for these events.
- L3 (evidence): PDF primitives (_Document, _Page, _wrap, _clip, _escape) live in laboratory/application/results_report.py; the invoice builder reuses them (follow-up: move them to shared/infrastructure).
