// 日本経済ダッシュボード：data/japan.json を描画する
const COLORS = ["#a83a4e", "#2563a8", "#1e7f4f", "#b06a1f", "#7c4dab", "#0f766e"];

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

function formatDate(iso) {
  const d = new Date(iso + "T00:00:00");
  return isNaN(d) ? iso : `${d.getFullYear()}年${d.getMonth() + 1}月${d.getDate()}日`;
}

function renderCards(cards) {
  document.getElementById("kpi-grid").innerHTML = cards.map((c) => `
    <div class="kpi">
      <div class="kpi-label">${esc(c.label)}</div>
      <div class="kpi-value">${esc(c.value)}<span class="kpi-unit">${esc(c.unit)}</span></div>
      <div class="kpi-sub">${esc(c.sub)}</div>
      <div class="kpi-date">${esc(c.date)}</div>
    </div>`).join("");
}

function renderQuestions(qs) {
  document.getElementById("q-grid").innerHTML = qs.map((q) => `
    <div class="q-card">
      <h3>${esc(q.q)}</h3>
      <table class="q-table"><tbody>
        ${q.rows.map((r) => `<tr><th scope="row">${esc(r[0])}</th><td class="num">${esc(r[1])}</td><td class="q-date">${esc(r[2])}</td></tr>`).join("")}
      </tbody></table>
      <p class="q-note">${esc(q.note)}</p>
      <a class="q-link" href="${esc(q.link)}">詳しい表へ →</a>
    </div>`).join("");
}

function renderTables(tables) {
  document.getElementById("tables").innerHTML = tables.map((t) => `
    <section class="chart-section jp-table" id="${esc(t.id)}">
      <h2 class="chart-title">${esc(t.title)}</h2>
      <div class="table-scroll">
        <table class="source-table">
          <thead><tr>${t.columns.map((c, i) => `<th${i ? ' class="num"' : ""}>${esc(c)}</th>`).join("")}</tr></thead>
          <tbody>${t.rows.map((r) => `<tr><th scope="row">${esc(r[0])}</th>${r.slice(1).map((v) => `<td class="num">${esc(v)}</td>`).join("")}</tr>`).join("")}</tbody>
        </table>
      </div>
      <p class="table-note">${esc(t.note)}</p>
    </section>`).join("");
}

function monthTick(label) {
  // "2026-03" → "2026/3"、日次 "2026-03-05" → "2026/3"
  const m = /^(\d{4})-(\d{2})/.exec(label);
  return m ? `${m[1]}/${Number(m[2])}` : label;
}

function makeChart(c) {
  const host = document.getElementById("charts");
  const sec = document.createElement("section");
  sec.className = "chart-section";
  sec.innerHTML = `<h2 class="chart-title">${esc(c.title)}</h2><p class="chart-note">${esc(c.note)}</p><div class="chart-box"><canvas></canvas></div>`;
  host.appendChild(sec);

  const datasets = c.series.map((s, i) => ({
    label: s.name,
    type: s.type || "line",
    data: s.data,
    yAxisID: s.axis || "y",
    borderColor: COLORS[i % COLORS.length],
    backgroundColor: s.type === "bar" ? COLORS[i % COLORS.length] + "99" : COLORS[i % COLORS.length],
    borderWidth: s.type === "bar" ? 0 : 2,
    borderDash: s.dash ? [5, 4] : undefined,
    pointRadius: c.daily ? 0 : 1.5,
    pointHoverRadius: 4,
    tension: 0.15,
    spanGaps: true,
    order: s.type === "bar" ? 2 : 1,
  }));
  if (c.target != null) {
    datasets.push({
      label: `目標 ${c.target}%`, type: "line", data: c.labels.map(() => c.target),
      borderColor: "#8792a2", borderDash: [4, 4], borderWidth: 1.5, pointRadius: 0, pointHoverRadius: 0, order: 3,
    });
  }
  const hasY2 = c.series.some((s) => s.axis === "y2");
  const grid = "rgba(120,130,145,0.15)";
  const tick = "#5a6472";
  const zeroGrid = (ctx) => (c.zero && ctx.tick && ctx.tick.value === 0 ? "rgba(26,35,50,0.45)" : grid);

  new Chart(sec.querySelector("canvas"), {
    data: { labels: c.labels, datasets },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      interaction: { mode: "index", intersect: false },
      plugins: {
        legend: { position: "top", labels: { font: { family: "'Noto Sans JP', sans-serif" }, usePointStyle: true, boxWidth: 8 } },
        tooltip: {
          callbacks: {
            title: (items) => (items.length ? (c.daily ? formatDate(items[0].label) : items[0].label) : ""),
            label: (item) => `${item.dataset.label}: ${item.parsed.y != null ? item.parsed.y.toFixed(c.daily ? 3 : 2) : "—"}`,
          },
        },
      },
      scales: {
        x: {
          ticks: {
            color: tick, maxTicksLimit: 12, autoSkip: true, font: { size: 10 },
            callback: function (v) { return monthTick(this.getLabelForValue(v)); },
          },
          grid: { color: grid },
        },
        y: { position: "left", ticks: { color: tick, callback: (v) => v + (c.unit || "") }, grid: { color: zeroGrid } },
        ...(hasY2 ? { y2: { position: "right", ticks: { color: tick }, grid: { drawOnChartArea: false } } } : {}),
      },
    },
  });
}

async function init() {
  try {
    const res = await fetch("data/japan.json", { cache: "no-store" });
    const data = await res.json();
    document.getElementById("last-updated").textContent = formatDate(data.lastUpdated);
    renderCards(data.cards);
    renderQuestions(data.questions);
    data.charts.forEach(makeChart);
    renderTables(data.tables);
    document.getElementById("source-dates").textContent =
      Object.entries(data.sourceDates).map(([k, v]) => `${k}：${/^\d{4}-\d{2}-\d{2}$/.test(v) ? formatDate(v) : v}`).join("　／　");
  } catch (e) {
    const notice = document.getElementById("notice");
    notice.hidden = false;
    notice.textContent = "データの読み込みに失敗しました。時間をおいて再度お試しください。";
    console.error(e);
  }
}

init();
