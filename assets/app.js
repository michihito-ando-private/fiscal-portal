const CATEGORY_LABELS = {
  expenditure: "歳出",
  revenue: "歳入",
  international: "国際",
  research: "論文・レポート",
  government: "政府資料",
  digest: "今日のまとめ",
};

// カテゴリごとのサブカテゴリ（テーマ／種別）定義
const SUBCATEGORIES = {
  expenditure: {
    social_security: "社会保障",
    education: "教育",
    public_works: "公共事業・インフラ",
    defense: "防衛",
    local_gov: "地方財政",
    budget_general: "予算全般",
  },
  revenue: {
    tax: "税",
    insurance_premium: "保険料",
    gov_bond: "国債・金利",
  },
  international: {
    intl_comparison: "国際比較",
    sweden: "スウェーデン",
    germany: "ドイツ",
    france: "フランス",
    uk: "イギリス",
    usa: "アメリカ",
    korea: "韓国",
    taiwan: "台湾",
    intl_other: "その他",
  },
  research: {
    paper_en: "英語論文",
    paper_ja: "日本語論文",
    intl_report: "国際機関レポート",
    report_ja: "日本語レポート",
  },
  government: {
    cao: "内閣府",
    mof: "財務省",
    mhlw: "厚生労働省",
    soumu: "総務省",
    mext: "文部科学省",
    cfa: "こども家庭庁",
    gov_other: "その他省庁",
  },
};

// サブカテゴリ行の見出し
const SUBTAB_HEADINGS = { research: "種別:", government: "省庁:" };

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

function subLabel(article) {
  const subs = SUBCATEGORIES[article.category];
  return (subs && subs[article.subcategory]) || null;
}

function render() {
  const list = document.getElementById("article-list");
  const empty = document.getElementById("empty-message");
  const q = currentQuery.trim().toLowerCase();

  const filtered = allArticles.filter((a) => {
    if (currentCategory !== "all" && a.category !== currentCategory) return false;
    if (
      currentCategory !== "all" &&
      currentSubcategory !== "all" &&
      a.subcategory !== currentSubcategory
    ) return false;
    if (!q) return true;
    const sub = subLabel(a) || "";
    const haystack = [a.title, a.summary, a.source, sub, ...(a.tags || [])].join(" ").toLowerCase();
    return haystack.includes(q);
  });

  filtered.sort((a, b) => (a.date < b.date ? 1 : -1));

  list.innerHTML = filtered
    .map((a) => {
      const sub = subLabel(a);
      return `
    <article class="card cat-${a.category}">
      <div class="card-meta">
        <span class="badge badge-${a.category}">${CATEGORY_LABELS[a.category] || a.category}</span>
        ${sub ? `<span class="sub-label sub-${a.category}">${sub}</span>` : ""}
        <time datetime="${a.date}">${formatDate(a.date)}</time>
      </div>
      <h2 class="card-title">${
        a.url
          ? `<a href="${a.url}" target="_blank" rel="noopener noreferrer">${a.title}</a>`
          : a.title
      }</h2>
      <p class="card-summary">${a.summary}</p>
      <div class="card-footer">
        <span class="card-source">${a.source ? `出典: ${a.source}` : ""}</span>
        <span class="tag-list">${(a.tags || []).map((t) => `<span class="tag" data-tag="${t}">#${t}</span>`).join("")}</span>
      </div>
    </article>`;
    })
    .join("");

  empty.hidden = filtered.length > 0;
}

function renderSubtabs() {
  const box = document.getElementById("subtabs");
  const subs = SUBCATEGORIES[currentCategory];
  if (currentCategory === "all" || !subs) {
    box.hidden = true;
    box.innerHTML = "";
    return;
  }
  box.className = `container subtabs subtabs-${currentCategory}`;
  const heading = SUBTAB_HEADINGS[currentCategory] || "テーマ:";
  box.innerHTML =
    `<span class="subtabs-label">${heading}</span>` +
    `<button class="subtab active" data-subcategory="all">すべて</button>` +
    Object.entries(subs)
      .map(([key, label]) => `<button class="subtab" data-subcategory="${key}">${label}</button>`)
      .join("");
  box.hidden = false;
  box.querySelectorAll(".subtab").forEach((subtab) => {
    subtab.addEventListener("click", () => {
      box.querySelectorAll(".subtab").forEach((s) => s.classList.remove("active"));
      subtab.classList.add("active");
      currentSubcategory = subtab.dataset.subcategory;
      render();
    });
  });
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
    currentSubcategory = "all";
    renderSubtabs();
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
