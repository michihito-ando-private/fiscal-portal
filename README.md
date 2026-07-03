# 財政情報ポータル (Fiscal Policy Watch)

日本と世界の財政・社会保障に関するニュースとレポートを、AI（Claude）が毎日自動収集・要約して掲載する静的Webサイトです。

## カテゴリ

| カテゴリ | 内容 |
|---|---|
| 歳出 | 社会保障・教育・公共事業・防衛など、国・自治体の支出に関するニュース |
| 歳入 | 税収・社会保険料・国債に関するニュース |
| 国際 | IMF等の国際機関や各国の財政・社会保障ニュース |
| 論文・レポート | 日本・海外の有識者、シンクタンク、審議会等の分析・一次資料 |

## 構成

```
fiscal_portal/
├── index.html              # トップページ
├── assets/
│   ├── style.css           # スタイル
│   └── app.js              # 記事の表示・絞り込み
├── data/
│   └── news.json           # 記事データ（自動更新される）
├── scripts/
│   └── update_news.py      # Claude API + Web検索で記事を収集する更新スクリプト
└── .github/workflows/
    └── update.yml          # 毎日 07:00 JST に自動実行される GitHub Actions
```

サイト本体は完全に静的で、`data/news.json` を fetch して表示するだけです。更新は GitHub Actions が `update_news.py` を実行し、収集した記事を `news.json` に追記してコミットすることで行われます。

## 公開手順（GitHub Pages）

1. GitHub で新しいリポジトリを作成する（例: `fiscal-portal`、Public）
2. このフォルダの中身をリポジトリのルートとしてプッシュする:
   ```bash
   cd fiscal_portal
   git init
   git add .
   git commit -m "初回コミット"
   git branch -M main
   git remote add origin https://github.com/<ユーザー名>/fiscal-portal.git
   git push -u origin main
   ```
3. リポジトリの **Settings → Pages** で
   - Source: `Deploy from a branch`
   - Branch: `main` / `/ (root)` を選択して保存
4. 数分後に `https://<ユーザー名>.github.io/fiscal-portal/` で公開される

## 自動更新の設定

1. [Anthropic Console](https://console.anthropic.com/) で APIキーを取得する
2. リポジトリの **Settings → Secrets and variables → Actions → New repository secret** で
   - Name: `ANTHROPIC_API_KEY`
   - Secret: 取得したAPIキー
   を登録する
3. これで毎日 07:00 JST に自動更新される。**Actions タブ → ニュース自動更新 → Run workflow** で手動実行も可能

### 更新の仕組み

- `scripts/update_news.py` が Claude（`claude-opus-4-8`）を Web検索ツール付きで呼び出し、直近3日間の新着ニュース・レポートを検索・要約させる
- 掲載済みURLは重複除外し、カテゴリごとに最大40件まで保持
- 1回の実行コストの目安は数十円程度（Web検索 最大15回 + 要約生成）

### ローカルでの動作確認

```bash
# サイトの表示確認
python3 -m http.server 8000
# → http://localhost:8000 を開く

# 更新スクリプトのテスト実行
ANTHROPIC_API_KEY=sk-... python3 scripts/update_news.py
```

## 注意事項

- 要約はAIによる自動生成のため、必ずリンク先の一次情報を確認してください
- 記事の全文転載はせず、見出し・短い要約・出典リンクのみを掲載しています
- 更新頻度やカテゴリ別の件数上限は `update.yml`（cron）と `update_news.py`（`MAX_PER_CATEGORY` 等）で調整できます
