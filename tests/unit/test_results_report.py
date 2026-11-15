"""The results PDF: a valid file with the portal's content, whatever the number of results."""

from __future__ import annotations

import re
from datetime import UTC, date, datetime

from medicalbooking.modules.laboratory.application.ports import (
    LabDetail,
    LabOrder,
    LabResult,
    ReportParties,
)
from medicalbooking.modules.laboratory.application.results_report import build_results_report

ORDER_ID = "11111111-2222-3333-4444-555555555555"
NOW = datetime(2026, 11, 14, 12, 0, tzinfo=UTC)


def _order(details: int) -> LabOrder:
    return LabOrder(
        id=ORDER_ID,
        paciente_id="p",
        paciente_user_id="u",
        medico_id="m",
        medico_user_id="mu",
        prioridad="NORMAL",
        estado="COMPLETADA",
        observaciones="ayuno",
        details=tuple(
            LabDetail(id=f"d{i}", tipo_prueba_id="t", estado="COMPLETADO") for i in range(details)
        ),
    )


def _result(detail: str, name: str, **fields) -> LabResult:
    base = dict(
        id=f"r-{name}",
        detalle_id=detail,
        nombre_analito=name,
        valor_texto=None,
        valor_numerico=10.4,
        unidad="g/dL",
        referencia_min=12.0,
        referencia_max=16.0,
        interpretacion="BAJO",
    )
    base.update(fields)
    return LabResult(**base)


def _parties() -> ReportParties:
    return ReportParties(
        patient_name="María Pérez",
        patient_document="CEDULA 1712345678",
        patient_birth_date=date(1992, 5, 14),
        patient_sex="F",
        patient_email="maria@test.test",
        patient_phone="0999999999",
        doctor_name="Andrés Mora",
        doctor_registration="MSP-1234",
    )


def _text(pdf: bytes) -> str:
    return pdf.decode("latin-1")


def _in_pdf(pdf: bytes, expected: str) -> bool:
    """The content is WinAnsi bytes, so the expected text is looked up in the same encoding."""
    return expected.encode("cp1252", errors="replace").decode("latin-1") in _text(pdf)


def test_report_is_a_valid_pdf_with_its_xref_offsets(  # the file must open in any reader
) -> None:
    pdf = build_results_report(
        _order(1), [_result("d0", "Hemoglobina")], parties=_parties(), generated_at=NOW
    )

    text = _text(pdf)
    assert pdf.startswith(b"%PDF-1.4")
    assert pdf.rstrip().endswith(b"%%EOF")
    xref_at = int(re.search(r"startxref\n(\d+)\n", text).group(1))
    assert text[xref_at : xref_at + 4] == "xref"
    entries = re.findall(r"(\d{10}) 00000 n", text)
    for number, offset in enumerate(entries, start=1):
        assert text[int(offset) :].startswith(f"{number} 0 obj")


def test_report_shows_the_order_results_references_and_classification() -> None:
    pdf = build_results_report(
        _order(1),
        [
            _result("d0", "Hemoglobina"),
            _result(
                "d0",
                "Ferritina",
                valor_numerico=None,
                valor_texto="normal",
                referencia_min=None,
                referencia_max=None,
                interpretacion="NO_EVALUABLE",
            ),
        ],
        parties=_parties(),
        generated_at=NOW,
    )

    for expected in (
        "Resultados de laboratorio",
        "Prueba 1",
        "Hemoglobina",
        "10,4 g/dL",
        "12 – 16 g/dL",
        "Bajo",
        "Ferritina",
        "normal",
        "No evaluable",
    ):
        assert _in_pdf(pdf, expected), expected


def test_results_are_grouped_by_test_and_pages_are_counted() -> None:
    results = [_result(f"d{i}", f"Analito {i}-{n}") for i in range(3) for n in range(12)]

    text = _text(build_results_report(_order(3), results, parties=_parties(), generated_at=NOW))

    assert "Prueba 1" in text and "Prueba 3" in text
    assert "/Count 2" in text  # 36 rows do not fit one page
    assert "Página 1 de 2" in text


def test_order_without_results_still_gives_a_readable_report() -> None:
    assert _in_pdf(
        build_results_report(_order(1), [], parties=_parties(), generated_at=NOW),
        "Todavía no hay resultados validados",
    )


def test_long_observations_are_wrapped_and_never_cut() -> None:
    observation = " ".join(f"palabra{i}" for i in range(60)) + " fin-de-la-observacion"
    order = LabOrder(
        id=ORDER_ID,
        paciente_id="p",
        paciente_user_id="u",
        medico_id="m",
        medico_user_id="mu",
        prioridad="NORMAL",
        estado="COMPLETADA",
        observaciones=observation,
        details=(),
    )

    pdf = build_results_report(order, [], parties=_parties(), generated_at=NOW)

    assert _in_pdf(pdf, "fin-de-la-observacion")
    assert _in_pdf(pdf, "palabra0 ")


def test_the_document_shows_only_the_last_four_digits_of_the_cedula() -> None:
    pdf = build_results_report(_order(1), [], parties=_parties(), generated_at=NOW)

    assert _in_pdf(pdf, "CEDULA ******5678")
    assert not _in_pdf(pdf, "1712345678")


def test_report_time_is_ecuador_local_time_not_utc() -> None:
    pdf = build_results_report(_order(1), [], parties=_parties(), generated_at=NOW)

    # NOW is 12:00 UTC, which is 07:00 in Ecuador.
    assert _in_pdf(pdf, "generado el 14/11/2026 07:00 ")
    assert _in_pdf(pdf, "hora de Ecuador")
    assert not _in_pdf(pdf, "12:00")
