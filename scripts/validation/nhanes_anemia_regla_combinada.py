"""Validación de la regla de anemia contra deficiencia de hierro en NHANES.

    python scripts/validation/nhanes_anemia_regla_combinada.py

Compara el hallazgo de anemia del dominio (domain/anemia.py, OMS 2024) con variantes
que añaden VCM y RDW, y escribe sensibilidad, especificidad, VPP y VPN con intervalos
de Wilson al 95 % en resultados_regla_combinada.md y en
knowledge/validation/nhanes_anemia_regla_combinada.csv.

La anemia y el ajuste de tabaquismo se calculan con las funciones del dominio, no con
umbrales copiados aquí.

Los criterios de la muestra, las URL y los hash SHA-256 de los archivos viven en
protocolo_muestreo.json. Los .xpt se descargan a datos/ (no versionado) y se
comprueban contra su hash antes de usarse.

Los umbrales de VCM y RDW son candidatos de prueba, no conocimiento validado.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import math
import sys
import urllib.request
from decimal import Decimal
from pathlib import Path
from types import ModuleType

import pandas as pd

AQUI = Path(__file__).resolve().parent
DATOS = AQUI / "datos"
PROTOCOLO = json.loads((AQUI / "protocolo_muestreo.json").read_text(encoding="utf-8"))
CRITERIOS = PROTOCOLO["criterios"]
EDAD_MIN, EDAD_MAX = CRITERIOS["edad_min"], CRITERIOS["edad_max"]
EMBARAZO_EXCLUIDO = CRITERIOS["embarazo_excluido"]
PCR_MAX = CRITERIOS["pcr_max_mg_l"]
FERRITINA_CORTE = PROTOCOLO["referencia"]["ferritina_corte_ug_l"]
VCM_BAJO = PROTOCOLO["prueba"]["vcm_bajo_fl"]
RDW_ALTO = PROTOCOLO["prueba"]["rdw_alto_pct"]

REPO = AQUI.parents[1]
DOMINIO = REPO / "src" / "medicalbooking" / "modules" / "clinical" / "domain"
SALIDA_CSV = REPO / "knowledge" / "validation" / "nhanes_anemia_regla_combinada.csv"
SEXO = {1: "M", 2: "F"}

Intervalo = tuple[float, float, float]


def _dominio(nombre: str) -> ModuleType:
    """Carga un módulo puro del dominio por ruta, sin importar el paquete de la aplicación."""
    spec = importlib.util.spec_from_file_location(f"dominio_{nombre}", DOMINIO / f"{nombre}.py")
    if spec is None or spec.loader is None:
        raise RuntimeError(f"No se pudo cargar domain/{nombre}.py")
    modulo = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = modulo
    spec.loader.exec_module(modulo)
    return modulo


ANEMIA = _dominio("anemia")
AJUSTES = _dominio("hb_adjustments")
# microcitosis.py importa anemia.py por su nombre de paquete: se registra el mismo módulo.
sys.modules["medicalbooking.modules.clinical.domain.anemia"] = ANEMIA
MICRO = _dominio("microcitosis")
LIMITE_VCM_PRUEBA = Decimal(str(PROTOCOLO["prueba"]["vcm_bajo_fl"]))  # valor de prueba, no regla


def obtener(ciclo: str) -> dict[str, pd.DataFrame]:
    """Descarga lo que falte, verifica el hash y devuelve las tablas por prefijo."""
    info = PROTOCOLO["ciclos"][ciclo]
    DATOS.mkdir(exist_ok=True)
    tablas = {}
    for archivo, sha_esperado in info["archivos"].items():
        destino = DATOS / f"{archivo}.xpt"
        if not destino.exists():
            url = PROTOCOLO["url_base"].format(anio=info["anio"], archivo=archivo)
            print(f"Descargando {url}")
            peticion = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})  # noqa: S310
            with urllib.request.urlopen(peticion, timeout=120) as resp:  # noqa: S310
                destino.write_bytes(resp.read())
        sha = hashlib.sha256(destino.read_bytes()).hexdigest()
        if sha != sha_esperado:
            raise RuntimeError(
                f"{destino.name}: el hash no coincide con el protocolo "
                f"(esperado {sha_esperado[:12]}, obtenido {sha[:12]}). "
                "El CDC pudo actualizar el archivo; revisar antes de cambiar el protocolo."
            )
        tablas[archivo.rsplit("_", 1)[0]] = pd.read_sas(destino, format="xport")
    return tablas


def ajuste_tabaco(fila: pd.Series) -> float:
    """Ajuste de tabaquismo en g/L con domain/hb_adjustments.py. NaN si no hay respuesta.

    SMQ040 solo se pregunta a quien declaró haber fumado 100 cigarrillos (SMQ020 = 1).
    Exigir SMQ040 con valor descarta a quienes nunca fumaron; por eso se mira antes SMQ020.
    """
    alguna_vez, ahora, cigarrillos = fila.get("SMQ020"), fila.get("SMQ040"), fila.get("SMD650")
    if alguna_vez == 2 or ahora == 3:
        fuma, cantidad = False, None
    elif ahora in (1, 2):
        sin_dato = pd.isna(cigarrillos) or cigarrillos > 95  # 777/999: rehúsa o no sabe
        fuma, cantidad = True, None if sin_dato else int(cigarrillos)
    else:
        return float("nan")
    return float(AJUSTES.smoking_adjustment(fuma, cantidad))


def categoria_anemia(fila: pd.Series, ajuste_g_l: int) -> str:
    """Categoría de anemia del dominio. La muestra excluye el embarazo, así que es "NO"."""
    sexo = SEXO[int(fila["RIAGENDR"])]
    hallazgo = ANEMIA.anemia_finding(
        Decimal(str(fila["LBXHGB"])),
        sexo,
        int(fila["RIDAGEYR"]) * 12,
        ANEMIA.NO_PREGNANT if sexo == "F" else None,
        ajuste_g_l=ajuste_g_l,
    )
    if hallazgo.categoria == ANEMIA.NO_EVALUABLE:
        raise RuntimeError(f"Anemia no evaluable en la muestra: {hallazgo.motivo}")
    return hallazgo.categoria


def senal_s20(fila: pd.Series, categoria: str) -> bool:
    """Señal de microcitosis (S20) del dominio, con el límite de prueba de 80 fL."""
    vcm = Decimal(str(fila["LBXMCVSI"]))
    hallazgo = MICRO.microcitosis_signal(categoria, vcm, LIMITE_VCM_PRUEBA)
    return hallazgo.categoria == MICRO.SENAL_MICROCITOSIS


def preparar(tablas: dict[str, pd.DataFrame]) -> tuple[pd.DataFrame, list[tuple[str, int]]]:
    """Une las tablas y aplica los criterios del protocolo. Devuelve también el flujo de n."""
    df = tablas["DEMO"][["SEQN", "RIAGENDR", "RIDAGEYR", "RIDEXPRG"]]
    df = df.merge(tablas["CBC"][["SEQN", "LBXHGB", "LBXMCVSI", "LBXRDW"]], on="SEQN", how="left")
    df = df.merge(tablas["FERTIN"][["SEQN", "LBXFER"]], on="SEQN", how="left")
    df = df.merge(tablas["HSCRP"][["SEQN", "LBXHSCRP"]], on="SEQN", how="left")
    df = df.merge(tablas["TFR"][["SEQN", "LBXTFR"]], on="SEQN", how="left")
    cols_smq = [c for c in ("SEQN", "SMQ020", "SMQ040", "SMD650") if c in tablas["SMQ"].columns]
    df = df.merge(tablas["SMQ"][cols_smq], on="SEQN", how="left")

    flujo = [("Participantes del ciclo", len(df))]
    df = df[df["RIDAGEYR"].between(EDAD_MIN, EDAD_MAX)]
    flujo.append((f"Edad {EDAD_MIN} a {EDAD_MAX}", len(df)))
    df = df[~df["RIDEXPRG"].isin(EMBARAZO_EXCLUIDO)]
    flujo.append(("Sin embarazo (RIDEXPRG 1 o 3)", len(df)))
    df = df.dropna(subset=CRITERIOS["variables_obligatorias"])
    flujo.append(("Con hemoglobina, ferritina, PCR, sTfR, VCM y RDW", len(df)))
    df = df.assign(ajuste=df.apply(ajuste_tabaco, axis=1)).dropna(subset=["ajuste"])
    flujo.append(("Con respuesta de tabaquismo", len(df)))
    df = df[df["LBXHSCRP"] <= PCR_MAX]
    flujo.append((f"PCR <= {PCR_MAX:g} mg/L", len(df)))

    df = df.assign(
        cat_sin=df.apply(lambda f: categoria_anemia(f, 0), axis=1),
        cat_con=df.apply(lambda f: categoria_anemia(f, int(f["ajuste"])), axis=1),
        deficiencia=df["LBXFER"] < FERRITINA_CORTE,
        vcm_bajo=df["LBXMCVSI"] < VCM_BAJO,
        rdw_alto=df["LBXRDW"] > RDW_ALTO,
    )
    return df, flujo


def wilson(k: int, n: int, z: float = 1.959964) -> Intervalo:
    if n == 0:
        return (float("nan"), float("nan"), float("nan"))
    p = k / n
    den = 1 + z * z / n
    centro = (p + z * z / (2 * n)) / den
    mitad = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return p, max(0.0, centro - mitad), min(1.0, centro + mitad)


def evaluar(df: pd.DataFrame) -> pd.DataFrame:
    filas = []
    sanos = ANEMIA.SIN_ANEMIA
    cat_s = df["cat_sin"]
    cat_c = df["cat_con"]
    variantes = (
        ("Sin ajuste", cat_s != sanos, df.apply(lambda f: senal_s20(f, f["cat_sin"]), axis=1)),
        ("Con tabaquismo", cat_c != sanos, df.apply(lambda f: senal_s20(f, f["cat_con"]), axis=1)),
    )
    for etiqueta_hb, con_anemia, senal in variantes:
        reglas = {
            "Base: Hb baja": con_anemia,
            "Hb baja Y VCM bajo": con_anemia & df["vcm_bajo"],
            "Anemia y señal S20 (dominio, límite de prueba 80 fL)": senal,
            "Hb baja Y RDW alto": con_anemia & df["rdw_alto"],
            "Hb baja Y (VCM bajo O RDW alto)": con_anemia & (df["vcm_bajo"] | df["rdw_alto"]),
            "Hb baja Y VCM bajo Y RDW alto": con_anemia & df["vcm_bajo"] & df["rdw_alto"],
        }
        for nombre, positivo in reglas.items():
            vp = int((positivo & df["deficiencia"]).sum())
            fp = int((positivo & ~df["deficiencia"]).sum())
            fn = int((~positivo & df["deficiencia"]).sum())
            vn = int((~positivo & ~df["deficiencia"]).sum())
            filas.append(
                {
                    "hemoglobina": etiqueta_hb,
                    "regla": nombre,
                    "VP": vp,
                    "FP": fp,
                    "FN": fn,
                    "VN": vn,
                    "sensibilidad": wilson(vp, vp + fn),
                    "especificidad": wilson(vn, vn + fp),
                    "VPP": wilson(vp, vp + fp),
                    "VPN": wilson(vn, vn + fn),
                }
            )
    return pd.DataFrame(filas)


def pct(t: Intervalo) -> str:
    if any(math.isnan(x) for x in t):
        return "sin datos"
    return f"{t[0] * 100:.1f} % ({t[1] * 100:.1f} a {t[2] * 100:.1f})".replace(".", ",")


def a_markdown(ciclo: str, df: pd.DataFrame, flujo: list[tuple[str, int]]) -> str:
    sexo = df["RIAGENDR"].map({1: "hombres", 2: "mujeres"}).value_counts().to_dict()
    composicion = ", ".join(f"{n} {s}" for s, n in sorted(sexo.items()))
    out = [f"## NHANES {ciclo}", "", "| Paso | n |", "|---|---|"]
    out += [f"| {paso} | {n} |" for paso, n in flujo]
    out += [
        "",
        f"Composición: {composicion}; edad {df['RIDAGEYR'].min():.0f} a "
        f"{df['RIDAGEYR'].max():.0f} años.",
        "",
        f"Deficiencia de hierro (ferritina < {FERRITINA_CORTE:g} µg/L): "
        f"{int(df['deficiencia'].sum())} de {len(df)} "
        f"({pct(wilson(int(df['deficiencia'].sum()), len(df))).split(' (')[0]}).",
        "",
        "| Hemoglobina | Regla | VP | FP | FN | VN | Sensibilidad | Especificidad | VPP | VPN |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in evaluar(df).itertuples():  # la tabla CSV se arma aparte, en filas_csv
        out.append(
            f"| {r.hemoglobina} | {r.regla} | {r.VP} | {r.FP} | {r.FN} | {r.VN} | "
            f"{pct(r.sensibilidad)} | {pct(r.especificidad)} | {pct(r.VPP)} | {pct(r.VPN)} |"
        )
    return "\n".join(out) + "\n"


def filas_csv(ciclo: str, df: pd.DataFrame) -> pd.DataFrame:
    """Una fila por regla con los conteos y cada métrica como valor, límite inferior y superior."""
    res = evaluar(df)
    out = res[["hemoglobina", "regla", "VP", "FP", "FN", "VN"]].copy()
    out.insert(0, "ciclo", ciclo)
    out.insert(1, "n", len(df))
    for metrica in ("sensibilidad", "especificidad", "VPP", "VPN"):
        for i, sufijo in enumerate(("", "_ic_inf", "_ic_sup")):
            out[f"{metrica}{sufijo}"] = [round(t[i] * 100, 1) for t in res[metrica]]
    return out


def main() -> int:
    secciones = [
        "# Regla combinada (Hb + VCM + RDW) contra deficiencia de hierro en NHANES",
        "",
        "**Estado:** resultado de prueba. Umbrales candidatos, no validados por un clínico.",
        "",
        f"Generado por `{Path(__file__).name}` con el protocolo versión {PROTOCOLO['version']} "
        f"(datos descargados el {PROTOCOLO['fecha_descarga']}). "
        f"Umbrales: VCM < {VCM_BAJO:g} fL, RDW > {RDW_ALTO:g} %. "
        "Intervalos de Wilson al 95 %, sin ponderar.",
        "",
    ]
    preparados, tablas_csv = [], []
    for ciclo in PROTOCOLO["ciclos"]:
        df, flujo = preparar(obtener(ciclo))
        preparados.append(df)
        tablas_csv.append(filas_csv(ciclo, df))
        secciones.append(a_markdown(ciclo, df, flujo))
    junto = pd.concat(preparados, ignore_index=True)
    secciones.append(
        a_markdown("ciclos combinados", junto, [("Suma de los ciclos", len(junto))])
        + "\nLos ciclos no cubren la misma población (ver composición de cada uno); "
        "el combinado sirve para estrechar intervalos, no como estimación poblacional.\n"
    )
    salida = AQUI / "resultados_regla_combinada.md"
    salida.write_text("\n".join(secciones), encoding="utf-8", newline="\n")
    SALIDA_CSV.parent.mkdir(parents=True, exist_ok=True)
    with SALIDA_CSV.open("w", encoding="utf-8", newline="\n") as archivo:
        pd.concat(tablas_csv, ignore_index=True).to_csv(archivo, index=False)
    print(f"Guardado en {salida} y {SALIDA_CSV}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
