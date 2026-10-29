// Painel público do Hidrômetro 1 (PUMA/CT). Lê data/*.json, agrega no navegador
// e reconsulta data/meta.json a cada 10 min para pegar a hora nova sem recarregar.
"use strict";

const PREDIO = "Prédio 9C";
const LIMITE_ATRASO_H = 6;            // o mesmo MAX_ATRASO_HORAS do download_auto2.py
const INTERVALO_CONSULTA_MS = 10 * 60 * 1000;
const H = 3600 * 1000;
const FUSO_MS = -3 * H;               // America/Sao_Paulo, sem horário de verão

const DIAS = ["Dom", "Seg", "Ter", "Qua", "Qui", "Sex", "Sáb"];
const DIAS_LONGOS = ["Domingo", "Segunda-feira", "Terça-feira", "Quarta-feira", "Quinta-feira", "Sexta-feira", "Sábado"];
const MESES = ["jan", "fev", "mar", "abr", "mai", "jun", "jul", "ago", "set", "out", "nov", "dez"];
const MESES_LONGOS = ["Janeiro", "Fevereiro", "Março", "Abril", "Maio", "Junho", "Julho", "Agosto", "Setembro", "Outubro", "Novembro", "Dezembro"];

const num = (x, d = 2) => x == null ? "–" : x.toLocaleString("pt-BR", { minimumFractionDigits: d, maximumFractionDigits: d });
const m3 = (x, d = 2) => x == null ? "sem dado" : `${num(x, d)} m³`;
const pad = n => String(n).padStart(2, "0");

// Data "de parede" em Santa Maria, independente do fuso de quem abre a página.
function local(ms) {
  const d = new Date(ms + FUSO_MS);
  return { a: d.getUTCFullYear(), m: d.getUTCMonth(), d: d.getUTCDate(), h: d.getUTCHours(), sem: d.getUTCDay() };
}
const isoDia = l => `${l.a}-${pad(l.m + 1)}-${pad(l.d)}`;
const brDia = l => `${pad(l.d)}/${pad(l.m + 1)}/${l.a}`;
const msDeIso = iso => Date.parse(iso + "T00:00:00-03:00");

const estado = { meta: null, horas: [], ocupacao: null, calendario: null, graficos: {}, aba: null, perfilDia: 1, ocupDia: "Seg", semestre: null };

// ---------- tema ----------
function tokens() {
  const s = getComputedStyle(document.documentElement);
  const v = n => s.getPropertyValue(n).trim();
  return { serie: v("--series-1"), texto: v("--text-primary"), texto2: v("--text-secondary"), mudo: v("--text-muted"),
           grade: v("--grid"), sup: v("--surface-1"), sup2: v("--surface-2"), borda: v("--border") };
}
function lerTema() { try { return localStorage.getItem("tema"); } catch { return null; } }
function salvarTema(t) { try { localStorage.setItem("tema", t); } catch { /* sem armazenamento: só não lembra */ } }
function aplicarTema(t) { if (t) document.documentElement.dataset.theme = t; }

// ---------- dados ----------
async function buscar(caminho, versao) {
  const r = await fetch(`${caminho}?v=${encodeURIComponent(versao)}`, { cache: "no-store" });
  if (!r.ok) throw new Error(`${caminho}: HTTP ${r.status}`);
  return r.json();
}

function montarHoras(consumo) {
  const t0 = Date.parse(consumo.inicio);
  const passo = (consumo.passo_h || 1) * H;
  return consumo.v.map((v, i) => { const ms = t0 + i * passo; return { ms, v, l: local(ms) }; });
}

function agrupar(horas, chave, rotulo) {
  const mapa = new Map();
  for (const x of horas) {
    const k = chave(x);
    let g = mapa.get(k);
    if (!g) { g = { k, rot: rotulo(x), soma: 0, n: 0, ms: x.ms }; mapa.set(k, g); }
    if (x.v != null) { g.soma += x.v; g.n++; }
  }
  return [...mapa.values()].map(g => ({ ...g, v: g.n ? g.soma : null }));
}

const porDia = () => agrupar(estado.horas, x => isoDia(x.l), x => `${pad(x.l.d)} ${MESES[x.l.m]} ${x.l.a}`);
const porMes = () => agrupar(estado.horas, x => `${x.l.a}-${pad(x.l.m + 1)}`, x => `${MESES[x.l.m]}/${x.l.a}`);
function porSemana() {
  // Semana de segunda a domingo, como em grafico2.py (Week Start = Time - weekday).
  return agrupar(estado.horas,
    x => { const desloc = (x.l.sem + 6) % 7; return isoDia(local(x.ms - desloc * 24 * H)); },
    x => { const desloc = (x.l.sem + 6) % 7; const ini = local(x.ms - desloc * 24 * H), fim = local(x.ms + (6 - desloc) * 24 * H);
           return `${pad(ini.d)}/${pad(ini.m + 1)} – ${pad(fim.d)}/${pad(fim.m + 1)}/${fim.a}`; });
}

// ---------- gráficos ----------
function grafico(id) {
  if (!estado.graficos[id]) {
    const el = document.getElementById(id);
    estado.graficos[id] = echarts.init(el, null, { renderer: "canvas" });
  }
  return estado.graficos[id];
}

// Eixo de tempo do ECharts formata na hora de quem abre a página; deslocamos os
// instantes para que o rótulo mostre a hora de Santa Maria em qualquer fuso.
const paraEixo = ms => ms + FUSO_MS + new Date(ms).getTimezoneOffset() * 60000;
function rotuloTempo(v) {
  const d = new Date(v);
  if (d.getHours() === 0 && d.getDate() === 1) return `${MESES[d.getMonth()]}/${d.getFullYear()}`;
  if (d.getHours() === 0) return `${pad(d.getDate())}/${pad(d.getMonth() + 1)}`;
  return `${pad(d.getHours())}h`;
}

function baseBarras(t, { rotulos, valores, unidade, nomeY, zoom, rotuloBarra, tooltipRot, largas, tempos }) {
  const op = {
    animation: false,
    textStyle: { fontFamily: "system-ui, -apple-system, Segoe UI, Roboto, sans-serif" },
    grid: { left: 8, right: 28, top: 28, bottom: zoom ? 64 : 24, containLabel: true },
    tooltip: {
      trigger: "axis", axisPointer: { type: "shadow", shadowStyle: { color: t.grade, opacity: .5 } },
      backgroundColor: t.sup, borderColor: t.borda, textStyle: { color: t.texto, fontSize: 13 },
      formatter: p => { const i = p[0].dataIndex; return `<b>${tooltipRot ? tooltipRot(i) : rotulos[i]}</b><br>${valores[i] == null ? "sem dado" : num(valores[i], unidade.d) + " " + unidade.u}`; },
    },
    xAxis: {
      type: "category", data: rotulos,
      axisLine: { lineStyle: { color: t.borda } }, axisTick: { show: false },
      axisLabel: { color: t.texto2, hideOverlap: true, fontSize: 11 },
    },
    yAxis: {
      type: "value", name: nomeY, nameTextStyle: { color: t.texto2, align: "left", fontSize: 12 },
      axisLabel: { color: t.texto2, fontSize: 11, formatter: v => num(v, v < 10 && v % 1 ? 1 : 0) },
      splitLine: { lineStyle: { color: t.grade } },
    },
    dataZoom: zoom ? [
      { type: "inside", ...zoom },
      { type: "slider", ...zoom, height: 26, bottom: 10, borderColor: t.borda, fillerColor: "rgba(127,127,127,.15)",
        textStyle: { color: t.mudo }, dataBackground: { lineStyle: { color: t.mudo, opacity: .6 }, areaStyle: { color: t.mudo, opacity: .15 } },
        labelFormatter: i => rotulos[i] ?? "" },
    ] : [],
    series: [{
      type: "bar", data: valores, large: largas, largeThreshold: 2000,
      itemStyle: { color: t.serie, borderRadius: largas ? 0 : [4, 4, 0, 0] },
      barCategoryGap: "18%",
      label: rotuloBarra ? { show: true, position: "top", color: t.texto2, fontSize: 11, formatter: p => p.value == null ? "" : num(p.value, 2) } : undefined,
      labelLayout: { hideOverlap: true },
      emphasis: { itemStyle: { color: t.texto2 } },
    }],
  };
  if (tempos) {
    const xs = tempos.map(paraEixo);
    op.xAxis = { ...op.xAxis, type: "time", data: undefined, axisLabel: { ...op.xAxis.axisLabel, formatter: rotuloTempo } };
    op.series[0].data = valores.map((v, i) => [xs[i], v]);
    op.series[0].barMaxWidth = 14;
    op.dataZoom.forEach(z => { delete z.labelFormatter; z.labelFormatter = v => rotuloTempo(v); });
  }
  return op;
}

// Atalhos de período acima de cada gráfico com zoom (o "Selecionar período" do original).
const ATALHOS = {
  horario: [["48 h", 48], ["7 dias", 24 * 7], ["30 dias", 24 * 30], ["Tudo", 0]],
  diario: [["30 dias", 30], ["90 dias", 90], ["1 ano", 365], ["Tudo", 0]],
  semanal: [["6 meses", 26], ["1 ano", 52], ["Tudo", 0]],
  mensal: [["12 meses", 12], ["Tudo", 0]],
};
const PADRAO = { horario: 24 * 7, diario: 90, semanal: 0, mensal: 0 };
const janela = {};

function montarAtalhos(aba) {
  const caixa = document.querySelector(`[data-filtros="${aba}"]`);
  const chips = document.createElement("div");
  chips.className = "chips"; chips.setAttribute("role", "group"); chips.setAttribute("aria-label", "Período");
  for (const [rot, n] of ATALHOS[aba]) {
    const b = document.createElement("button");
    b.type = "button"; b.textContent = rot; b.dataset.n = n;
    b.addEventListener("click", () => { janela[aba] = n; desenhar(aba); });
    chips.append(b);
  }
  caixa.append(chips);
}
function marcarAtalho(aba) {
  document.querySelectorAll(`[data-filtros="${aba}"] button`).forEach(b => b.setAttribute("aria-pressed", String(+b.dataset.n === janela[aba])));
}
const zoomDe = (aba, total) => { const n = janela[aba]; return n && n < total ? { startValue: total - n, endValue: total - 1 } : { start: 0, end: 100 }; };
// Em eixo de tempo o zoom é em instantes, não em índices.
const zoomTempo = (aba, ms) => { const n = janela[aba]; return n && n < ms.length ? { startValue: paraEixo(ms[ms.length - n]), endValue: paraEixo(ms.at(-1)) } : { start: 0, end: 100 }; };

function desenharHorario(t) {
  const h = estado.horas;
  const ms = h.map(x => x.ms);
  grafico("g-horario").setOption(baseBarras(t, {
    rotulos: [], valores: h.map(x => x.v), unidade: { u: "m³/h", d: 3 }, nomeY: "Consumo (m³/h)",
    zoom: zoomTempo("horario", ms), largas: true, tempos: ms,
    tooltipRot: i => `${DIAS[h[i].l.sem]} ${brDia(h[i].l)} · ${pad(h[i].l.h)}:00–${pad((h[i].l.h + 1) % 24)}:00`,
  }), true);
}
function desenharDiario(t) {
  const d = porDia();
  grafico("g-diario").setOption(baseBarras(t, {
    rotulos: [], valores: d.map(x => x.v), unidade: { u: "m³", d: 2 }, nomeY: "Consumo (m³/dia)",
    zoom: zoomTempo("diario", d.map(x => msDeIso(x.k))), tempos: d.map(x => msDeIso(x.k)),
    tooltipRot: i => `${DIAS_LONGOS[local(d[i].ms).sem]}, ${brDia(local(d[i].ms))}${d[i].n < 24 ? ` · ${d[i].n} de 24 h com dado` : ""}`,
  }), true);
}
function desenharSemanal(t) {
  const s = porSemana();
  grafico("g-semanal").setOption(baseBarras(t, {
    rotulos: s.map(x => x.rot.slice(0, 5) + "/" + x.rot.slice(-4)), valores: s.map(x => x.v), unidade: { u: "m³", d: 1 }, nomeY: "Consumo (m³/semana)",
    zoom: zoomDe("semanal", s.length), tooltipRot: i => `Semana ${s[i].rot}${s[i].n < 168 ? ` · ${s[i].n} de 168 h com dado` : ""}`,
  }), true);
}
function desenharMensal(t) {
  const m = porMes();
  grafico("g-mensal").setOption(baseBarras(t, {
    rotulos: m.map(x => x.rot), valores: m.map(x => x.v), unidade: { u: "m³", d: 2 }, nomeY: "Consumo (m³/mês)",
    zoom: zoomDe("mensal", m.length),
    // Valor em cima da barra só quando cabe; no celular fica no tooltip e na tabela.
    rotuloBarra: document.getElementById("g-mensal").offsetWidth / Math.min(janela.mensal || m.length, m.length) >= 38,
    tooltipRot: i => { const l = local(m[i].ms); return `${MESES_LONGOS[l.m]} de ${l.a}`; },
  }), true);
  const corpo = document.querySelector("#t-mensal tbody");
  corpo.replaceChildren(...m.slice().reverse().map(x => {
    const tr = document.createElement("tr"); const l = local(x.ms);
    for (const txt of [`${MESES_LONGOS[l.m]} de ${l.a}`, num(x.v, 2)]) { const td = document.createElement("td"); td.textContent = txt; tr.append(td); }
    return tr;
  }));
}

// ---------- perfil de consumo (média de cada hora sobre todos os dias daquele dia da semana) ----------
// Períodos: semestres civis (1º = jan–jun, 2º = jul–dez) ou a série inteira; sem filtro de letivo/feriado.
function faixaSemestre(semestre) {
  const [n, a] = semestre.split("º/").map(Number);
  return [msDeIso(`${a}-${n === 1 ? "01-01" : "07-01"}`), msDeIso(`${n === 1 ? a : a + 1}-${n === 1 ? "07-01" : "01-01"}`)];
}

function perfil(semestre, diaSem) {
  const [a, b] = semestre === "todos" ? [-Infinity, Infinity] : faixaSemestre(semestre);
  const soma = Array(24).fill(0), n = Array(24).fill(0), dias = new Set();
  for (const x of estado.horas) {
    if (x.v == null || x.l.sem !== diaSem || x.ms < a || x.ms >= b) continue;
    soma[x.l.h] += x.v; n[x.l.h]++; dias.add(isoDia(x.l));
  }
  return { media: soma.map((s, i) => n[i] ? s / n[i] : null), nDias: dias.size };
}

function semestresComDado() {
  if (!estado.horas.length) return [];
  const lista = [];
  for (const x of [estado.horas[0], estado.horas.at(-1)]) lista.push(`${x.l.m < 6 ? 1 : 2}º/${x.l.a}`);
  const out = [];
  for (let [n, a] = lista[0].split("º/").map(Number); ; n === 1 ? n = 2 : (n = 1, a++)) {
    out.push(`${n}º/${a}`);
    if (`${n}º/${a}` === lista[1]) return out;
  }
}

function montarPerfil() {
  const sel = document.getElementById("perfil-semestre");
  const lista = semestresComDado();
  if (!estado.semestre) estado.semestre = lista.includes("1º/2025") ? "1º/2025" : lista.at(-1);
  sel.replaceChildren(...lista.map(s => new Option(s, s, false, s === estado.semestre)), new Option("Todo o período", "todos", false, estado.semestre === "todos"));
  sel.onchange = () => { estado.semestre = sel.value; desenhar("perfil"); };
  const chips = document.getElementById("perfil-dias");
  if (!chips.children.length) {
    for (const d of [1, 2, 3, 4, 5, 6, 0]) {
      const b = document.createElement("button"); b.type = "button"; b.textContent = DIAS[d]; b.dataset.d = d;
      b.addEventListener("click", () => { estado.perfilDia = d; desenhar("perfil"); });
      chips.append(b);
    }
  }
}

function desenharPerfil(t) {
  const { media, nDias } = perfil(estado.semestre, estado.perfilDia);
  document.querySelectorAll("#perfil-dias button").forEach(b => b.setAttribute("aria-pressed", String(+b.dataset.d === estado.perfilDia)));
  document.getElementById("perfil-rotulo").textContent = `${DIAS_LONGOS[estado.perfilDia]} · ${estado.semestre === "todos" ? "todo o período" : estado.semestre}`;
  document.getElementById("perfil-nota").textContent = nDias
    ? `Média de cada hora sobre ${nDias} ${DIAS_LONGOS[estado.perfilDia].toLowerCase()}s do período (todos os dias, incluindo férias e feriados). 1º semestre = jan–jun; 2º = jul–dez.`
    : "Sem dados para esse dia no período escolhido.";
  const rot = media.map((_, h) => `${pad(h)}:00`);
  const pico = media.reduce((im, v, i) => v != null && (media[im] == null || v > media[im]) ? i : im, 0);
  const op = baseBarras(t, { rotulos: rot, valores: media, unidade: { u: "m³/h", d: 3 }, nomeY: "Consumo médio (m³/h)",
    tooltipRot: i => `${DIAS_LONGOS[estado.perfilDia]}, ${rot[i]}–${pad((i + 1) % 24)}:00` });
  // Rótulo só no pico, para não pôr número em todas as barras.
  op.series[0].label = { show: true, position: "top", color: t.texto2, fontSize: 11, formatter: p => p.dataIndex === pico && p.value != null ? `pico ${num(p.value, 2)}` : "" };
  // Mesma escala nos 7 dias do período (pico da semana), para comparar um dia com outro.
  const maxSemana = Math.max(0, ...[0, 1, 2, 3, 4, 5, 6].flatMap(d => perfil(estado.semestre, d).media.filter(v => v != null)));
  if (maxSemana > 0) op.yAxis = { ...op.yAxis, max: Math.ceil(maxSemana * 1.1 * 20) / 20 };
  grafico("g-perfil").setOption(op, true);
}

// ---------- perfil de ocupação ----------
function montarOcupacao() {
  document.getElementById("ocup-semestre").textContent = estado.ocupacao.semestre;
  const chips = document.getElementById("ocup-dias");
  if (chips.children.length) return;
  for (const d of Object.keys(estado.ocupacao.dias)) {
    const b = document.createElement("button"); b.type = "button"; b.textContent = d; b.dataset.d = d;
    b.addEventListener("click", () => { estado.ocupDia = d; desenhar("ocupacao"); });
    chips.append(b);
  }
}
function desenharOcupacao(t) {
  const linhas = estado.ocupacao.dias[estado.ocupDia].filter(x => x.h >= "06:00" && x.h <= "21:30");
  document.querySelectorAll("#ocup-dias button").forEach(b => b.setAttribute("aria-pressed", String(b.dataset.d === estado.ocupDia)));
  const longo = DIAS_LONGOS[DIAS.indexOf(estado.ocupDia)];
  document.getElementById("ocup-rotulo").textContent = longo;
  grafico("g-ocupacao").setOption(baseBarras(t, {
    rotulos: linhas.map(x => x.h), valores: linhas.map(x => x.n), unidade: { u: "ocupantes", d: 0 }, nomeY: "Ocupantes",
    tooltipRot: i => `${longo}, ${linhas[i].h}`,
  }), true);
}

// ---------- KPIs e status ----------
function somaJanela(ini, fim) { let s = 0; for (const x of estado.horas) if (x.ms >= ini && x.ms < fim && x.v != null) s += x.v; return s; }
function atualizarResumo() {
  const h = estado.horas; if (!h.length) return;
  const ultimo = h.at(-1).ms + H; // fim da última hora fechada
  const lu = local(h.at(-1).ms);
  document.getElementById("k-24h").textContent = m3(somaJanela(ultimo - 24 * H, ultimo), 2);
  document.getElementById("k-7d").textContent = m3(somaJanela(ultimo - 7 * 24 * H, ultimo), 1);
  document.getElementById("k-mes-rot").textContent = `${MESES_LONGOS[lu.m]} de ${lu.a}`;
  document.getElementById("k-mes").textContent = m3(somaJanela(msDeIso(`${lu.a}-${pad(lu.m + 1)}-01`), ultimo), 1);
  document.getElementById("k-total").textContent = m3(estado.meta.total_m3, 0);

  const st = document.getElementById("status");
  const atrasoH = (Date.now() - ultimo) / H;
  const lf = local(ultimo);
  const ger = local(Date.parse(estado.meta.gerado_em));
  st.textContent = `Dados até ${brDia(lf)} ${pad(lf.h)}:00 · atualizado em ${brDia(ger)} ${pad(ger.h)}:${pad(new Date(Date.parse(estado.meta.gerado_em)).getUTCMinutes())}`;
  st.className = "status " + (atrasoH > LIMITE_ATRASO_H ? "velho" : "ok");
  if (atrasoH > LIMITE_ATRASO_H) st.textContent += ` · medidor sem comunicação há ${Math.floor(atrasoH)} h`;
}

// ---------- abas ----------
const DESENHOS = { horario: desenharHorario, diario: desenharDiario, semanal: desenharSemanal, mensal: desenharMensal, perfil: desenharPerfil, ocupacao: desenharOcupacao };
function desenhar(aba) {
  if (!DESENHOS[aba] || !estado.horas.length) return;
  if (aba in ATALHOS) marcarAtalho(aba);
  DESENHOS[aba](tokens());
}
function mostrar(aba) {
  if (!document.querySelector(`[data-painel="${aba}"]`)) aba = "hidrometro";
  estado.aba = aba;
  document.querySelectorAll(".abas button").forEach(b => b.setAttribute("aria-selected", String(b.dataset.aba === aba)));
  document.querySelectorAll(".painel").forEach(p => p.classList.toggle("ativo", p.dataset.painel === aba));
  desenhar(aba);
  redimensionarVisiveis();
}
// Só os gráficos da aba aberta: um resize com o contêiner oculto (largura 0)
// deixa o ECharts calculando marcas de eixo sem fim e trava a página.
function redimensionarVisiveis() {
  Object.values(estado.graficos).forEach(g => { if (g.getDom().offsetWidth > 0) g.resize(); });
}

// ---------- carga e atualização ----------
async function carregar(primeira) {
  const meta = await buscar("data/meta.json", Date.now());
  if (!primeira && estado.meta && meta.gerado_em === estado.meta.gerado_em) { atualizarResumo(); return; }
  const [consumo, ocupacao, calendario] = await Promise.all([
    buscar("data/consumo.json", meta.gerado_em),
    primeira ? buscar("data/ocupacao.json", meta.gerado_em) : estado.ocupacao,
    primeira ? buscar("data/calendario.json", meta.gerado_em) : estado.calendario,
  ]);
  Object.assign(estado, { meta, ocupacao, calendario, horas: montarHoras(consumo) });
  if (primeira) { montarOcupacao(); }
  montarPerfil();
  atualizarResumo();
  desenhar(estado.aba);
}

function iniciar() {
  document.querySelectorAll("[data-predio]").forEach(e => e.textContent = PREDIO);
  aplicarTema(lerTema());
  document.getElementById("tema").addEventListener("click", () => {
    const escuro = getComputedStyle(document.documentElement).colorScheme === "dark";
    const novo = escuro ? "light" : "dark";
    aplicarTema(novo); salvarTema(novo); desenhar(estado.aba);
  });
  matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => desenhar(estado.aba));

  for (const aba of Object.keys(ATALHOS)) { janela[aba] = PADRAO[aba]; montarAtalhos(aba); }
  document.querySelectorAll(".abas button").forEach(b => b.addEventListener("click", () => { history.replaceState(null, "", "#" + b.dataset.aba); mostrar(b.dataset.aba); }));
  addEventListener("hashchange", () => mostrar(location.hash.slice(1)));
  addEventListener("resize", redimensionarVisiveis);
  mostrar(location.hash.slice(1) || "hidrometro");

  carregar(true).catch(e => {
    const st = document.getElementById("status");
    st.textContent = "Não foi possível carregar os dados. Tente recarregar a página."; st.className = "status velho";
    console.error(e);
  });
  setInterval(() => carregar(false).catch(console.error), INTERVALO_CONSULTA_MS);
  document.addEventListener("visibilitychange", () => { if (!document.hidden) carregar(false).catch(console.error); });
}

iniciar();
