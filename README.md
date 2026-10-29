# Água no Prédio 9C: painel público do Hidrômetro 1

Painel público do consumo de água do Prédio 9C do Centro de Tecnologia da UFSM (programa PUMA/CT). Ele substitui o relatório do Looker Studio "PUMA/CT - Consumo de Água" e se atualiza sozinho a cada hora.

## Como funciona

```
ryzen7, cron a cada hora (minuto 10)
  atualizar.sh
    1. download_auto2.py --days 3   (fica na pasta Hidrometro1 do Drive; baixa o Grafana da Spinon)
    2. exportar.py                  ("Água Consumida.csv" -> data/consumo.json + data/meta.json)
    3. git commit + push            (só quando há hora nova)
GitHub Pages  ->  index.html + app.js leem data/*.json e agregam no navegador
```

A página consulta `data/meta.json` de novo a cada 10 min e redesenha quando chega uma hora nova. Se o último dado tiver mais de 6 h, o rodapé mostra "medidor sem comunicação".

| Arquivo | O que é |
|---|---|
| `data/consumo.json` | série horária em m³/h desde 22/10/2024 15:00 (`null` = hora sem leitura) |
| `data/meta.json` | `gerado_em`, `ultimo_ponto`, totais |
| `data/ocupacao.json` | perfil de ocupação por meia hora (ensalamento 1º/2025), estático |
| `data/calendario.json` | semestres letivos e feriados da UFSM, usados no perfil de consumo |

Abas: Hidrômetro 1, consumo horário, diário, semanal (segunda a domingo) e mensal, perfil de consumo médio por hora (dias letivos sem feriados, por semestre) e perfil de ocupação.

## Instalação no ryzen7

```bash
git clone git@github.com:LeandroCPinto/puma-ct-agua.git ~/puma-ct-agua
mkdir -p ~/logs
# se a pasta do Drive ou o python do download forem outros:
cat > ~/.config/puma-ct-agua.env <<'EOF'
HIDROMETRO_DIR="/caminho/para/Medições/Hidrometro1"
PYTHON_DOWNLOAD="/caminho/do/python/com/pandas/e/playwright"
EOF
SEM_DOWNLOAD=1 ~/puma-ct-agua/atualizar.sh      # teste sem baixar
crontab -e
# 10 * * * * $HOME/puma-ct-agua/atualizar.sh >> $HOME/logs/puma-agua.log 2>&1
```

Se o cron atual já roda o `download_auto2.py`, troque essa entrada por esta, que faz o download e a publicação.

## Atualizações manuais

- **Novo semestre:** acrescente em `data/calendario.json` as datas do calendário da UFSM.
- **Nova ocupação:** `python3 exportar.py --ocupacao Perfil_Ocupacao_Predio_MeiaHora.xlsx --semestre "2º/2025"`.
- **Ver localmente:** `python3 -m http.server`, depois abra http://localhost:8000.

Só usa a biblioteca padrão do Python. As credenciais da Spinon ficam no `download_auto2.py` do Drive e nunca entram neste repositório.
