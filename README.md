# Água no Prédio 9C: painel público do Hidrômetro 1

Painel público do consumo de água do Prédio 9C do Centro de Tecnologia da UFSM (programa PUMA/CT). Ele substitui o relatório do Looker Studio "PUMA/CT - Consumo de Água" e se atualiza sozinho a cada hora.

## Como funciona

```
ryzen7, cron a cada hora (minuto 15)
  atualizar.sh
    1. baixar.py --days 3   (Grafana da Spinon -> ~/.local/share/puma-ct-agua/Água Consumida.csv)
    2. exportar.py          ("Água Consumida.csv" -> data/consumo.json + data/meta.json)
    3. cópia do CSV na pasta Hidrometro1 do Drive (grafico2.py e backup), se a pasta existir
    4. git commit + push    (só quando há hora nova)
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
git clone git@github.com:LeandroCPinto/puma-ct-agua.git ~/Documentos/puma-ct-agua
mkdir -p ~/logs
cat > ~/.config/puma-ct-agua.env <<'EOF'
SPINON_USER="..."                                            # login do Grafana da Spinon
SPINON_PASS="..."
PYTHON_DOWNLOAD="/caminho/do/python/com/pandas/e/playwright"
HIDROMETRO_DIR="/caminho/para/Medições/Hidrometro1"          # opcional: recebe cópia do CSV
EOF
chmod 600 ~/.config/puma-ct-agua.env
# histórico: copie o "Água Consumida.csv" para ~/.local/share/puma-ct-agua/
# (ou rode uma vez o baixar.py sem --days, com as variáveis do .env exportadas)
SEM_DOWNLOAD=1 ~/Documentos/puma-ct-agua/atualizar.sh      # teste sem baixar
crontab -e
# 15 * * * * $HOME/Documentos/puma-ct-agua/atualizar.sh >> $HOME/logs/puma-agua.log 2>&1
```

O `baixar.py` veio do `download_auto2.py` da pasta Hidrometro1, que o cron não usa mais. Ele roda no python do `PYTHON_DOWNLOAD` (pandas + playwright).

## Alertas no celular

`alerta.py` roda no cron do ryzen7 às :25 (depois do `atualizar.sh`), lê `data/*.json` e manda notificações pelo [ntfy](https://ntfy.sh):

- **sem dados:** último ponto com mais de 6 h; repete a cada 12 h e avisa quando volta;
- **consumo diário:** total de ontem acima de 2× ou abaixo de 0,5× a mediana do mesmo dia da semana nas 6 semanas anteriores (em feriado só avisa o alto);
- **possível vazamento:** média das 2h às 5h acima de 3× a mediana das 30 noites anteriores.

O limite de 20% não serve: o consumo é baixo e irregular, e no histórico 77% dos dias fugiram mais de 20% da referência.

```bash
# no ~/.config/puma-ct-agua.env:  NTFY_TOPIC="puma-ct-agua-<secreto>"   (opcional: ALERTA_FATOR="2")
~/Documentos/puma-ct-agua/alerta.py --teste   # notificação de teste
~/Documentos/puma-ct-agua/alerta.py --seco    # só imprime o que avisaria
# 25 * * * * $HOME/Documentos/puma-ct-agua/alerta.py >> $HOME/logs/puma-agua-alerta.log 2>&1
```

No celular, instale o app ntfy e inscreva o tópico do `.env`. O estado (o que já foi avisado) fica em `~/.local/state/puma-ct-agua/alerta.json`. Se o próprio ryzen7 cair, nenhum aviso sai.

## Atualizações manuais

- **Novo semestre:** acrescente em `data/calendario.json` as datas do calendário da UFSM.
- **Nova ocupação:** `python3 exportar.py --ocupacao Perfil_Ocupacao_Predio_MeiaHora.xlsx --semestre "2º/2025"`.
- **Ver localmente:** `python3 -m http.server`, depois abra http://localhost:8000.

`exportar.py` e `alerta.py` só usam a biblioteca padrão do Python. As credenciais da Spinon ficam no `~/.config/puma-ct-agua.env` e nunca entram neste repositório.

## Cache do navegador (embed no Notion)

O GitHub Pages serve os arquivos com `max-age=600` e o embed do Notion não tem como forçar recarga. Por isso `index.html` carrega `app.js?v=<versão>` e `style.css?v=<versão>`: **ao alterar `app.js` ou `style.css`, troque esse `v=` no `index.html`** (o hash curto do commit serve). Os dados (`data/*.json`) já são buscados sem cache.
