"""Glucosa en ayunas (ADA >= 126 mg/dL) contra HbA1c (>= 6,5 %) en NHANES, adultos de 18 o mas.

Sin ponderar, como el resto de la validacion. Intervalos de Wilson al 95 %.
Uso: python nhanes_diabetes_fpg_hba1c.py
"""

from __future__ import annotations

import math
import urllib.request
from pathlib import Path

import pandas as pd

DATOS = Path(__file__).resolve().parent / "datos"
URL = "https://wwwn.cdc.gov/Nchs/Data/Nhanes/Public/{anio}/DataFiles/{archivo}.xpt"
CICLOS = {"2017-2018": ("2017", "J"), "2015-2016": ("2015", "I")}
UMBRAL_FPG = 126.0
UMBRAL_HBA1C = 6.5


def descargar(anio: str, archivo: str) -> Path:
    DATOS.mkdir(exist_ok=True)
    destino = DATOS / f"{archivo}.xpt"
    if not destino.exists():
        peticion = urllib.request.Request(  # noqa: S310
            URL.format(anio=anio, archivo=archivo), headers={"User-Agent": "Mozilla/5.0"}
        )
        with urllib.request.urlopen(peticion, timeout=120) as resp:  # noqa: S310
            destino.write_bytes(resp.read())
    return destino


def wilson(k: int, n: int, z: float = 1.959964) -> tuple[float, float, float]:
    if n == 0:
        return (math.nan, math.nan, math.nan)
    p = k / n
    centro = (p + z * z / (2 * n)) / (1 + z * z / n)
    margen = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return (100 * p, 100 * (centro - margen), 100 * (centro + margen))


def cargar(ciclo: str) -> pd.DataFrame:
    anio, letra = CICLOS[ciclo]
    glu = pd.read_sas(descargar(anio, f"GLU_{letra}"), format="xport")[["SEQN", "LBXGLU"]]
    ghb = pd.read_sas(descargar(anio, f"GHB_{letra}"), format="xport")[["SEQN", "LBXGH"]]
    demo = pd.read_sas(descargar(anio, f"DEMO_{letra}"), format="xport")[["SEQN", "RIDAGEYR"]]
    df = glu.merge(ghb, on="SEQN").merge(demo, on="SEQN")
    df = df[(df["RIDAGEYR"] >= 18) & df["LBXGLU"].notna() & df["LBXGH"].notna()]
    return df


def evaluar(ciclo: str) -> str:
    df = cargar(ciclo)
    ref = df["LBXGH"] >= UMBRAL_HBA1C
    idx = df["LBXGLU"] >= UMBRAL_FPG
    vp = int((idx & ref).sum())
    fp = int((idx & ~ref).sum())
    fn = int((~idx & ref).sum())
    vn = int((~idx & ~ref).sum())
    sens = wilson(vp, vp + fn)
    espe = wilson(vn, vn + fp)
    vpp = wilson(vp, vp + fp)
    vpn = wilson(vn, vn + fn)
    fmt = lambda t: f"{t[0]:.1f} % ({t[1]:.1f} a {t[2]:.1f})"  # noqa: E731
    return (
        f"| {ciclo} | {len(df)} | {int(ref.sum())} | {vp} / {fp} / {fn} / {vn} | "
        f"{fmt(sens)} | {fmt(espe)} | {fmt(vpp)} | {fmt(vpn)} |"
    )


def main() -> int:
    encabezado = (
        "| Ciclo | n | HbA1c >= 6,5 | VP / FP / FN / VN "
        "| Sensibilidad | Especificidad | VPP | VPN |"
    )
    print(encabezado)
    print("|---|---|---|---|---|---|---|---|")
    for ciclo in CICLOS:
        print(evaluar(ciclo))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
