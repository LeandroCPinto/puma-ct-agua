#!/usr/bin/env python3
# coding: utf-8
"""
Converte "Água Consumida.csv" (gerado pelo download_auto2.py) nos JSON que o
painel público lê. Só biblioteca padrão: roda no ryzen7 sem venv.

Uso:
    python3 exportar.py "/caminho/Água Consumida.csv"
    python3 exportar.py --ocupacao Perfil_Ocupacao_Predio_MeiaHora.xlsx

Saídas (em data/):
    consumo.json  {inicio, passo_h, v: [m³/h por hora, null = sem dado]}
    meta.json     {gerado_em, ultimo_ponto, n_horas, n_nulos, total_m3, sem_registro}
    ocupacao.json {semestre, dias: {Seg: [{h, n}, ...], ...}}  (só com --ocupacao)

Só reescreve um arquivo quando o conteúdo muda, para que o git do cron não
gere commits vazios.
"""

import argparse
import csv
import json
import re
import sys
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from xml.etree import ElementTree as ET

TZ = timezone(timedelta(hours=-3))  # America/Sao_Paulo, sem horário de verão desde 2019
DATA = Path(__file__).resolve().parent / "data"
# Sequência de zeros exatos a partir desta duração vira "sem registro": o prédio
# nunca chega a 0 nem de madrugada, então é falha do medidor ou da coleta.
ZEROS_MIN_H = 24


def gravar_se_mudou(caminho, obj):
    texto = json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
    if caminho.exists() and caminho.read_text(encoding="utf-8") == texto:
        return False
    caminho.write_text(texto, encoding="utf-8")
    return True


def ler_csv(caminho):
    serie = {}
    with open(caminho, encoding="utf-8-sig", newline="") as f:
        for linha in csv.DictReader(f):
            t = datetime.strptime(linha["Time"], "%Y-%m-%d %H:%M:%S")
            bruto = (linha["Consumo"] or "").replace("m³", "").strip()
            serie[t] = float(bruto) if bruto not in ("", "nan", "NaN") else None
    if not serie:
        raise ValueError("CSV vazio")
    return serie


def anular_zeros_longos(v, inicio):
    """Troca por null as sequências de >= ZEROS_MIN_H horas com 0 exato e devolve
    os intervalos [início, fim (última hora), horas] em ISO local."""
    intervalos, i = [], 0
    while i < len(v):
        if v[i] != 0:
            i += 1
            continue
        j = i
        while j + 1 < len(v) and v[j + 1] == 0:
            j += 1
        if j - i + 1 >= ZEROS_MIN_H:
            v[i:j + 1] = [None] * (j - i + 1)
            iso = lambda k: (inicio + timedelta(hours=k)).replace(tzinfo=TZ).isoformat(timespec="minutes")
            intervalos.append([iso(i), iso(j), j - i + 1])
        i = j + 1
    return intervalos


def exportar_consumo(csv_path):
    serie = ler_csv(csv_path)
    inicio, fim = min(serie), max(serie)

    # Grade horária contínua: hora sem linha no CSV vira null. Assim o período em
    # que o medidor ficou mudo aparece como lacuna, não como consumo zero.
    v, t = [], inicio
    while t <= fim:
        x = serie.get(t)
        v.append(None if x is None else round(x, 3))
        t += timedelta(hours=1)
    sem_registro = anular_zeros_longos(v, inicio)

    consumo = {
        "inicio": inicio.replace(tzinfo=TZ).isoformat(timespec="minutes"),
        "passo_h": 1,
        "v": v,
    }
    mudou = gravar_se_mudou(DATA / "consumo.json", consumo)

    # gerado_em só avança quando há dado novo; senão o meta mudaria a cada hora
    # e o cron faria um commit por hora mesmo com o medidor parado.
    meta_path = DATA / "meta.json"
    antigo = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
    meta = {
        "gerado_em": (datetime.now(TZ).isoformat(timespec="seconds")
                      if mudou or "gerado_em" not in antigo else antigo["gerado_em"]),
        "ultimo_ponto": fim.replace(tzinfo=TZ).isoformat(timespec="minutes"),
        "n_horas": len(v),
        "n_nulos": sum(x is None for x in v),
        "total_m3": round(sum(x for x in v if x is not None), 3),
        "sem_registro": sem_registro,
    }
    gravar_se_mudou(meta_path, meta)
    print(f"consumo.json: {len(v)} horas ({meta['n_nulos']} sem dado, "
          f"{len(sem_registro)} intervalos de zeros ≥ {ZEROS_MIN_H} h anulados), "
          f"{inicio:%d/%m/%Y %H:%M} → {fim:%d/%m/%Y %H:%M}, "
          f"{meta['total_m3']:.2f} m³ — {'atualizado' if mudou else 'sem mudanças'}")
    return mudou


def ler_xlsx_simples(caminho):
    """Lê a primeira aba de um xlsx (strings inline ou compartilhadas) sem pandas."""
    ns = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    with zipfile.ZipFile(caminho) as z:
        compartilhadas = []
        if "xl/sharedStrings.xml" in z.namelist():
            raiz = ET.fromstring(z.read("xl/sharedStrings.xml"))
            compartilhadas = ["".join(t.text or "" for t in si.iter(f"{{{ns['m']}}}t"))
                              for si in raiz.findall("m:si", ns)]
        raiz = ET.fromstring(z.read("xl/worksheets/sheet1.xml"))
    linhas = []
    for row in raiz.iter(f"{{{ns['m']}}}row"):
        valores = {}
        for c in row.findall("m:c", ns):
            col = re.match(r"[A-Z]+", c.get("r")).group()
            tipo = c.get("t")
            if tipo == "inlineStr":
                val = "".join(t.text or "" for t in c.iter(f"{{{ns['m']}}}t"))
            elif tipo == "s":
                val = compartilhadas[int(c.find("m:v", ns).text)]
            else:
                el = c.find("m:v", ns)
                val = float(el.text) if el is not None else None
            valores[col] = val
        linhas.append(valores)
    return linhas


def exportar_ocupacao(xlsx_path, semestre):
    linhas = ler_xlsx_simples(xlsx_path)
    cab = linhas[0]
    if [cab.get(k) for k in "ABC"] != ["Dia", "Horário", "Ocupantes"]:
        raise ValueError(f"cabeçalho inesperado: {cab}")
    dias = {}
    for r in linhas[1:]:
        dias.setdefault(r["A"], []).append({"h": r["B"], "n": int(r["C"] or 0)})
    ordem = ["Seg", "Ter", "Qua", "Qui", "Sex", "Sáb", "Dom"]
    dias = {d: sorted(dias[d], key=lambda x: x["h"]) for d in ordem if d in dias}
    gravar_se_mudou(DATA / "ocupacao.json", {"semestre": semestre, "dias": dias})
    print(f"ocupacao.json: {', '.join(f'{d} ({len(x)})' for d, x in dias.items())}")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("csv", nargs="?", help='caminho de "Água Consumida.csv"')
    ap.add_argument("--ocupacao", metavar="XLSX",
                    help="gera data/ocupacao.json a partir do perfil de ocupação")
    ap.add_argument("--semestre", default="1º/2025",
                    help="semestre do perfil de ocupação (padrão: 1º/2025)")
    args = ap.parse_args()
    if not args.csv and not args.ocupacao:
        ap.error("informe o CSV e/ou --ocupacao")

    DATA.mkdir(exist_ok=True)
    if args.ocupacao:
        exportar_ocupacao(args.ocupacao, args.semestre)
    if args.csv:
        exportar_consumo(args.csv)
    return 0


if __name__ == "__main__":
    sys.exit(main())
