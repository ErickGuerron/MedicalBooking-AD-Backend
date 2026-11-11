"""Flask application factory.

Each replica is stateless: no in-memory locks or caches hold business state.
Concurrency is enforced by the database.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from flask import Flask, Response, g, jsonify, request
from werkzeug.exceptions import HTTPException

from medicalbooking.config import Settings
from medicalbooking.modules.clinical.application.clinical_profile import PatientProfileService
from medicalbooking.modules.clinical.application.knowledge import KnowledgeService
from medicalbooking.modules.clinical.application.proposals import ProposalService
from medicalbooking.modules.clinical.application.shadow_findings import ShadowFindingsService
from medicalbooking.modules.clinical.infrastructure.repositories import SqlClinicalStore
from medicalbooking.modules.clinical.presentation.openapi import clinical_fragment
from medicalbooking.modules.clinical.presentation.routes import clinical_bp
from medicalbooking.modules.identity.application.use_cases import IdentityService
from medicalbooking.modules.identity.infrastructure.email import BrevoActivationMailer
from medicalbooking.modules.identity.infrastructure.repositories import SqlUserDirectory
from medicalbooking.modules.identity.infrastructure.security import (
    Argon2PasswordHasher,
    JwtTokenIssuer,
)
from medicalbooking.modules.identity.presentation.openapi import identity_fragment
from medicalbooking.modules.identity.presentation.routes import identity_bp
from medicalbooking.modules.laboratory.application.use_cases import LabService
from medicalbooking.modules.laboratory.infrastructure.repositories import SqlLabStore
from medicalbooking.modules.laboratory.infrastructure.storage import LocalDocumentStorage
from medicalbooking.modules.laboratory.presentation.openapi import laboratory_fragment
from medicalbooking.modules.laboratory.presentation.routes import lab_bp
from medicalbooking.modules.payments.application.billing_service import BillingService
from medicalbooking.modules.payments.application.customers import CustomerDirectory
from medicalbooking.modules.payments.application.payment_methods import PaymentMethodService
from medicalbooking.modules.payments.application.webhook_service import StripeWebhookService
from medicalbooking.modules.payments.infrastructure.billing_repositories import SqlBillingStore
from medicalbooking.modules.payments.infrastructure.customer_repositories import (
    SqlStripeCustomerStore,
)
from medicalbooking.modules.payments.infrastructure.repositories import SqlPaymentsStore
from medicalbooking.modules.payments.infrastructure.stripe_checkout import StripeCheckoutGateway
from medicalbooking.modules.payments.presentation.openapi import payments_fragment
from medicalbooking.modules.payments.presentation.routes import payments_bp
from medicalbooking.modules.scheduling.application.use_cases import SchedulingService
from medicalbooking.modules.scheduling.infrastructure.repositories import SqlSchedulingStore
from medicalbooking.modules.scheduling.presentation.openapi import scheduling_fragment
from medicalbooking.modules.scheduling.presentation.routes import scheduling_bp
from medicalbooking.shared.domain.errors import AppError
from medicalbooking.shared.infrastructure.database import create_database_engine
from medicalbooking.shared.presentation.api_docs import register_api_docs
from medicalbooking.shared.presentation.health import health_bp

_CORRELATION_HEADER = "X-Correlation-ID"


def create_app(settings: Settings | None = None) -> Flask:
    settings = settings or Settings()
    app = Flask("medicalbooking")
    app.extensions["medicalbooking.settings"] = settings
    # Rejects oversized bodies before Werkzeug spools them to disk (PDF limit is 10 MB).
    app.config["MAX_CONTENT_LENGTH"] = 12 * 1024 * 1024
    engine = create_database_engine(settings)
    app.extensions["medicalbooking.engine"] = engine
    app.extensions["medicalbooking.identity"] = _build_identity(settings, engine)
    app.extensions["medicalbooking.scheduling"] = _build_scheduling(settings, engine)
    app.extensions["medicalbooking.laboratory"] = _build_laboratory(settings, engine)
    knowledge, proposals, shadow, patient_profile = _build_clinical(engine)
    app.extensions["medicalbooking.knowledge"] = knowledge
    app.extensions["medicalbooking.proposals"] = proposals
    app.extensions["medicalbooking.shadow"] = shadow
    app.extensions["medicalbooking.patient_profile"] = patient_profile
    app.extensions["medicalbooking.payments_webhook"] = _build_payments_webhook(settings, engine)
    app.extensions["medicalbooking.billing"] = _build_billing(settings, engine)
    app.extensions["medicalbooking.payment_methods"] = _build_payment_methods(settings, engine)

    app.register_blueprint(health_bp)
    app.register_blueprint(payments_bp)
    app.register_blueprint(identity_bp)
    app.register_blueprint(scheduling_bp)
    app.register_blueprint(lab_bp)
    app.register_blueprint(clinical_bp)
    if settings.docs_enabled:
        register_api_docs(
            app,
            [
                identity_fragment(),
                scheduling_fragment(),
                laboratory_fragment(),
                clinical_fragment(),
                payments_fragment(),
            ],
        )
    _register_error_handlers(app)
    _register_correlation_id(app)
    _register_replica_header(app, settings.replica_id)
    return app


def _build_identity(settings: Settings, engine: Any) -> IdentityService:
    """Composition root for identity: wires adapters into the use cases."""
    signing_key = settings.jwt_signing_key.get_secret_value() if settings.jwt_signing_key else None
    return IdentityService(
        directory=SqlUserDirectory(engine),
        hasher=Argon2PasswordHasher(),
        tokens=JwtTokenIssuer(signing_key, settings.jwt_access_ttl_minutes),
        mailer=_build_mailer(settings),
        public_base_url=settings.app_public_url,
        verification_hours=settings.email_verification_hours,
    )


def _build_mailer(settings: Settings) -> BrevoActivationMailer | None:
    """Brevo is optional: without a key or sender, registration answers but sends nothing."""
    if settings.brevo_api_key is None or not settings.mail_from:
        return None
    return BrevoActivationMailer(
        api_key=settings.brevo_api_key.get_secret_value(),
        sender_email=settings.mail_from,
        sender_name=settings.mail_from_name,
        asset_base_url=settings.email_assets_base_url,
    )


def _build_scheduling(settings: Settings, engine: Any) -> SchedulingService:
    """Composition root for scheduling: the clock is injected so rules stay testable."""
    return SchedulingService(
        store=SqlSchedulingStore(engine),
        clock=lambda: datetime.now(UTC),
        hold_minutes=settings.appointment_hold_minutes,
    )


def _build_laboratory(settings: Settings, engine: Any) -> LabService:
    """Composition root for laboratory: SQL store plus private local document storage."""
    return LabService(
        store=SqlLabStore(engine),
        storage=LocalDocumentStorage(Path(settings.lab_documents_dir)),
        clock=lambda: datetime.now(UTC),
        correlation_id=lambda: g.get("correlation_id"),
    )


def _build_payments_webhook(settings: Settings, engine: Any) -> StripeWebhookService:
    """Composition root for the Stripe webhook (E4). The secret stays inside the service."""
    secret = (
        settings.stripe_webhook_secret.get_secret_value()
        if settings.stripe_webhook_secret
        else None
    )
    return StripeWebhookService(
        store=SqlPaymentsStore(engine, clock=lambda: datetime.now(UTC)),
        secret=secret,
        clock=lambda: int(datetime.now(UTC).timestamp()),
    )


def _build_billing(settings: Settings, engine: Any) -> BillingService:
    """Composition root for billing (invoices and checkout). The Stripe key stays in the gateway.

    Return URLs point to the Flutter front end; a payment is confirmed only by the webhook.
    """
    gateway = _build_stripe_gateway(settings)
    return BillingService(
        store=SqlBillingStore(engine),
        gateway=gateway,
        clock=lambda: datetime.now(UTC),
        customers=CustomerDirectory(store=SqlStripeCustomerStore(engine), gateway=gateway),
    )


def _build_payment_methods(settings: Settings, engine: Any) -> PaymentMethodService:
    """Composition root for saved payment methods (/me/payment-methods). One Stripe gateway and
    the same per-user customer mapping that checkout uses."""
    gateway = _build_stripe_gateway(settings)
    return PaymentMethodService(
        gateway=gateway,
        customers=CustomerDirectory(store=SqlStripeCustomerStore(engine), gateway=gateway),
    )


def _build_stripe_gateway(settings: Settings) -> StripeCheckoutGateway:
    key = settings.stripe_secret_key.get_secret_value() if settings.stripe_secret_key else None
    return StripeCheckoutGateway(
        secret_key=key,
        return_url=f"{settings.flutter_front_url}/pago/retorno?session_id={{CHECKOUT_SESSION_ID}}",
        publishable_key=settings.stripe_publishable_key,
    )


def _build_clinical(
    engine: Any,
) -> tuple[KnowledgeService, ProposalService, ShadowFindingsService, PatientProfileService]:
    """Composition root for clinical knowledge (E5), shadow findings (S13) and the patient profile
    (S12): one shared store."""
    store = SqlClinicalStore(engine)
    return (
        KnowledgeService(store=store, clock=_utc_now),
        ProposalService(store=store, clock=_utc_now),
        ShadowFindingsService(store=store, clock=_utc_now),
        PatientProfileService(store=store, clock=_utc_now),
    )


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _register_replica_header(app: Flask, replica_id: str) -> None:
    @app.after_request
    def _echo_replica_id(response: Response) -> Response:
        response.headers["X-Replica-Id"] = replica_id
        return response


def _register_correlation_id(app: Flask) -> None:
    @app.before_request
    def _set_correlation_id() -> None:
        incoming = request.headers.get(_CORRELATION_HEADER, "")
        try:
            g.correlation_id = str(uuid.UUID(incoming))
        except ValueError:
            g.correlation_id = str(uuid.uuid4())

    @app.after_request
    def _echo_correlation_id(response: Response) -> Response:
        response.headers[_CORRELATION_HEADER] = g.get("correlation_id", "")
        return response


def _problem(status: int, title: str, detail: str) -> tuple[Response, int]:
    body: dict[str, Any] = {
        "type": "about:blank",
        "title": title,
        "status": status,
        "detail": detail,
        "correlation_id": g.get("correlation_id", ""),
    }
    response = jsonify(body)
    response.mimetype = "application/problem+json"
    return response, status


def _register_error_handlers(app: Flask) -> None:
    @app.errorhandler(AppError)
    def _domain_error(error: AppError) -> tuple[Response, int]:
        return _problem(error.status, error.title, error.detail)

    @app.errorhandler(HTTPException)
    def _http_error(error: HTTPException) -> tuple[Response, int]:
        return _problem(error.code or 500, error.name, error.description or "")

    @app.errorhandler(Exception)
    def _unexpected_error(error: Exception) -> tuple[Response, int]:
        app.logger.exception("unhandled error", extra={"correlation_id": g.get("correlation_id")})
        return _problem(500, "Internal Server Error", "Unexpected error. Use correlation_id.")
