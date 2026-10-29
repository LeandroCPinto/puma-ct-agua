#!/usr/bin/env bash
# Atualiza o painel público: baixa as últimas horas do Hidrômetro 1, regenera
# data/*.json e publica no GitHub Pages se algo mudou. Feito para o cron do ryzen7:
#   10 * * * * $HOME/Documentos/puma-ct-agua/atualizar.sh >> $HOME/logs/puma-agua.log 2>&1
#
# Configuração (opcional) em ~/.config/puma-ct-agua.env:
#   HIDROMETRO_DIR=".../Medições/Hidrometro1"   # onde estão download_auto2.py e o CSV
#   PYTHON_DOWNLOAD="/caminho/venv/bin/python"  # python com pandas + playwright
set -uo pipefail

REPO="$(cd "$(dirname "$0")" && pwd)"
[ -f "$HOME/.config/puma-ct-agua.env" ] && . "$HOME/.config/puma-ct-agua.env"
HIDROMETRO_DIR="${HIDROMETRO_DIR:-$HOME/Insync/leandro.pinto@ufsm.br/Google Drive/DB/work/Programa PUMA CT/Medições/Hidrometro1}"
PYTHON_DOWNLOAD="${PYTHON_DOWNLOAD:-python3}"
CSV="$HIDROMETRO_DIR/Água Consumida.csv"

# Uma execução por vez: o download pode passar de uma hora se a Spinon estiver lenta.
exec 9>"$REPO/.atualizar.lock"
flock -n 9 || { echo "$(date -Is) outra execução em andamento; saindo"; exit 0; }

echo "=== $(date -Is) ==="

if [ "${SEM_DOWNLOAD:-0}" != 1 ]; then
  (cd "$HIDROMETRO_DIR" && "$PYTHON_DOWNLOAD" download_auto2.py --days 3)
  rc=$?
  # 2 = dado gravado mas medidor mudo: publica mesmo assim, o rodapé mostra o atraso.
  if [ $rc -ne 0 ] && [ $rc -ne 2 ]; then
    echo "download falhou (código $rc); publicando o que já existe no CSV"
  fi
fi

python3 "$REPO/exportar.py" "$CSV" || { echo "exportar.py falhou"; exit 1; }

cd "$REPO"
if [ -z "$(git status --porcelain -- data/)" ]; then
  echo "sem dado novo; nada a publicar"
  exit 0
fi
git add data/
git commit -q -m "dados: até $(python3 -c 'import json;print(json.load(open("data/meta.json"))["ultimo_ponto"])')"
git pull -q --rebase && git push -q && echo "publicado"
