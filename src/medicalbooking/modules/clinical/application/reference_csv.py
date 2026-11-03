"""Reference ranges from a reviewed CSV (knowledge/references.csv). Pure validation, no I/O.

Every row must cite the PMID of the source it was taken from. A file with any invalid row is
refused as a whole: the importer reports every problem with its line number and writes nothing.
"""

from __future__ import annotations

import csv
import io
import re
import uuid
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation

COLUMNS = (
    "analito_id",
    "pmid",
    "unidad",
    "minimo",
    "maximo",
    "sexo",
    "edad_min_dias",
    "edad_max_dias",
    "metodo",
    "tipo_muestra",
    "contexto",
    "fecha_inicio",
    "fecha_fin",
)
_PMID = re.compile(r"^[1-9][0-9]{0,8}$")


@dataclass(frozen=True)
class ReferenceRow:
    line: int
    analito_id: str
    pmid: str
    unidad: str
    minimo: Decimal | None
    maximo: Decimal | None
    sexo: str | None
    edad_min_dias: int | None
    edad_max_dias: int | None
    metodo: str | None
    tipo_muestra: str | None
    contexto: str | None
    fecha_inicio: date
    fecha_fin: date | None


@dataclass(frozen=True)
class ParseResult:
    rows: tuple[ReferenceRow, ...]
    errors: tuple[str, ...]


def _optional(value: str | None) -> str | None:
    cleaned = (value or "").strip()
    return cleaned or None


def _decimal(value: str | None, name: str, line: int, errors: list[str]) -> Decimal | None:
    raw = _optional(value)
    if raw is None:
        return None
    try:
        number = Decimal(raw)
    except InvalidOperation:
        errors.append(f"line {line}: {name} is not a number")
        return None
    if not number.is_finite():
        errors.append(f"line {line}: {name} must be finite")
        return None
    return number


def _integer(value: str | None, name: str, line: int, errors: list[str]) -> int | None:
    raw = _optional(value)
    if raw is None:
        return None
    if not raw.isdigit():
        errors.append(f"line {line}: {name} must be a whole number of days")
        return None
    return int(raw)


def _date(
    value: str | None, name: str, line: int, errors: list[str], required: bool
) -> date | None:
    raw = _optional(value)
    if raw is None:
        if required:
            errors.append(f"line {line}: {name} is required (YYYY-MM-DD)")
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError:
        errors.append(f"line {line}: {name} must be a date (YYYY-MM-DD)")
        return None


def parse_references(text: str) -> ParseResult:
    reader = csv.DictReader(io.StringIO(text))
    if reader.fieldnames is None or tuple(reader.fieldnames) != COLUMNS:
        return ParseResult((), (f"the header must be exactly: {','.join(COLUMNS)}",))
    rows: list[ReferenceRow] = []
    errors: list[str] = []
    for record in reader:
        line = reader.line_num
        before = len(errors)
        analito = _optional(record["analito_id"])
        try:
            analito = str(uuid.UUID(analito or ""))
        except ValueError:
            errors.append(f"line {line}: analito_id must be a UUID")
        pmid = _optional(record["pmid"]) or ""
        if not _PMID.match(pmid):
            errors.append(f"line {line}: pmid is required (digits only; the source must be cited)")
        unit = _optional(record["unidad"])
        if unit is None or len(unit) > 40:
            errors.append(f"line {line}: unidad is required (at most 40 characters)")
        minimo = _decimal(record["minimo"], "minimo", line, errors)
        maximo = _decimal(record["maximo"], "maximo", line, errors)
        if minimo is None and maximo is None:
            errors.append(f"line {line}: at least one of minimo or maximo is required")
        if minimo is not None and maximo is not None and minimo > maximo:
            errors.append(f"line {line}: minimo is greater than maximo")
        edad_min = _integer(record["edad_min_dias"], "edad_min_dias", line, errors)
        edad_max = _integer(record["edad_max_dias"], "edad_max_dias", line, errors)
        if edad_min is not None and edad_max is not None and edad_min > edad_max:
            errors.append(f"line {line}: edad_min_dias is greater than edad_max_dias")
        inicio = _date(record["fecha_inicio"], "fecha_inicio", line, errors, required=True)
        fin = _date(record["fecha_fin"], "fecha_fin", line, errors, required=False)
        if inicio and fin and fin < inicio:
            errors.append(f"line {line}: fecha_fin is before fecha_inicio")
        if len(errors) == before and analito is not None and inicio is not None:
            rows.append(
                ReferenceRow(
                    line=line,
                    analito_id=analito,
                    pmid=pmid,
                    unidad=unit or "",
                    minimo=minimo,
                    maximo=maximo,
                    sexo=_optional(record["sexo"]),
                    edad_min_dias=edad_min,
                    edad_max_dias=edad_max,
                    metodo=_optional(record["metodo"]),
                    tipo_muestra=_optional(record["tipo_muestra"]),
                    contexto=_optional(record["contexto"]),
                    fecha_inicio=inicio,
                    fecha_fin=fin,
                )
            )
    if not rows and not errors:
        errors.append("the file has no reference rows")
    return ParseResult(tuple(rows), tuple(errors))
