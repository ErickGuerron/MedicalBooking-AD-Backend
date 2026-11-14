"""Barrido del umbral de glucosa en ayunas contra HbA1c >= 6,5 %, por ciclo y franja de edad.

Usa la misma carga de datos que nhanes_diabetes_fpg_hba1c.py. Sin ponderar.
Uso: python nhanes_diabetes_barrido_umbral.py
Escribe resultados_diabetes_barrido.md en esta misma carpeta.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from nhanes_diabetes_fpg_hba1c import CICLOS, cargar, wilson  # noqa: E402

UMBRALES = range(100, 141, 5)
BANDAS = [("Todos", 18, 200), ("18-44", 18, 44), ("45-64", 45, 64), ("65 o mas", 65, 200)]
SALIDA = Path(__file__).resolve().parent / "resultados_diabetes_barrido.md"


def fmt(t: tuple[float, float, float]) -> str:
    return f"{t[0]:.1f} ({t[1]:.1f} a {t[2]:.1f})"


def filas(ciclo: str) -> list[str]:
    df = cargar(ciclo)
    salida = []
    for nombre, edad_min, edad_max in BANDAS:
        sub = df[(df["RIDAGEYR"] >= edad_min) & (df["RIDAGEYR"] <= edad_max)]
        ref = sub["LBXGH"] >= 6.5
        n = len(sub)
        for umbral in UMBRALES:
            idx = sub["LBXGLU"] >= umbral
            vp = int((idx & ref).sum())
            fp = int((idx & ~ref).sum())
            fn = int((~idx & ref).sum())
            vn = int((~idx & ~ref).sum())
            salida.append(
                f"| {ciclo} | {nombre} | {n} | {umbral} | "
                f"{fmt(wilson(vp, vp + fn))} | {fmt(wilson(vn, vn + fp))} | "
                f"{fmt(wilson(vp, vp + fp))} | {fmt(wilson(vn, vn + fn))} |"
            )
    return salida


def main() -> int:
    cabecera = [
        "| Ciclo | Franja | n | Umbral FPG (mg/dL) | Sensibilidad | Especificidad | VPP | VPN |",
        "|---|---|---|---|---|---|---|---|",
    ]
    lineas = list(cabecera)
    for ciclo in CICLOS:
        lineas.extend(filas(ciclo))
    texto = "\n".join(lineas) + "\n"
    SALIDA.write_text(texto, encoding="utf-8")
    print(texto)
    print(f"Escrito en {SALIDA}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
