"""財政情報ポータルのニュースデータを Claude API (Web検索付き) で自動更新するスクリプト。

GitHub Actions から毎日実行される想定。ローカルでも
  ANTHROPIC_API_KEY=sk-... python scripts/update_news.py
で実行できる。
"""

import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import anthropic

JST = timezone(timedelta(hours=9))
DATA_PATH = Path(__file__).resolve().parent.parent / "data" / "news.json"
MAX_PER_CATEGORY = 40

CATEGORIES = {
    "expenditure": "日本の歳出関連ニュース（社会保障・教育・公共事業・防衛。国の予算だけでなく自治体の財政も含む）",
    "revenue": "日本の歳入関連ニュース（税収・社会保険料・国債の発行や金利・地方税）",
    "international": "財政・社会保障に関する国際ニュース（IMF・OECD等の国際機関、米国・欧州・アジア各国の財政動向）",
    "research": "財政・社会保障・教育に関する学術論文やレポート（下記のサブカテゴリに必ず分類する）",
    "government": (
        "日本の政府資料: 内閣府・財務省・厚生労働省・総務省・文部科学省・こども家庭庁などの省庁や"
        "その審議会が公表した一次資料（予算資料・審議会資料・白書・統計・制度改正資料・検証結果など）の解説とリンク。"
        "報道記事ではなく省庁サイト等の一次資料のURLを載せること"
    ),
    "stakeholder": (
        "社会保障・教育に関する利益団体・専門家集団・当事者団体の政策への主張・提言。"
        "政府以外のアクターが公表した提言書・意見書・要望書・声明・調査報告や、それらを報じたニュース。"
        "団体自身のサイトの一次資料を優先し、なければ報道記事でもよい"
    ),
}

# カテゴリごとのサブカテゴリ（テーマ／種別）。記事には必ずいずれかを付与する
SUBCATEGORIES = {
    "expenditure": {
        "social_security": "社会保障（医療・介護・年金・子育て・生活保護などの給付）",
        "education": "教育（教育無償化・就学支援・大学財政など）",
        "public_works": "公共事業・インフラ（国土強靱化・防災・老朽化対策など）",
        "defense": "防衛（防衛費・防衛財源など）",
        "local_gov": "地方財政（地方交付税・自治体の予算・財政難など）",
        "budget_general": "予算全般（予算編成・骨太方針・補正予算など分野横断のもの）",
    },
    "revenue": {
        "tax": "税（税収動向・税制改正・消費税・所得税・法人税・地方税など）",
        "insurance_premium": "保険料（社会保険料の料率・負担のあり方など）",
        "gov_bond": "国債・金利（国債発行・国債費・金利動向・財政健全化目標など）",
    },
    "international": {
        "intl_comparison": "国際比較（IMF・OECD等による多国間の比較・世界全体の財政動向）",
        "sweden": "スウェーデンの財政・社会保障・教育ニュース",
        "germany": "ドイツの財政・社会保障・教育ニュース",
        "france": "フランスの財政・社会保障・教育ニュース",
        "uk": "イギリスの財政・社会保障・教育ニュース",
        "usa": "アメリカの財政・社会保障・教育ニュース",
        "korea": "韓国の財政・社会保障・教育ニュース",
        "taiwan": "台湾の財政・社会保障・教育ニュース",
        "intl_other": "その他の国・地域の財政・社会保障ニュース（特に重要なもののみ）",
    },
    "research": {
        "paper_en": (
            "英語論文: 教育・社会保障（年金・医療・介護・所得保障）・財政を主題とする経済学の英語論文。"
            "対象は (a) 主要学術誌 = 5大誌 (American Economic Review, Quarterly Journal of Economics, "
            "Journal of Political Economy, Econometrica, Review of Economic Studies)、"
            "2nd tier誌 (AEJ: Applied Economics, AEJ: Economic Policy, Review of Economics and Statistics, "
            "Journal of the European Economic Association, Economic Journal など)、"
            "トップフィールド誌 (Journal of Public Economics, Journal of Health Economics, "
            "Journal of Labor Economics, Journal of Human Resources, Economics of Education Review など)、"
            "(b) 有力ワーキングペーパー = NBER, IZA, IFAU, CEPR, 有力大学（Harvard, MIT, Stanford, "
            "Princeton, LSE等）のワーキングペーパー。タイトルは英語原題のまま、要約は日本語で書く"
        ),
        "paper_ja": (
            "日本語論文: 財政・社会保障・教育に関する日本語の学術論文"
            "（『経済分析』『季刊社会保障研究』『社会保障研究』『財政研究』『日本経済研究』『経済研究』、"
            "RIETI・ESRI等のディスカッションペーパーなど）"
        ),
        "intl_report": "国際機関レポート: IMF・OECD・世界銀行・ILO・EU等の国際機関が公表した財政・社会保障関連のレポートや報告書",
        "report_ja": (
            "日本語レポート: 日本の民間シンクタンク（ニッセイ基礎研・大和総研・第一生命経済研・野村総研等）の日本語レポート"
            "（省庁・審議会の資料は government カテゴリへ）。"
            "**定点観測: ニッセイ基礎研究所の野村彰宏（経済研究部 主任研究員、内閣府・財務省主計局出身）のレポートは毎回チェックし、"
            "新しいものがあれば優先的に収録する。骨太方針・予算編成改革・投資枠・中長期試算・PB目標などのテーマで月数本執筆。"
            "検索例: 「野村彰宏 ニッセイ基礎研究所 レポート 財政」**"
        ),
    },
    "stakeholder": {
        "medical": (
            "医療・介護団体: 日本医師会、日本歯科医師会、日本薬剤師会、日本看護協会、"
            "四病院団体協議会・日本病院会・全日本病院協会などの病院団体、日本医療法人協会、"
            "全国老人保健施設協会・全国老人福祉施設協議会・日本介護支援専門員協会などの介護関係団体、"
            "日本製薬工業協会（製薬業界）"
        ),
        "insurer": (
            "保険者・年金関係: 健康保険組合連合会（健保連）、全国健康保険協会（協会けんぽ）、"
            "国民健康保険中央会、後期高齢者医療広域連合、企業年金連合会、"
            "国民年金基金連合会、生命保険協会など"
        ),
        "labor_mgmt": (
            "労使団体: 日本経済団体連合会（経団連）、日本商工会議所、経済同友会、全国中小企業団体中央会などの経営者団体、"
            "日本労働組合総連合会（連合）、全労連、産業別労組（自治労・日教組・UAゼンセン等）などの労働団体。"
            "社会保障・教育の負担や制度設計に関する提言"
        ),
        "education": (
            "教育団体: 日本私立中学高等学校連合会、日本私立大学連盟・日本私立大学協会、国立大学協会、"
            "全国知事会・全国市長会・全国町村会の教育部門、日本教育学会、PTA全国協議会、"
            "教職員団体（日教組・全教）など教育政策・教育費に関する提言"
        ),
        "citizen": (
            "当事者・市民団体: 患者団体（全国がん患者団体連合会・日本難病疾病団体協議会など）、"
            "障害者団体（日本障害者協議会・きょうされんなど）、高齢者団体、"
            "子ども・子育て支援団体（しんぐるまざあず・ふぉーらむ、こども食堂ネットワーク等）、"
            "貧困・生活保護問題に取り組む市民団体、年金者組合など"
        ),
        "academic": (
            "学会・専門家集団: 日本財政学会、日本経済学会、社会政策学会、日本社会保障法学会、"
            "日本公衆衛生学会、日本老年医学会、日本教育学会などの学会や、"
            "研究者有志による共同提言・声明（大学教員グループの意見書など）"
        ),
        "stake_other": (
            "その他の団体: 上記に当てはまらない業界団体、士業団体（日本税理士会連合会・日本社会保険労務士会連合会など）、"
            "地方団体（全国知事会・全国市長会・全国町村会の社会保障関係の提言）、"
            "国際団体の日本支部、シンクタンク以外の民間アクターなど"
        ),
    },
    "government": {
        "cao": "内閣府（経済財政諮問会議・骨太方針・経済財政白書・中長期試算など）",
        "mof": "財務省（予算・決算資料、財政制度等審議会、日本の財政関係資料など）",
        "mhlw": "厚生労働省（社会保障審議会、診療報酬・介護報酬、年金財政検証、白書など）",
        "soumu": "総務省（地方財政計画、地方財政白書、統計など）",
        "mext": "文部科学省（教育予算、就学支援、科学技術予算など）",
        "cfa": "こども家庭庁（子育て支援、少子化対策の予算・資料など）",
        "gov_other": "その他の省庁・政府機関（国土交通省、防衛省、経済産業省、会計検査院など）",
    },
}

PROMPT_TEMPLATE = """あなたは日本の財政情報ポータルサイトの編集者です。Web検索を使って、{since}以降に公表された新しいニュース・レポートを収集し、日本語で要約してください。

対象カテゴリ:
{category_desc}

サブカテゴリ一覧（**すべての記事に、そのカテゴリのサブカテゴリを必ず1つ付けること**）:
{subcategory_desc}

既に掲載済みのURL（これらは除外すること）:
{known_urls}

要件:
- expenditure / revenue / government は各カテゴリ最大3件、international は最大5件、research はサブカテゴリごとに最大2件、stakeholder は最大4件。合計で最大26件まで。新しく重要なものを優先する。
- 省庁・審議会の一次資料は expenditure / revenue ではなく government に分類する。expenditure / revenue は報道記事を中心とする。
- **団体の主張・提言は stakeholder に分類する**。医師会・健保連・経団連・連合・患者団体・学会などが公表した提言書・意見書・要望書・声明や、それらを報じたニュースが対象。政府の審議会に団体が提出した資料も、団体側の主張が主眼なら stakeholder でよい。民間シンクタンクの分析レポートは research/report_ja のまま（提言団体とは区別する）。
- stakeholder では、医療・年金・介護・子育て・教育・雇用など**領域が分かるタグを必ず付ける**（例: "医療", "年金", "教育"）。団体名もタグに入れる（例: "日本医師会", "経団連"）。
- international は主要先進国（スウェーデン・ドイツ・フランス・イギリス・アメリカ・韓国・台湾）と国際比較を**他の国よりも重点的に**探索する。その他の国は特に重要なニュースのみ intl_other として拾う。
- international の収集元は英語ニュースと日本語ニュースを中心とする。ただし**スウェーデンについてはスウェーデン語のニュース**（SVT Nyheter, Dagens Nyheter, Svenska Dagbladet, Dagens industri, regeringen.se 等）**も検索対象に含める**。要約はいずれも日本語で書く。
- 必ず実在する記事のURLを記載する。検索結果で確認できなかった記事は含めない。
- summary は事実ベースで3〜5文、日本語で書く（英語・スウェーデン語の記事も要約は日本語）。数値（金額・割合・年度）や、論文なら著者名・掲載誌をできるだけ含める。
- 論文は財政・社会保障・教育に関連するものに限る。paper_en は指定した雑誌・ワーキングペーパーシリーズのものだけを拾う。
- international のテーマ（財政・債務／社会保障／税制／教育など）はタグに入れる。
- date は記事・論文の公表日 (YYYY-MM-DD)。不明な場合は概算でよいが未来日付は禁止。
- **【重要】記事のURLから公表年を必ず検証すること。** 検索結果には数年前の記事が混ざる。日経のURLコード（`...Y5A820...`は2025年8月20日、`...C26A8...`は2026年8月）、PDFのファイル名（`20250806_12.pdf`）、本文中の年度表記で確認する。特に「◯年度予算・概算要求」の話はその前年に報じられるため、『2026年度概算要求』の記事は2025年夏のニュースである点に注意。年が合わない記事は収録しない。

最終的な回答は、次の形式のJSON配列だけを ```json コードブロックに入れて出力してください:
```json
[
  {{
    "category": "expenditure | revenue | international | research",
    "subcategory": "上記一覧にある、そのカテゴリのサブカテゴリキー",
    "title": "見出し（日本語。英語論文は英語原題のまま）",
    "summary": "要約（日本語、3〜5文）",
    "source": "媒体・発行機関名・掲載誌名",
    "url": "https://...",
    "date": "YYYY-MM-DD",
    "tags": ["タグ1", "タグ2"]
  }}
]
```

さらに、新着記事が1件以上ある場合は、**配列の最後に category "digest" のまとめ記事を必ず1件**追加すること:
- title: 「今日の財政ニュースまとめ（YYYY年M月D日）」
- summary: その回の新着記事全体を俯瞰した要約・解説を400〜600字の日本語で書く。単なる列挙ではなく、何が起きたのか・なぜ重要か・記事同士や これまでの動きとのつながりが分かる解説にする。
- subcategory / url は不要。source は "編集部まとめ（AI生成）" とする。
- date は今日の日付。

新しい記事が見つからなかったカテゴリは省略してよい。全く見つからなければ空配列 [] を出力すること（その場合まとめも不要）。
"""


def load_data() -> dict:
    with open(DATA_PATH, encoding="utf-8") as f:
        return json.load(f)


def extract_json(text: str) -> list:
    """回答テキストからJSON配列を取り出す。"""
    m = re.search(r"```json\s*(\[.*?\])\s*```", text, re.DOTALL)
    raw = m.group(1) if m else None
    if raw is None:
        # フェンスなしで配列だけ返ってきた場合のフォールバック
        m = re.search(r"(\[.*\])", text, re.DOTALL)
        raw = m.group(1) if m else "[]"
    return json.loads(raw)


def make_id(category: str, date: str, url: str = "") -> str:
    prefix = {
        "expenditure": "exp", "revenue": "rev", "international": "int",
        "research": "res", "government": "gov", "stakeholder": "stk",
        "digest": "dig",
    }[category]
    if category == "digest":
        return f"{prefix}-{date.replace('-', '')}"
    slug = re.sub(r"[^a-z0-9]+", "", url.split("//")[-1])[-12:]
    return f"{prefix}-{date.replace('-', '')}-{slug}"


def validate(item: dict, known_urls: set) -> bool:
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", item.get("date", "")):
        return False
    if item["date"] > datetime.now(JST).strftime("%Y-%m-%d"):
        return False
    if item.get("category") == "digest":
        # まとめ記事はURL・出典・サブカテゴリ不要
        return {"title", "summary"}.issubset(item)
    required = {"category", "title", "summary", "source", "url", "date"}
    if not required.issubset(item):
        return False
    if item["category"] not in CATEGORIES:
        return False
    if item.get("subcategory") not in SUBCATEGORIES[item["category"]]:
        return False
    if not item["url"].startswith("http"):
        return False
    if item["url"] in known_urls:
        return False
    return True


def main() -> None:
    data = load_data()
    articles = data["articles"]
    known_urls = {a["url"] for a in articles}

    since = (datetime.now(JST) - timedelta(days=3)).strftime("%Y年%m月%d日")
    category_desc = "\n".join(f"- {k}: {v}" for k, v in CATEGORIES.items())
    subcategory_desc = "\n".join(
        f"- {cat} の {k}: {v}"
        for cat, subs in SUBCATEGORIES.items()
        for k, v in subs.items()
    )
    prompt = PROMPT_TEMPLATE.format(
        since=since,
        category_desc=category_desc,
        subcategory_desc=subcategory_desc,
        known_urls="\n".join(sorted(known_urls)) or "(なし)",
    )

    client = anthropic.Anthropic()  # ANTHROPIC_API_KEY を環境変数から読む
    with client.messages.stream(
        model="claude-opus-4-8",
        max_tokens=32000,
        thinking={"type": "adaptive"},
        tools=[{
            "type": "web_search_20260209",
            "name": "web_search",
            "max_uses": 15,
        }],
        messages=[{"role": "user", "content": prompt}],
    ) as stream:
        response = stream.get_final_message()

    text = "".join(b.text for b in response.content if b.type == "text")
    try:
        new_items = extract_json(text)
    except json.JSONDecodeError as e:
        print(f"JSONの解析に失敗しました: {e}\n---\n{text[:2000]}", file=sys.stderr)
        sys.exit(1)

    existing_ids = {a["id"] for a in articles}
    added = 0
    for item in new_items:
        if not isinstance(item, dict) or not validate(item, known_urls):
            continue
        item["id"] = make_id(item["category"], item["date"], item.get("url", ""))
        if item["id"] in existing_ids:  # 同日のまとめ再生成などの重複防止
            continue
        item.setdefault("tags", [])
        articles.append(item)
        existing_ids.add(item["id"])
        if item.get("url"):
            known_urls.add(item["url"])
        added += 1

    # カテゴリ×サブカテゴリごとに新しい順で上限件数まで保持
    articles.sort(key=lambda a: a["date"], reverse=True)
    trimmed, counts = [], {}
    for a in articles:
        key = (a["category"], a.get("subcategory"))
        c = counts.get(key, 0)
        if c < MAX_PER_CATEGORY:
            trimmed.append(a)
            counts[key] = c + 1

    data["articles"] = trimmed
    data["lastUpdated"] = datetime.now(JST).isoformat(timespec="seconds")

    with open(DATA_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")

    print(f"追加 {added} 件 / 合計 {len(trimmed)} 件")


if __name__ == "__main__":
    main()
