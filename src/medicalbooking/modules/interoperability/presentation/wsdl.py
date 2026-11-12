"""Hand-written WSDL 1.1 for the laboratory status service (SOAP 1.1, document/literal).

The contract is static: only the endpoint address depends on the request. Authentication is
not described here; the JWT travels in the HTTP Authorization header (see docs/soap-laboratory.md).
"""

from __future__ import annotations

from xml.sax.saxutils import quoteattr

NAMESPACE = "urn:medicalbooking:laboratory:v1"

_TEMPLATE = """<?xml version="1.0" encoding="UTF-8"?>
<wsdl:definitions xmlns:wsdl="http://schemas.xmlsoap.org/wsdl/"
    xmlns:soap="http://schemas.xmlsoap.org/wsdl/soap/"
    xmlns:xs="http://www.w3.org/2001/XMLSchema"
    xmlns:tns="{namespace}"
    targetNamespace="{namespace}"
    name="LaboratoryService">
  <wsdl:types>
    <xs:schema targetNamespace="{namespace}" elementFormDefault="qualified">
      <xs:element name="GetLaboratoryResultStatus">
        <xs:complexType>
          <xs:sequence>
            <xs:element name="orderId" type="xs:string"/>
          </xs:sequence>
        </xs:complexType>
      </xs:element>
      <xs:element name="GetLaboratoryResultStatusResponse">
        <xs:complexType>
          <xs:sequence>
            <xs:element name="orderId" type="xs:string"/>
            <xs:element name="status" type="xs:string"/>
            <xs:element name="timestamp" type="xs:dateTime"/>
          </xs:sequence>
        </xs:complexType>
      </xs:element>
    </xs:schema>
  </wsdl:types>
  <wsdl:message name="GetLaboratoryResultStatusInput">
    <wsdl:part name="parameters" element="tns:GetLaboratoryResultStatus"/>
  </wsdl:message>
  <wsdl:message name="GetLaboratoryResultStatusOutput">
    <wsdl:part name="parameters" element="tns:GetLaboratoryResultStatusResponse"/>
  </wsdl:message>
  <wsdl:portType name="LaboratoryPortType">
    <wsdl:operation name="GetLaboratoryResultStatus">
      <wsdl:input message="tns:GetLaboratoryResultStatusInput"/>
      <wsdl:output message="tns:GetLaboratoryResultStatusOutput"/>
    </wsdl:operation>
  </wsdl:portType>
  <wsdl:binding name="LaboratoryBinding" type="tns:LaboratoryPortType">
    <soap:binding style="document" transport="http://schemas.xmlsoap.org/soap/http"/>
    <wsdl:operation name="GetLaboratoryResultStatus">
      <soap:operation soapAction="{namespace}#GetLaboratoryResultStatus" style="document"/>
      <wsdl:input>
        <soap:body use="literal"/>
      </wsdl:input>
      <wsdl:output>
        <soap:body use="literal"/>
      </wsdl:output>
    </wsdl:operation>
  </wsdl:binding>
  <wsdl:service name="LaboratoryService">
    <wsdl:port name="LaboratoryPort" binding="tns:LaboratoryBinding">
      <soap:address location={location}/>
    </wsdl:port>
  </wsdl:service>
</wsdl:definitions>
"""


def build_wsdl(location: str) -> str:
    """Return the WSDL document; ``location`` is the absolute URL of the SOAP endpoint."""
    return _TEMPLATE.format(namespace=NAMESPACE, location=quoteattr(location))
