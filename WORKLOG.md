# 作業ログ

財政情報ポータルの開発・運用記録。作業セッションごとに追記していく。

---

## 2026-07-03〜04: 初期構築と公開

### やったこと

- サイト一式を新規構築
  - `index.html` + `assets/style.css` + `assets/app.js`: 4カテゴリ（歳出・歳入・国際・論文レポート）のタブ切替、キーワード検索、タグ絞り込み付きの静的サイト
  - `data/news.json`: 初期データとして実際のニュース・レポート20件を収集して収録（2026年度予算、骨太方針2026、IMF財政モニター等）
  - `scripts/update_news.py`: Claude API（Web検索ツール付き）で直近3日の新着を収集・要約し `news.json` に追記するスクリプト
  - `.github/workflows/update.yml`: 毎日07:00 JSTに自動実行するGitHub Actions
- GitHubで公開
  - リポジトリ `fiscal-portal` を作成しGitHub Pagesを有効化
  - 公開URL: https://michihito-ando-private.github.io/fiscal-portal/
- 検索エンジン対策（試験的利用のため）
  - `noindex, nofollow, noarchive` メタタグ、`robots.txt` で全クローラー拒否

### 決定事項・経緯

- **リポジトリは公開設定**: 無料プランではプライベートリポジトリでPagesが使えなかったため。検索避けは上記で対応済みだが、URLを知る人は閲覧可能
- 初回のPagesデプロイがGitHub側の一時エラーで失敗 → `.nojekyll` を追加して再デプロイで成功
- `ANTHROPIC_API_KEY` 未設定時はワークフローがエラーを出さず静かにスキップする設計にした
- 更新スクリプトの保持ルール: カテゴリごとに最大40件、URL重複は除外、日付降順

### 残タスク

- [ ] リポジトリのSecretsに `ANTHROPIC_API_KEY` を登録（これをしないと自動更新が動かない）
  - 設定場所: Settings → Secrets and variables → Actions → New repository secret
- [ ] 自動更新の初回実行結果の確認（Actionsタブから手動実行でテスト可能）

### ブラッシュアップ候補（アイデアメモ）

- 検索ボックスに絞り込み中であることの表示＋クリアボタン（検索語が残っていると記事が消えたように見える）
- カテゴリ内のサブタグ（例: 歳出→社会保障/教育/公共事業/防衛）でのさらなる絞り込み
- 過去記事のアーカイブページ（40件上限で消える記事の保存）
- 週間サマリー（1週間の動きをAIが1本の記事にまとめる）
- 更新スクリプトの検索クエリ・プロンプトのチューニング

---

（次回の作業をここに追記）
