#!/usr/bin/env python3
# coding: utf-8
"""
Avisa no celular (ntfy) quando o Hidrômetro 1 para de mandar dado ou quando o
consumo foge do padrão. Lê os data/*.json que o atualizar.sh gera; feito para
o cron do ryzen7, alguns minutos depois do atualizar.sh:
    25 * * * * $HOME/Documentos/puma-ct-agua/alerta.py >> $HOME/logs/puma-agua-alerta.log 2>&1

Verificações:
    sem dado    último ponto com mais de SEM_DADO_H horas (igual ao rodapé do
                painel). Avisa ao começar, repete a cada 12 h e avisa quando volta.
    parado      zeros há 24 h ou mais até o último ponto (o exportar.py marca
                como "sem registro" e o diário não enxerga). Avisa ao começar,
                repete a cada 12 h e avisa quando a medição volta.
    diário      total de ontem comparado com a mediana do mesmo dia da semana
                nas 6 semanas anteriores; avisa acima de 2× ou abaixo de 0,5×.
                Em feriado só avisa o consumo alto.
    madrugada   média das 2h às 5h da última noite acima de 3× a mediana das
                30 noites anteriores (e pelo menos +0,03 m³/h): possível vazamento.

Configuração em ~/.config/puma-ct-agua.env (o mesmo do atualizar.sh):
    NTFY_TOPIC="puma-ct-agua-<algo-secreto>"
    NTFY_URL="https://ntfy.sh"          # opcional
    ALERTA_FATOR="2"                    # opcional: 2 = dobro / metade

Uso:
    alerta.py            verifica e notifica
    alerta.py --teste    manda uma notificação de teste
    alerta.py --seco     verifica e só imprime, sem notificar nem gravar estado
"""

import argparse
import json
import os
import shlex
import statistics as st
import sys
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

TZ = timezone(timedelta(hours=-3))
DATA = Path(__file__).resolve().parent / "data"
ENV = Path.home() / ".config/puma-ct-agua.env"
ESTADO = Path.home() / ".local/state/puma-ct-agua/alerta.json"
PAINEL = "https://leandrocpinto.github.io/puma-ct-agua/"

SEM_DADO_H = 6
DIAS = ["seg", "ter", "qua", "qui", "sex", "sáb", "dom"]
REPETIR_SEM_DADO_H = 12
SEMANAS_REF = 6
NOITES_REF = 30


def ler_env():
    # O .env é shell; aqui só interessam linhas simples CHAVE="valor".
    cfg = {}
    if ENV.exists():
        for linha in ENV.read_text(encoding="utf-8").splitlines():
            linha = linha.strip()
            if not linha or linha.startswith("#") or "=" not in linha:
                continue
            chave, valor = linha.split("=", 1)
            partes = shlex.split(valor, comments=True)
            cfg[chave.strip()] = partes[0] if partes else ""
    cfg.update({k: v for k, v in os.environ.items() if k.startswith(("NTFY_", "ALERTA_"))})
    return cfg


def notificar(cfg, titulo, texto, prioridade=4, tags=("droplet",), seco=False):
    print(f"[{'seco' if seco else 'ntfy'}] {titulo}: {texto}")
    if seco:
        return
    topico = cfg.get("NTFY_TOPIC")
    if not topico:
        sys.exit("NTFY_TOPIC não definido em ~/.config/puma-ct-agua.env")
    corpo = json.dumps({
        "topic": topico, "title": titulo, "message": texto,
        "priority": prioridade, "tags": list(tags), "click": PAINEL,
    }).encode()
    req = urllib.request.Request(cfg.get("NTFY_URL", "https://ntfy.sh").rstrip("/"),
                                 data=corpo, headers={"Content-Type": "application/json"})
    urllib.request.urlopen(req, timeout=30).read()


def carregar_serie():
    c = json.loads((DATA / "consumo.json").read_text(encoding="utf-8"))
    t0 = datetime.fromisoformat(c["inicio"])
    return {t0 + timedelta(hours=i): x for i, x in enumerate(c["v"])}


def feriados():
    cal = json.loads((DATA / "calendario.json").read_text(encoding="utf-8"))
    return set(cal.get("feriados_no_letivo", []))


def totais_diarios(serie):
    dias = {}
    for t, x in serie.items():
        if x is not None:
            dias.setdefault(t.date(), []).append(x)
    # Dia com mais de 2 h sem leitura não serve nem de alvo nem de referência.
    return {d: sum(v) for d, v in dias.items() if len(v) >= 22}


def verificar_sem_dado(cfg, estado, agora, seco):
    meta = json.loads((DATA / "meta.json").read_text(encoding="utf-8"))
    ultimo = datetime.fromisoformat(meta["ultimo_ponto"])
    atraso_h = (agora - ultimo).total_seconds() / 3600
    s = estado.setdefault("sem_dado", {})
    if atraso_h > SEM_DADO_H:
        avisado = s.get("avisado_em")
        if not avisado or agora - datetime.fromisoformat(avisado) >= timedelta(hours=REPETIR_SEM_DADO_H):
            notificar(cfg, "Hidrômetro 1 sem dados",
                      f"Último dado: {ultimo:%d/%m %H:%M} (há {atraso_h:.0f} h). "
                      "Verifique o medidor/Spinon ou o cron do ryzen.",
                      prioridade=5, tags=("warning",), seco=seco)
            s["avisado_em"] = agora.isoformat(timespec="seconds")
    elif s.get("avisado_em"):
        notificar(cfg, "Hidrômetro 1 voltou",
                  f"Dados normalizados; último ponto {ultimo:%d/%m %H:%M}.",
                  prioridade=3, tags=("white_check_mark",), seco=seco)
        s.pop("avisado_em")
    return ultimo


def verificar_parado(cfg, estado, agora, ultimo, seco):
    meta = json.loads((DATA / "meta.json").read_text(encoding="utf-8"))
    sr = meta.get("sem_registro") or []
    s = estado.setdefault("parado", {})
    if sr and datetime.fromisoformat(sr[-1][1]) == ultimo:
        ini, horas = datetime.fromisoformat(sr[-1][0]), sr[-1][2]
        avisado = s.get("avisado_em")
        if not avisado or agora - datetime.fromisoformat(avisado) >= timedelta(hours=REPETIR_SEM_DADO_H):
            notificar(cfg, "Hidrômetro 1 marcando zero",
                      f"Consumo zero desde {ini:%d/%m %H:%M} ({horas} h). "
                      "Falta de água, medidor travado ou falha na coleta?",
                      prioridade=4, tags=("warning",), seco=seco)
            s["avisado_em"] = agora.isoformat(timespec="seconds")
    elif s.get("avisado_em"):
        notificar(cfg, "Hidrômetro 1 voltou a medir",
                  f"Consumo diferente de zero de novo; último ponto {ultimo:%d/%m %H:%M}.",
                  prioridade=3, tags=("white_check_mark",), seco=seco)
        s.pop("avisado_em")


def verificar_diario(cfg, estado, ultimo, seco):
    # Último dia completo: o próprio dia do último ponto só se ele já chegou às 23h.
    ontem = ultimo.date() if ultimo.hour == 23 else ultimo.date() - timedelta(days=1)
    if estado.get("diario_verificado") == ontem.isoformat():
        return
    tot = totais_diarios(carregar_serie())
    fer = feriados()
    fator = float(cfg.get("ALERTA_FATOR", 2))
    if ontem in tot:
        ref = [tot[d] for k in range(1, SEMANAS_REF + 1)
               if (d := ontem - timedelta(weeks=k)) in tot and d.isoformat() not in fer]
        if len(ref) >= 4 and (med := st.median(ref)) > 0.2:
            x = tot[ontem]
            feriado = ontem.isoformat() in fer
            dia = f"{ontem:%d/%m} ({'feriado' if feriado else DIAS[ontem.weekday()]})"
            if x > fator * med:
                notificar(cfg, "Consumo alto no Hidrômetro 1",
                          f"{dia}: {x:.2f} m³, {x / med:.1f}× a mediana do mesmo dia "
                          f"da semana ({med:.2f} m³).", tags=("chart_with_upwards_trend",), seco=seco)
            elif x < med / fator and not feriado:
                notificar(cfg, "Consumo baixo no Hidrômetro 1",
                          f"{dia}: {x:.2f} m³, {x / med:.0%} da mediana do mesmo dia "
                          f"da semana ({med:.2f} m³). Medidor travado?",
                          prioridade=3, tags=("chart_with_downwards_trend",), seco=seco)
    estado["diario_verificado"] = ontem.isoformat()


def verificar_madrugada(cfg, estado, ultimo, seco):
    noite = ultimo.date() if ultimo.hour >= 4 else ultimo.date() - timedelta(days=1)
    if estado.get("madrugada_verificada") == noite.isoformat():
        return
    serie = carregar_serie()

    def media(d):
        v = [serie.get(datetime(d.year, d.month, d.day, h, tzinfo=TZ)) for h in (2, 3, 4)]
        return st.mean(v) if None not in v else None

    x = media(noite)
    ref = [m for k in range(1, NOITES_REF + 1) if (m := media(noite - timedelta(days=k))) is not None]
    if x is not None and len(ref) >= 20:
        med = st.median(ref)
        if x > max(3 * med, med + 0.03):
            notificar(cfg, "Possível vazamento no Hidrômetro 1",
                      f"Madrugada de {noite:%d/%m} (2h–5h): {x:.3f} m³/h, contra "
                      f"{med:.3f} m³/h normalmente. Prédio deveria estar vazio.",
                      prioridade=5, tags=("rotating_light",), seco=seco)
    estado["madrugada_verificada"] = noite.isoformat()


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--teste", action="store_true", help="manda uma notificação de teste")
    ap.add_argument("--seco", action="store_true", help="só imprime; não notifica nem grava estado")
    args = ap.parse_args()
    cfg = ler_env()
    agora = datetime.now(TZ)
    print(f"=== {agora.isoformat(timespec='seconds')} ===")

    if args.teste:
        notificar(cfg, "Teste: alertas do Hidrômetro 1",
                  "Se você recebeu isto, os avisos estão funcionando.", prioridade=3)
        return

    estado = json.loads(ESTADO.read_text(encoding="utf-8")) if ESTADO.exists() and not args.seco else {}
    ultimo = verificar_sem_dado(cfg, estado, agora, args.seco)
    verificar_parado(cfg, estado, agora, ultimo, args.seco)
    verificar_diario(cfg, estado, ultimo, args.seco)
    verificar_madrugada(cfg, estado, ultimo, args.seco)
    if not args.seco:
        ESTADO.parent.mkdir(parents=True, exist_ok=True)
        ESTADO.write_text(json.dumps(estado, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
