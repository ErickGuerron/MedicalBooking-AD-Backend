# SOAP laboratory status contract

The backend exposes one SOAP 1.1 operation for external consumers that need to know whether a
laboratory order has progressed. It returns a minimal status only.

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| GET | `/soap/laboratory?wsdl` | Returns the WSDL (public, no token). |
| POST | `/soap/laboratory` | Calls `GetLaboratoryResultStatus` (SOAP 1.1 envelope). |

- Namespace: `urn:medicalbooking:laboratory:v1`
- Service `LaboratoryService`, port `LaboratoryPort`, document/literal.
- The WSDL is hand-written and static. Its `soap:address` is the absolute URL of the request.

## Operation

Request body (inside `soap:Body`):

```xml
<lab:GetLaboratoryResultStatus xmlns:lab="urn:medicalbooking:laboratory:v1">
  <lab:orderId>9b1e0c5a-2f4d-4e7a-9c8b-1a2b3c4d5e6f</lab:orderId>
</lab:GetLaboratoryResultStatus>
```

Successful response (`200`):

```xml
<lab:GetLaboratoryResultStatusResponse xmlns:lab="urn:medicalbooking:laboratory:v1">
  <lab:orderId>...</lab:orderId>
  <lab:status>SOLICITADA</lab:status>
  <lab:timestamp>2026-01-02T03:04:05Z</lab:timestamp>
</lab:GetLaboratoryResultStatusResponse>
```

- `status` is the order state (`SOLICITADA`, `EN_PROCESO`, `COMPLETADA`, `CANCELADA`).
- `timestamp` is the moment the status was read, in UTC. It is not the order creation time.
- The answer never contains analyte names, values, units, ranges, proposals or diagnoses.

## Authentication and authorization

- Send the JWT in the HTTP header `Authorization: Bearer <token>`. A token in the SOAP body is
  ignored.
- Missing or invalid token: `401` with `WWW-Authenticate: Bearer` and a SOAP Fault
  (`Authentication required`).
- Ownership uses the same rule as `GET /api/v1/lab/orders/{id}`: the patient who owns the order,
  or the doctor who placed it. Other callers are refused.
- A missing order, a foreign order and a malformed `orderId` all return the same `403` Fault
  (`Order not available for this account`), byte for byte. Identifiers do not leak.

## Errors

| Case | HTTP | faultstring |
|---|---|---|
| No or invalid bearer token | 401 | `Authentication required` |
| Missing, foreign or malformed order | 403 | `Order not available for this account` |
| Not well-formed XML, or missing `Body`/`orderId` | 400 | `Malformed SOAP request` |
| DOCTYPE or entity declarations | 400 | `Malformed SOAP request` |
| Operation other than `GetLaboratoryResultStatus` | 400 | `Unsupported operation` |

## Parsing and security

- The envelope is parsed with `defusedxml`. DOCTYPE, entity declarations and external references
  are rejected before any operation logic runs. Do not replace it with the standard library parser.
- The XML is parsed only after the token is validated.

## Consumer example

```python
import httpx

wsdl = httpx.get("https://api.example/soap/laboratory", params={"wsdl": ""})
# Read the operation name and the endpoint address from the WSDL, then POST the envelope with
# the header Authorization: Bearer <token>.
```

The independent consumer used in tests lives in `tests/contract/soap_consumer.py`.

## Limits

- Only the status operation is exposed. Results, documents and reviews stay in the REST API.
- The contract test runs against a loopback server; it does not test a deployed gateway.
