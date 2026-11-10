"""Barrido de umbrales para diabetes, anemia y deficiencia de hierro en NHANES.

Tres bloques, cada uno con su referencia:
- Diabetes: glucosa en ayunas contra HbA1c >= 6,5 %. Referencia independiente: valida.
- Anemia: hemoglobina contra ferritina < 15 ug/L. Referencia de hierro: descriptiva, no valida.
- Hierro: solo muestra la deficiencia por ferritina en el mismo grupo. Descriptivo.

Los umbrales de anemia y hierro NO se eligen por maximizar el VPP: la referencia es el mismo
criterio de hierro, y eso ajustaria el hallazgo a la propia referencia. Se muestran solo
para ver el compromiso sensibilidad frente a VPP.

Uso: python nhanes_umbrales_barrido.py
Escribe resultados_umbrales_barrido.md en esta carpeta. Sin ponderar.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))

from nhanes_diabetes_fpg_hba1c import CICLOS, cargar, wilson  # noqa: E402

DATOS = Path(__file__).resolve().parent / "datos"
SALIDA = Path(__file__).resolve().parent / "resultados_umbrales_barrido.md"
UMBRALES_GLUCOSA = range(100, 141, 5)
HB_CORTES = {2: range(110, 131, 5), 1: range(120, 141, 5)}  # mujeres (2) y hombres (1), g/L
FERRITINA_CORTE = 15.0
CICLOS_HIERRO = {"2017-2018": "J", "2021-2023": "L"}


def fmt(t: tuple[float, float, float]) -> str:
    return f"{t[0]:.1f} ({t[1]:.1f} a {t[2]:.1f})"


def matriz(idx: pd.Series, ref: pd.Series) -> tuple[int, int, int, int]:
    return (
        int((idx & ref).sum()),
        int((idx & ~ref).sum()),
        int((~idx & ref).sum()),
        int((~idx & ~ref).sum()),
    )


def bloque_diabetes() -> list[str]:
    filas = [
        "### Diabetes (referencia HbA1c, independiente)",
        "",
        "| Ciclo | Umbral FPG (mg/dL) | n | Sensibilidad | Especificidad | VPP | VPN |",
        "|---|---|---|---|---|---|---|",
    ]
    for ciclo in CICLOS:
        df = cargar(ciclo)
        ref = df["LBXGH"] >= 6.5
        for umbral in UMBRALES_GLUCOSA:
            vp, fp, fn, vn = matriz(df["LBXGLU"] >= umbral, ref)
            filas.append(
                f"| {ciclo} | {umbral} | {len(df)} | {fmt(wilson(vp, vp + fn))} | "
                f"{fmt(wilson(vn, vn + fp))} | {fmt(wilson(vp, vp + fp))} | "
                f"{fmt(wilson(vn, vn + fn))} |"
            )
    return filas + [""]


def tabla_hierro(ciclo: str, letra: str) -> pd.DataFrame:
    cbc = pd.read_sas(DATOS / f"CBC_{letra}.xpt", format="xport")[["SEQN", "LBXHGB"]]
    fer = pd.read_sas(DATOS / f"FERTIN_{letra}.xpt", format="xport")[["SEQN", "LBXFER"]]
    demo = pd.read_sas(DATOS / f"DEMO_{letra}.xpt", format="xport")[
        ["SEQN", "RIAGENDR", "RIDAGEYR"]
    ]
    df = cbc.merge(fer, on="SEQN").merge(demo, on="SEQN")
    df = df[(df["RIDAGEYR"] >= 18) & (df["RIDAGEYR"] <= 65)]
    df = df.dropna(subset=["LBXHGB", "LBXFER"]).copy()
    df["LBXHGB"] = df["LBXHGB"] * 10  # NHANES: g/dL; los cortes van en g/L
    return df


def bloque_anemia_hierro() -> list[str]:
    filas = [
        "### Anemia contra deficiencia de hierro (ferritina < 15 ug/L, descriptiva)",
        "",
        "| Ciclo | Sexo | Corte Hb (g/L) | n | Sensibilidad | Especificidad | VPP | VPN |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for ciclo, letra in CICLOS_HIERRO.items():
        df = tabla_hierro(ciclo, letra)
        for sexo, cortes in HB_CORTES.items():
            sub = df[df["RIAGENDR"] == sexo]
            if sub.empty:
                continue
            ref = sub["LBXFER"] < FERRITINA_CORTE
            nombre = "mujeres" if sexo == 2 else "hombres"
            for corte in cortes:
                vp, fp, fn, vn = matriz(sub["LBXHGB"] < corte, ref)
                filas.append(
                    f"| {ciclo} | {nombre} | {corte} | {len(sub)} | "
                    f"{fmt(wilson(vp, vp + fn))} | {fmt(wilson(vn, vn + fp))} | "
                    f"{fmt(wilson(vp, vp + fp))} | {fmt(wilson(vn, vn + fn))} |"
                )
    return filas + [""]


def main() -> int:
    lineas = ["# Barrido de umbrales: diabetes, anemia y hierro (NHANES)", ""]
    lineas += bloque_diabetes()
    lineas += bloque_anemia_hierro()
    lineas += [
        "Notas: el ciclo 2021-2023 solo tiene ferritina en mujeres de 18 a 49 anos, "
        "por lo que no hay hombres en ese ciclo.",
        "Los umbrales de anemia y hierro son descriptivos: no se eligen por maximizar "
        "el VPP contra ferritina.",
    ]
    texto = "\n".join(lineas) + "\n"
    SALIDA.write_text(texto, encoding="utf-8")
    print(texto)
    print(f"Escrito en {SALIDA}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
