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
    "research": "財政・社会保障に関する日本や海外の有識者・研究機関の論文やレポート（シンクタンク、大学、財務省・内閣府等の審議会資料）",
}

PROMPT_TEMPLATE = """あなたは日本の財政情報ポータルサイトの編集者です。Web検索を使って、{since}以降に公表された新しいニュース・レポートを収集し、日本語で要約してください。

対象カテゴリ:
{category_desc}

既に掲載済みのURL（これらは除外すること）:
{known_urls}

要件:
- 各カテゴリにつき最大3件、合計で最大10件まで。新しく重要なものを優先する。
- 必ず実在する記事のURLを記載する。検索結果で確認できなかった記事は含めない。
- summary は事実ベースで3〜5文。数値（金額・割合・年度）をできるだけ含める。
- date は記事の公表日 (YYYY-MM-DD)。不明な場合は概算でよいが未来日付は禁止。

最終的な回答は、次の形式のJSON配列だけを ```json コードブロックに入れて出力してください:
```json
[
  {{
    "category": "expenditure | revenue | international | research",
    "title": "見出し（日本語）",
    "summary": "要約（日本語、3〜5文）",
    "source": "媒体・発行機関名",
    "url": "https://...",
    "date": "YYYY-MM-DD",
    "tags": ["タグ1", "タグ2"]
  }}
]
```
新しい記事が見つからなかったカテゴリは省略してよい。全く見つからなければ空配列 [] を出力すること。
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


def make_id(category: str, date: str, url: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "", url.split("//")[-1])[-12:]
    prefix = {"expenditure": "exp", "revenue": "rev", "international": "int", "research": "res"}[category]
    return f"{prefix}-{date.replace('-', '')}-{slug}"


def validate(item: dict, known_urls: set) -> bool:
    required = {"category", "title", "summary", "source", "url", "date"}
    if not required.issubset(item):
        return False
    if item["category"] not in CATEGORIES:
        return False
    if not item["url"].startswith("http"):
        return False
    if item["url"] in known_urls:
        return False
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", item["date"]):
        return False
    if item["date"] > datetime.now(JST).strftime("%Y-%m-%d"):
        return False
    return True


def main() -> None:
    data = load_data()
    articles = data["articles"]
    known_urls = {a["url"] for a in articles}

    since = (datetime.now(JST) - timedelta(days=3)).strftime("%Y年%m月%d日")
    category_desc = "\n".join(f"- {k}: {v}" for k, v in CATEGORIES.items())
    prompt = PROMPT_TEMPLATE.format(
        since=since,
        category_desc=category_desc,
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

    added = 0
    for item in new_items:
        if not isinstance(item, dict) or not validate(item, known_urls):
            continue
        item["id"] = make_id(item["category"], item["date"], item["url"])
        item.setdefault("tags", [])
        articles.append(item)
        known_urls.add(item["url"])
        added += 1

    # カテゴリごとに新しい順で上限件数まで保持
    articles.sort(key=lambda a: a["date"], reverse=True)
    trimmed, counts = [], {}
    for a in articles:
        c = counts.get(a["category"], 0)
        if c < MAX_PER_CATEGORY:
            trimmed.append(a)
            counts[a["category"]] = c + 1

    data["articles"] = trimmed
    data["lastUpdated"] = datetime.now(JST).isoformat(timespec="seconds")

    with open(DATA_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")

    print(f"追加 {added} 件 / 合計 {len(trimmed)} 件")


if __name__ == "__main__":
    main()
