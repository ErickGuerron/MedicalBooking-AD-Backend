"""Send due patient notifications through Brevo, with backoff and a bounded attempt count.

Run once per schedule, for example every minute:

    uv run python -m medicalbooking.workers.send_notifications

Required settings: BREVO_API_KEY and MAIL_FROM (MAIL_FROM_NAME is optional). Each run claims a
batch of due rows under a lease, so several processes can run at once without double sends.

Exit code 0 means the run finished (individual failed sends are retried or marked FALLIDA and
counted); 1 means the run failed or the configuration is incomplete. Details are never printed:
the provider or database error may carry the api key or connection data. Only counts are printed.
"""

from __future__ import annotations

import hashlib
import logging
import sys
from datetime import UTC, datetime
from pathlib import Path

from medicalbooking.config import Settings
from medicalbooking.modules.laboratory.application.order_report import render_order_report
from medicalbooking.modules.laboratory.infrastructure.repositories import SqlLabStore
from medicalbooking.modules.laboratory.infrastructure.storage import LocalDocumentStorage
from medicalbooking.modules.notifications.application.dispatcher import NotificationDispatcher
from medicalbooking.modules.notifications.application.ports import (
    DueNotification,
    EmailAttachment,
    NotificationError,
)
from medicalbooking.modules.notifications.domain.templates import (
    PAGO_COMPLETADO,
    RESULTADO_LABORATORIO,
)
from medicalbooking.modules.notifications.infrastructure.brevo import BrevoNotificationSender
from medicalbooking.modules.notifications.infrastructure.email_render import content_for
from medicalbooking.modules.notifications.infrastructure.repositories import SqlNotificationStore
from medicalbooking.modules.payments.application.invoice_report import build_invoice_pdf
from medicalbooking.modules.payments.infrastructure.invoice_store import SqlInvoiceStore
from medicalbooking.shared.infrastructure.database import create_database_engine

logger = logging.getLogger(__name__)

BATCH_SIZE = 50


def missing_configuration(settings: Settings) -> list[str]:
    """Names of the required settings that are not set. Values are never returned."""
    missing = []
    if settings.brevo_api_key is None:
        missing.append("BREVO_API_KEY")
    if not settings.mail_from:
        missing.append("MAIL_FROM")
    return missing


def run(dispatcher) -> int:
    try:
        report = dispatcher.send_due(BATCH_SIZE)
    except Exception as error:
        # Log the error type only: the message may carry the api key or connection data.
        logger.error("notification run failed: %s", type(error).__name__)
        return 1
    print(f"notifications attempted={report.attempted} sent={report.sent} failed={report.failed}")
    return 0


def main() -> int:
    if any(argument in ("-h", "--help") for argument in sys.argv[1:]):
        print(__doc__)
        return 0
    try:
        settings = Settings()
        engine = create_database_engine(settings)
    except Exception as error:
        # Settings errors can echo the offending value; report the type only.
        logger.error("worker configuration failed: %s", type(error).__name__)
        return 1
    try:
        missing = missing_configuration(settings)
        if missing:
            logger.error("worker configuration incomplete: missing %s", ", ".join(missing))
            return 1
        lab_store = SqlLabStore(engine)
        documents = LocalDocumentStorage(Path(settings.lab_documents_dir))

        invoices = SqlInvoiceStore(engine)

        def attachments_for(due: DueNotification) -> tuple[EmailAttachment, ...]:
            if due.tipo == PAGO_COMPLETADO and due.aggregate_id is not None:
                # The invoice is built now from the paid payment and never stored.
                invoice = invoices.paid_invoice_for_payment(due.aggregate_id)
                if invoice is None:
                    raise NotificationError("INVOICE_NOT_FOUND")
                name = f"factura-{invoice.numero_factura}.pdf"
                pdf = build_invoice_pdf(invoice)
                return (EmailAttachment(name=name, content_type="application/pdf", data=pdf),)
            # The results go out as the order's PDF, built now from the completed order.
            if due.tipo != RESULTADO_LABORATORIO or due.aggregate_id is None:
                return ()
            pdf = render_order_report(lab_store, due.aggregate_id, datetime.now(UTC))
            if pdf is None:
                raise NotificationError("ORDER_NOT_FOUND")
            name = f"resultados-{due.aggregate_id[:8]}.pdf"
            attachments = [EmailAttachment(name=name, content_type="application/pdf", data=pdf)]
            # The laboratory's own report goes with the results, when it was uploaded.
            for document in lab_store.list_order_documents(due.aggregate_id):
                content = documents.read(document.ruta_storage)
                if hashlib.sha256(content).hexdigest() != document.hash_sha256:
                    raise NotificationError("REPORT_INTEGRITY")
                attachments.append(
                    EmailAttachment(
                        name=document.nombre_archivo,
                        content_type="application/pdf",
                        data=content,
                    )
                )
            return tuple(attachments)

        notifications = SqlNotificationStore(engine)
        dispatcher = NotificationDispatcher(
            queue=notifications,
            sender=BrevoNotificationSender(
                api_key=settings.brevo_api_key.get_secret_value(),
                sender_email=settings.mail_from,
                sender_name=settings.mail_from_name,
            ),
            clock=lambda: datetime.now(UTC),
            attachments_for=attachments_for,
            content_for=lambda due: content_for(due, notifications, settings.email_assets_base_url),
        )
        return run(dispatcher)
    finally:
        engine.dispose()


if __name__ == "__main__":
    sys.exit(main())
