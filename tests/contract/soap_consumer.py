"""Independent SOAP consumer for the laboratory status service.

It imports nothing from ``medicalbooking``: it speaks only HTTP and XML, the way an external
client would. It discovers the operation and the endpoint address from the WSDL.
"""

from __future__ import annotations

from dataclasses import dataclass

import defusedxml.ElementTree as ET
import httpx

WSDL_NS = "http://schemas.xmlsoap.org/wsdl/"
SOAP_BINDING_NS = "http://schemas.xmlsoap.org/wsdl/soap/"
ENVELOPE_NS = "http://schemas.xmlsoap.org/soap/envelope/"


@dataclass(frozen=True)
class StatusReply:
    order_id: str
    status: str
    timestamp: str
    field_names: tuple[str, ...]


@dataclass(frozen=True)
class ServiceDescription:
    namespace: str
    operation: str
    address: str


def _local(tag: str) -> str:
    return tag.split("}")[-1]


class LaboratoryConsumer:
    def __init__(self, base_url: str, *, timeout: float = 10.0) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout

    def describe(self) -> ServiceDescription:
        """Fetch ``?wsdl`` and read the namespace, operation and endpoint address from it."""
        response = httpx.get(
            f"{self._base_url}/soap/laboratory", params={"wsdl": ""}, timeout=self._timeout
        )
        response.raise_for_status()
        root = ET.fromstring(response.content)
        namespace = root.get("targetNamespace", "")
        operation_element = root.find(f"{{{WSDL_NS}}}portType/{{{WSDL_NS}}}operation")
        operation = "" if operation_element is None else operation_element.get("name", "")
        address = next(
            element.get("location", "") for element in root.iter(f"{{{SOAP_BINDING_NS}}}address")
        )
        return ServiceDescription(namespace=namespace, operation=operation, address=address)

    def get_status(
        self, description: ServiceDescription, order_id: str, token: str | None
    ) -> httpx.Response:
        headers = {"Content-Type": "text/xml; charset=utf-8", "SOAPAction": ""}
        if token is not None:
            headers["Authorization"] = f"Bearer {token}"
        envelope = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            f'<soap:Envelope xmlns:soap="{ENVELOPE_NS}"><soap:Body>'
            f'<lab:{description.operation} xmlns:lab="{description.namespace}">'
            f"<lab:orderId>{order_id}</lab:orderId>"
            f"</lab:{description.operation}>"
            "</soap:Body></soap:Envelope>"
        )
        return httpx.post(
            description.address,
            content=envelope.encode(),
            headers=headers,
            timeout=self._timeout,
        )

    @staticmethod
    def parse_status(response: httpx.Response) -> StatusReply:
        root = ET.fromstring(response.content)
        reply = next(
            element
            for element in root.iter()
            if _local(element.tag).endswith("ResultStatusResponse")
        )
        fields = {_local(child.tag): child.text or "" for child in reply}
        return StatusReply(
            order_id=fields["orderId"],
            status=fields["status"],
            timestamp=fields["timestamp"],
            field_names=tuple(_local(child.tag) for child in reply),
        )

    @staticmethod
    def fault_string(response: httpx.Response) -> str | None:
        root = ET.fromstring(response.content)
        for element in root.iter():
            if _local(element.tag) == "faultstring":
                return element.text
        return None
