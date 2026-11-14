"""SOAP 1.1 adapter for the laboratory status operation (E6).

HTTP and XML only. The JWT is read from the Authorization header and checked by the identity
guard; ownership is decided by the laboratory rule behind the status service. Every failure is
a SOAP Fault, and missing and foreign orders share one fault so identifiers do not leak.
"""

from __future__ import annotations

import uuid
from datetime import UTC
from typing import Any
from xml.sax.saxutils import escape

from defusedxml import DefusedXmlException
from defusedxml.ElementTree import ParseError, fromstring
from flask import Blueprint, Response, abort, current_app, request

from medicalbooking.modules.identity.application.ports import AccessProfile
from medicalbooking.modules.interoperability.presentation.wsdl import NAMESPACE, build_wsdl
from medicalbooking.shared.domain.errors import Forbidden, NotFound, Unauthenticated

soap_bp = Blueprint("soap", __name__)

ENVELOPE_NS = "http://schemas.xmlsoap.org/soap/envelope/"
_ENVELOPE_TAG = f"{{{ENVELOPE_NS}}}Envelope"
_BODY_TAG = f"{{{ENVELOPE_NS}}}Body"
_OPERATION_TAG = f"{{{NAMESPACE}}}GetLaboratoryResultStatus"
_ORDER_ID_TAG = f"{{{NAMESPACE}}}orderId"
_XML_MIME = "text/xml"
_CLIENT_FAULT = "soap:Client"
_AUTH_REQUIRED = "Authentication required"
# One message for missing and foreign orders: the response must not reveal which exist.
_NOT_AVAILABLE = "Order not available for this account"


class _SoapFault(Exception):
    def __init__(self, status: int, faultstring: str, *, challenge: bool = False) -> None:
        super().__init__(faultstring)
        self.status = status
        self.faultstring = faultstring
        self.challenge = challenge

    def to_response(self) -> Response:
        headers = {"WWW-Authenticate": "Bearer"} if self.challenge else {}
        body = _envelope(
            "<soap:Fault>"
            f"<faultcode>{_CLIENT_FAULT}</faultcode>"
            f"<faultstring>{escape(self.faultstring)}</faultstring>"
            "</soap:Fault>"
        )
        return Response(body, status=self.status, mimetype=_XML_MIME, headers=headers)


def _envelope(body_content: str) -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f'<soap:Envelope xmlns:soap="{ENVELOPE_NS}"><soap:Body>{body_content}</soap:Body>'
        "</soap:Envelope>"
    )


def _authenticate() -> AccessProfile:
    scheme, _, token = request.headers.get("Authorization", "").partition(" ")
    if scheme != "Bearer" or not token:
        raise _SoapFault(401, _AUTH_REQUIRED, challenge=True)
    identity = current_app.extensions["medicalbooking.identity"]
    try:
        return identity.authenticate_token(token)
    except Unauthenticated:
        raise _SoapFault(401, _AUTH_REQUIRED, challenge=True) from None


def _parse_order_id(data: bytes) -> str:
    """Parse the envelope with defusedxml, which refuses DOCTYPE and entity declarations."""
    try:
        root = fromstring(data)
    except (DefusedXmlException, ParseError):
        raise _SoapFault(400, "Malformed SOAP request") from None
    body = root.find(_BODY_TAG) if root.tag == _ENVELOPE_TAG else None
    if body is None or len(body) != 1:
        raise _SoapFault(400, "Malformed SOAP request")
    operation = body[0]
    if operation.tag != _OPERATION_TAG:
        raise _SoapFault(400, "Unsupported operation")
    value = operation.findtext(_ORDER_ID_TAG)
    if value is None:
        raise _SoapFault(400, "Malformed SOAP request")
    try:
        return str(uuid.UUID(value.strip()))
    except ValueError:
        # Not a UUID, so it cannot name an order: same answer as a missing order.
        raise _SoapFault(403, _NOT_AVAILABLE) from None


def _status_xml(status: Any) -> str:
    moment = status.checked_at.astimezone(UTC).isoformat(timespec="seconds")
    return _envelope(
        f'<lab:GetLaboratoryResultStatusResponse xmlns:lab="{NAMESPACE}">'
        f"<lab:orderId>{escape(status.order_id)}</lab:orderId>"
        f"<lab:status>{escape(status.status)}</lab:status>"
        f"<lab:timestamp>{escape(moment.replace('+00:00', 'Z'))}</lab:timestamp>"
        "</lab:GetLaboratoryResultStatusResponse>"
    )


@soap_bp.get("/soap/laboratory")
def laboratory_wsdl() -> Response:
    if "wsdl" not in request.args:
        abort(404)
    location = request.host_url.rstrip("/") + "/soap/laboratory"
    return Response(build_wsdl(location), mimetype=_XML_MIME)


@soap_bp.post("/soap/laboratory")
def laboratory_operation() -> Response:
    try:
        actor = _authenticate()
        order_id = _parse_order_id(request.get_data())
        status = current_app.extensions["medicalbooking.laboratory_status"].status_of(
            actor, order_id
        )
    except _SoapFault as fault:
        return fault.to_response()
    except (Forbidden, NotFound):
        return _SoapFault(403, _NOT_AVAILABLE).to_response()
    return Response(_status_xml(status), status=200, mimetype=_XML_MIME)
