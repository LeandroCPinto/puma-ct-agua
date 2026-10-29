#!/usr/bin/env bash
# Atualiza o painel público: baixa as últimas horas do Hidrômetro 1, regenera
# data/*.json e publica no GitHub Pages se algo mudou. Feito para o cron do ryzen7:
#   15 * * * * $HOME/Documentos/puma-ct-agua/atualizar.sh >> $HOME/logs/puma-agua.log 2>&1
#
# Configuração em ~/.config/puma-ct-agua.env:
#   SPINON_USER="..." SPINON_PASS="..."         # login do Grafana da Spinon (obrigatório)
#   PYTHON_DOWNLOAD="/caminho/venv/bin/python"  # python com pandas + playwright
#   DADOS_DIR="$HOME/.local/share/puma-ct-agua" # onde fica o CSV e os backups (opcional)
#   HIDROMETRO_DIR=".../Medições/Hidrometro1"   # recebe uma cópia do CSV (opcional)
set -uo pipefail

REPO="$(cd "$(dirname "$0")" && pwd)"
# set -a: o baixar.py lê SPINON_USER/SPINON_PASS/DADOS_DIR do ambiente.
set -a
[ -f "$HOME/.config/puma-ct-agua.env" ] && . "$HOME/.config/puma-ct-agua.env"
DADOS_DIR="${DADOS_DIR:-$HOME/.local/share/puma-ct-agua}"
set +a
PYTHON_DOWNLOAD="${PYTHON_DOWNLOAD:-python3}"
CSV="$DADOS_DIR/Água Consumida.csv"

# Uma execução por vez: o download pode passar de uma hora se a Spinon estiver lenta.
exec 9>"$REPO/.atualizar.lock"
flock -n 9 || { echo "$(date -Is) outra execução em andamento; saindo"; exit 0; }

echo "=== $(date -Is) ==="

if [ "${SEM_DOWNLOAD:-0}" != 1 ]; then
  "$PYTHON_DOWNLOAD" "$REPO/baixar.py" --days 3
  rc=$?
  # 2 = dado gravado mas medidor mudo: publica mesmo assim, o rodapé mostra o atraso.
  if [ $rc -ne 0 ] && [ $rc -ne 2 ]; then
    echo "download falhou (código $rc); publicando o que já existe no CSV"
  fi
fi

python3 "$REPO/exportar.py" "$CSV" || { echo "exportar.py falhou"; exit 1; }

# Cópia no Drive: o grafico2.py lê o CSV de lá, e o Drive serve de backup.
# Se o disco não estiver montado, só avisa; o painel segue sem ele.
if [ -n "${HIDROMETRO_DIR:-}" ]; then
  if [ -d "$HIDROMETRO_DIR" ]; then
    cmp -s "$CSV" "$HIDROMETRO_DIR/Água Consumida.csv" || cp "$CSV" "$HIDROMETRO_DIR/Água Consumida.csv"
  else
    echo "aviso: $HIDROMETRO_DIR não existe; cópia do CSV não feita"
  fi
fi

cd "$REPO"
if [ -z "$(git status --porcelain -- data/)" ]; then
  echo "sem dado novo; nada a publicar"
  exit 0
fi
git add data/
git commit -q -m "dados: até $(python3 -c 'import json;print(json.load(open("data/meta.json"))["ultimo_ponto"])')"
git pull -q --rebase && git push -q && echo "publicado"
