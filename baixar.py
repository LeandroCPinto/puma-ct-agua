#!/usr/bin/env python
# coding: utf-8
"""
Baixa a série horária de consumo de água do Hidrometro1 (Prédio 9C - CT/UFSM)
do Grafana da Spinon e atualiza "Água Consumida.csv".

Substitui o download_auto.py, que dirigia o navegador por coordenadas de tela
absolutas (pyautogui) e sleeps fixos. Aqui o Playwright espera pelos elementos
reais da página, roda headless e não toca na sessão gráfica do usuário.

Uso:
    baixar.py                                    # 22/10/2024 15:00 -> agora
    baixar.py               --days 7             # só a última semana (p/ cron)
    baixar.py               --from 2025-12-01    # a partir de uma data
    baixar.py               --headed             # mostra o navegador (debug)
    baixar.py               --dry-run            # baixa e valida, não grava
    baixar.py               --merge              # funde com o CSV existente

Credenciais: variáveis de ambiente SPINON_USER / SPINON_PASS (o atualizar.sh
as lê de ~/.config/puma-ct-agua.env; nunca entram no repositório, que é público).
O CSV e os backups ficam em $DADOS_DIR (padrão ~/.local/share/puma-ct-agua).

Códigos de saída:
    0  tudo certo
    1  falha no download ou CSV inválido (CSV de produção intacto)
    2  dado gravado, mas o último ponto está velho demais (medidor mudo)
"""

import argparse
import os
import shutil
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
from playwright.sync_api import TimeoutError as PWTimeout
from playwright.sync_api import sync_playwright

BASE = "https://monitoramento.spinon.com.br"
DASHBOARD_UID = "ca225f4e-8b22-4e34-9007-919f84b60c12"
DASHBOARD_SLUG = "smart-water-geral"
PANEL_ID = 33  # painel "Água Consumida / Hora"

TZ = timezone(timedelta(hours=-3))  # America/Sao_Paulo
SERIE_INICIO = datetime(2024, 10, 22, 15, 0, 0, tzinfo=TZ)

PROJETO = Path(os.environ.get("DADOS_DIR") or Path.home() / ".local/share/puma-ct-agua")
CSV_DESTINO = PROJETO / "Água Consumida.csv"

BACKUPS_MANTER = 10     # quantos backups datados preservar
MAX_ATRASO_HORAS = 6.0  # acima disso, exit 2 (o medidor publica a cada ~5 min)

USER = os.environ.get("SPINON_USER", "")
PASSWORD = os.environ.get("SPINON_PASS", "")


def log(msg):
    """print que sobrevive a um kill -TERM (o script antigo perdia todo o stdout)."""
    print(msg, flush=True)


def baixar_csv(dt_inicio, dt_fim, destino, headed=False, timeout_ms=120_000):
    """Loga no Grafana, abre o painel em modo inspect e baixa o CSV.

    Retorna o Path do arquivo baixado. Levanta exceção se qualquer etapa falhar
    — nunca segue em frente cega, como fazia a versão pyautogui.
    """
    from_ms = int(dt_inicio.timestamp() * 1000)
    to_ms = int(dt_fim.timestamp() * 1000)
    url = (
        f"{BASE}/d/{DASHBOARD_UID}/{DASHBOARD_SLUG}"
        f"?from={from_ms}&to={to_ms}&inspect={PANEL_ID}&inspectTab=data"
    )

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=not headed)
        ctx = browser.new_context(
            timezone_id="America/Sao_Paulo",
            accept_downloads=True,
            viewport={"width": 1600, "height": 1000},
        )
        page = ctx.new_page()
        try:
            log("Abrindo página de login...")
            page.goto(f"{BASE}/login", wait_until="domcontentloaded", timeout=timeout_ms)

            # Espera o campo existir de fato, em vez de dormir 5s e torcer.
            page.wait_for_selector('input[name="user"]', timeout=timeout_ms)
            page.fill('input[name="user"]', USER)
            page.fill('input[name="password"]', PASSWORD)
            page.click('button[aria-label="Login button"]')

            page.wait_for_url(lambda u: "/login" not in u, timeout=timeout_ms)
            log(f"Login OK ({USER})")

            log(f"Abrindo painel {PANEL_ID} de "
                f"{dt_inicio:%d/%m/%Y %H:%M} a {dt_fim:%d/%m/%Y %H:%M}...")
            page.goto(url, wait_until="networkidle", timeout=timeout_ms)

            btn = page.wait_for_selector(
                "button:has-text('Download CSV')", timeout=timeout_ms
            )
            # A consulta pode continuar carregando depois do botão aparecer.
            page.wait_for_load_state("networkidle", timeout=timeout_ms)

            log("Baixando CSV...")
            with page.expect_download(timeout=timeout_ms) as dl_info:
                btn.click()
            download = dl_info.value
            download.save_as(destino)
            log(f"Download concluído: {destino.name} "
                f"({destino.stat().st_size:,} bytes)")
            return destino
        finally:
            browser.close()


def validar(caminho, dt_fim, descartar_hora_aberta=True):
    """Valida o CSV baixado antes de deixá-lo chegar perto do arquivo bom.

    O script antigo sobrescrevia o CSV de produção com shutil.move ANTES de
    fazer qualquer backup ou conferência — um download truncado destruía a série.
    """
    df = pd.read_csv(caminho, encoding="utf-8-sig")

    if list(df.columns) != ["Time", "Consumo"]:
        raise ValueError(f"colunas inesperadas: {list(df.columns)}")
    if df.empty:
        raise ValueError("CSV veio vazio")

    df["Time"] = pd.to_datetime(df["Time"], errors="coerce")
    n_ruins = int(df["Time"].isna().sum())
    if n_ruins:
        raise ValueError(f"{n_ruins} timestamps inválidos")

    df["Consumo"] = (
        df["Consumo"].astype(str).str.replace("m³", "", regex=False).str.strip()
    )
    df["Consumo"] = pd.to_numeric(df["Consumo"], errors="coerce")

    if (df["Consumo"] < 0).any():
        raise ValueError("há valores de consumo negativos")

    nan = int(df["Consumo"].isna().sum())
    atraso = dt_fim - df["Time"].max().tz_localize(TZ)
    log(f"Validação: {len(df)} linhas | {df['Time'].min()} → {df['Time'].max()} "
        f"| {nan} NaN | atraso do último ponto: {atraso}")

    df = df.sort_values("Time").reset_index(drop=True)

    # A hora corrente ainda está aberta: o Grafana devolve o consumo parcial
    # acumulado até agora, que cresce a cada minuto. Guardá-lo faria (a) o CSV
    # mudar a cada execução, disparando um backup por hora, e (b) a série
    # terminar sempre num valor subestimado — foi assim que o arquivo antigo
    # ficou com "2025-12-15 22:00,0.0". Descarta-se; a próxima rodada pega a
    # hora já fechada.
    if descartar_hora_aberta:
        corte = pd.Timestamp(datetime.now(TZ).replace(
            minute=0, second=0, microsecond=0, tzinfo=None))
        n_antes = len(df)
        df = df[df["Time"] < corte].reset_index(drop=True)
        if len(df) < n_antes:
            log(f"  hora em aberto descartada ({n_antes - len(df)} linha)")

    if df.empty:
        raise ValueError("nada sobrou após descartar a hora em aberto")

    return df, atraso


def rotacionar_backups(manter=BACKUPS_MANTER):
    """Mantém apenas os N backups datados mais recentes.

    Sem isso, rodando de hora em hora o diretório acumularia ~24 arquivos de
    500 KB por dia. Não mexe no 'Água Consumida_backup.csv' legado (sem carimbo),
    que veio do script antigo.
    """
    antigos = sorted(PROJETO.glob("Água Consumida_backup-*.csv"))
    for velho in antigos[:-manter] if len(antigos) > manter else []:
        velho.unlink()
        log(f"Backup rotacionado (removido): {velho.name}")


def gravar(df_novo, merge=False):
    """Grava o CSV de produção. Backup datado só quando o conteúdo muda."""
    if CSV_DESTINO.exists():
        antigo = pd.read_csv(CSV_DESTINO, encoding="utf-8-sig")
        antigo["Time"] = pd.to_datetime(antigo["Time"], errors="coerce")

        if merge:
            antes = len(antigo)
            df_novo = (
                pd.concat([antigo, df_novo])
                .drop_duplicates(subset="Time", keep="last")
                .sort_values("Time")
                .reset_index(drop=True)
            )
            log(f"Merge: {antes} linhas antigas + novas → {len(df_novo)} linhas")

        # Nada mudou? Não reescreve e não gera backup — o caso comum quando o
        # cron roda e o medidor ainda não publicou uma hora nova.
        if antigo.equals(df_novo):
            log("Sem mudanças em relação ao CSV atual; nada gravado.")
            return False

        carimbo = datetime.now(TZ).strftime("%Y%m%d-%H%M%S")
        backup = CSV_DESTINO.with_name(f"Água Consumida_backup-{carimbo}.csv")
        shutil.copy2(CSV_DESTINO, backup)
        log(f"Backup criado: {backup.name}")
        rotacionar_backups()

    df_novo.to_csv(CSV_DESTINO, index=False)
    log(f"Gravado: {CSV_DESTINO.name} ({len(df_novo)} linhas, "
        f"até {df_novo['Time'].max()})")
    return True


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--from", dest="inicio", metavar="YYYY-MM-DD",
                    help=f"início (padrão: {SERIE_INICIO:%Y-%m-%d %H:%M})")
    ap.add_argument("--to", dest="fim", metavar="YYYY-MM-DD",
                    help="fim (padrão: agora)")
    ap.add_argument("--headed", action="store_true", help="mostra o navegador")
    ap.add_argument("--dry-run", action="store_true",
                    help="baixa e valida, mas não grava o CSV de produção")
    ap.add_argument("--merge", action="store_true",
                    help="funde com o CSV existente em vez de substituir")
    ap.add_argument("--days", type=int, metavar="N",
                    help="baixa só os últimos N dias (implica --merge). "
                         "Modo leve, para o cron.")
    ap.add_argument("--max-atraso", type=float, default=MAX_ATRASO_HORAS,
                    metavar="H",
                    help=f"sai com código 2 se o último dado for mais velho que "
                         f"H horas (padrão: {MAX_ATRASO_HORAS}). 0 desliga.")
    args = ap.parse_args()

    if args.days is not None:
        if args.inicio:
            ap.error("--days e --from são mutuamente exclusivos")
        # Sem merge, uma janela curta truncaria a série histórica inteira.
        args.merge = True

    dt_fim = (
        datetime.strptime(args.fim, "%Y-%m-%d").replace(tzinfo=TZ) + timedelta(days=1)
        if args.fim else datetime.now(TZ)
    )
    if args.days is not None:
        dt_inicio = dt_fim - timedelta(days=args.days)
        log(f"Modo incremental: últimos {args.days} dia(s), merge ativado.")
    elif args.inicio:
        dt_inicio = datetime.strptime(args.inicio, "%Y-%m-%d").replace(tzinfo=TZ)
    else:
        dt_inicio = SERIE_INICIO

    if not (USER and PASSWORD):
        log("ERRO: defina SPINON_USER e SPINON_PASS em ~/.config/puma-ct-agua.env")
        return 1
    PROJETO.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory() as tmp:
        bruto = Path(tmp) / "download.csv"
        try:
            baixar_csv(dt_inicio, dt_fim, bruto, headed=args.headed)
        except PWTimeout as e:
            log(f"ERRO: tempo esgotado esperando a página. {e}")
            return 1
        except Exception as e:
            log(f"ERRO no download: {type(e).__name__}: {e}")
            return 1

        try:
            df, atraso = validar(bruto, dt_fim,
                                 descartar_hora_aberta=args.fim is None)
        except Exception as e:
            log(f"ERRO: CSV baixado é inválido, nada foi gravado. {e}")
            return 1

        if args.dry_run:
            log("--dry-run: CSV de produção não foi tocado.")
        else:
            gravar(df, merge=args.merge)

    # O dado é válido, só velho. Grava e só então reclama, para que o cron
    # mande e-mail: 8 meses de silêncio passaram despercebidos uma vez.
    if args.max_atraso and atraso > timedelta(hours=args.max_atraso):
        log(f"ERRO: último dado tem {atraso} (limite: {args.max_atraso}h). "
            f"Medidor provavelmente mudo.")
        return 2

    log("Concluído.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
