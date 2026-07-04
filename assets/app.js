const CATEGORY_LABELS = {
  expenditure: "歳出",
  revenue: "歳入",
  international: "国際",
  research: "論文・レポート",
};

const SUBCATEGORY_LABELS = {
  paper_en: "英語論文",
  paper_ja: "日本語論文",
  intl_report: "国際機関レポート",
  report_ja: "日本語レポート",
};

let allArticles = [];
let currentCategory = "all";
let currentSubcategory = "all";
let currentQuery = "";

function formatDate(iso) {
  const d = new Date(iso + "T00:00:00");
  if (isNaN(d)) return iso;
  return `${d.getFullYear()}年${d.getMonth() + 1}月${d.getDate()}日`;
}

function formatDateTime(iso) {
  const d = new Date(iso);
  if (isNaN(d)) return iso;
  return `${d.getFullYear()}年${d.getMonth() + 1}月${d.getDate()}日 ${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
}

function render() {
  const list = document.getElementById("article-list");
  const empty = document.getElementById("empty-message");
  const q = currentQuery.trim().toLowerCase();

  const filtered = allArticles.filter((a) => {
    if (currentCategory !== "all" && a.category !== currentCategory) return false;
    if (
      currentCategory === "research" &&
      currentSubcategory !== "all" &&
      a.subcategory !== currentSubcategory
    ) return false;
    if (!q) return true;
    const haystack = [a.title, a.summary, a.source, ...(a.tags || [])].join(" ").toLowerCase();
    return haystack.includes(q);
  });

  filtered.sort((a, b) => (a.date < b.date ? 1 : -1));

  list.innerHTML = filtered
    .map(
      (a) => `
    <article class="card cat-${a.category}">
      <div class="card-meta">
        <span class="badge badge-${a.category}">${
          (a.category === "research" && SUBCATEGORY_LABELS[a.subcategory]) ||
          CATEGORY_LABELS[a.category] ||
          a.category
        }</span>
        <time datetime="${a.date}">${formatDate(a.date)}</time>
      </div>
      <h2 class="card-title"><a href="${a.url}" target="_blank" rel="noopener noreferrer">${a.title}</a></h2>
      <p class="card-summary">${a.summary}</p>
      <div class="card-footer">
        <span class="card-source">出典: ${a.source}</span>
        <span class="tag-list">${(a.tags || []).map((t) => `<span class="tag" data-tag="${t}">#${t}</span>`).join("")}</span>
      </div>
    </article>`
    )
    .join("");

  empty.hidden = filtered.length > 0;
}

async function init() {
  try {
    const res = await fetch("data/news.json", { cache: "no-store" });
    const data = await res.json();
    allArticles = data.articles || [];
    document.getElementById("last-updated").textContent = formatDateTime(data.lastUpdated);
    render();
  } catch (e) {
    const notice = document.getElementById("notice");
    notice.hidden = false;
    notice.textContent = "データの読み込みに失敗しました。時間をおいて再度お試しください。";
    console.error(e);
  }
}

document.querySelectorAll(".tab").forEach((tab) => {
  tab.addEventListener("click", () => {
    document.querySelectorAll(".tab").forEach((t) => t.classList.remove("active"));
    tab.classList.add("active");
    currentCategory = tab.dataset.category;
    // 論文・レポートタブのときだけサブカテゴリ絞り込みを表示
    document.getElementById("subtabs").hidden = currentCategory !== "research";
    currentSubcategory = "all";
    document.querySelectorAll(".subtab").forEach((s) => s.classList.remove("active"));
    document.querySelector('.subtab[data-subcategory="all"]').classList.add("active");
    render();
  });
});

document.querySelectorAll(".subtab").forEach((subtab) => {
  subtab.addEventListener("click", () => {
    document.querySelectorAll(".subtab").forEach((s) => s.classList.remove("active"));
    subtab.classList.add("active");
    currentSubcategory = subtab.dataset.subcategory;
    render();
  });
});

document.getElementById("search-box").addEventListener("input", (e) => {
  currentQuery = e.target.value;
  render();
});

document.getElementById("article-list").addEventListener("click", (e) => {
  const tag = e.target.closest(".tag");
  if (tag) {
    const box = document.getElementById("search-box");
    box.value = tag.dataset.tag;
    currentQuery = tag.dataset.tag;
    render();
  }
});

init();
