// 市場データ（PER・利回り・イールドスプレッド）の可視化
const INDEX_LABELS = {
  Nikkei225: "日経平均",
  SP500: "S&P500",
  DowJones: "ダウ平均",
};

// サイトのカテゴリ色を流用（日本=えんじ、S&P500=青、ダウ=緑）
const INDEX_COLORS = {
  Nikkei225: "#a83a4e",
  SP500: "#2563a8",
  DowJones: "#1e7f4f",
};

function formatDate(iso) {
  const d = new Date(iso + "T00:00:00");
  if (isNaN(d)) return iso;
  return `${d.getFullYear()}年${d.getMonth() + 1}月${d.getDate()}日`;
}

// 全指数の日付を統合したソート済みラベル配列を作る（時系列カテゴリ軸）
function buildLabels(series) {
  const set = new Set();
  Object.values(series).forEach((pts) => pts.forEach((p) => set.add(p.date)));
  return [...set].sort();
}

// 指数の点を「日付→値」に引き直し、共通ラベルに合わせる（欠損は null で線を繋がない）
function toData(pts, labels, key) {
  const map = new Map(pts.map((p) => [p.date, p[key]]));
  return labels.map((d) => (map.has(d) ? map.get(d) : null));
}

function makeChart(canvasId, series, labels, key, unit) {
  const datasets = Object.keys(series).map((idx) => ({
    label: INDEX_LABELS[idx] || idx,
    data: toData(series[idx], labels, key),
    borderColor: INDEX_COLORS[idx],
    backgroundColor: INDEX_COLORS[idx],
    borderWidth: 2,
    pointRadius: 0,
    pointHoverRadius: 4,
    tension: 0.2,
    spanGaps: true,
  }));

  const dark = window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches;
  const gridColor = "rgba(120,130,145,0.15)";
  const tickColor = "#5a6472";

  return new Chart(document.getElementById(canvasId), {
    type: "line",
    data: { labels, datasets },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      interaction: { mode: "index", intersect: false },
      plugins: {
        legend: { position: "top", labels: { font: { family: "'Noto Sans JP', sans-serif" }, usePointStyle: true, boxWidth: 8 } },
        tooltip: {
          callbacks: {
            title: (items) => (items.length ? formatDate(items[0].label) : ""),
            label: (item) => `${item.dataset.label}: ${item.parsed.y != null ? item.parsed.y.toFixed(2) + unit : "—"}`,
          },
        },
      },
      scales: {
        x: {
          ticks: {
            color: tickColor,
            maxTicksLimit: 12,
            autoSkip: true,
            font: { size: 10 },
            callback: function (v) {
              const d = this.getLabelForValue(v);
              return d ? d.slice(0, 7) : d; // YYYY-MM
            },
          },
          grid: { color: gridColor },
        },
        y: {
          ticks: { color: tickColor, callback: (v) => v + unit },
          grid: { color: gridColor },
        },
      },
    },
  });
}

async function init() {
  try {
    const res = await fetch("data/market.json", { cache: "no-store" });
    const data = await res.json();
    document.getElementById("last-updated").textContent = formatDate(data.lastUpdated);
    const labels = buildLabels(data.series);
    makeChart("chart-ys", data.series, labels, "ys", "%");
    makeChart("chart-per", data.series, labels, "per", "倍");
    makeChart("chart-bond", data.series, labels, "bond", "%");
    makeChart("chart-ey", data.series, labels, "ey", "%");
  } catch (e) {
    const notice = document.getElementById("notice");
    notice.hidden = false;
    notice.textContent = "市場データの読み込みに失敗しました。時間をおいて再度お試しください。";
    console.error(e);
  }
}

init();
