---
name: dev-env-best-practices
user-invocable: false
description: |-
  開発環境構築時に言語/FW 別のベストプラクティス reference と構築観点を引き出す。
  Use when「開発環境構築」「環境セットアップ」「新規プロジェクト立ち上げ」「dev env」「開発環境のベストプラクティス」。
---

# Dev Env Best Practices

開発環境構築の意思決定を、観点リスト × 言語/FW 別 reference で支援する。

## 観点リスト

開発環境構築で抑えるべき観点。reference を読むときの網羅性確認と、新規調査を依頼するときの調査観点の両方に使う。言語/FW により濃淡はあるが、欠落させる場合は理由を明示する。

1. ランタイム / 言語バージョン管理（プロジェクト内でバージョン固定できるか）
2. 依存関係・パッケージ管理（マネージャー選定、lockfile の再現性）
3. プロジェクト構造・初期化（標準レイアウト、設定ファイル構成）
4. コード品質（linter / formatter / 型チェック）
5. テスト環境
6. pre-commit フック
7. CI/CD
8. セキュリティ（依存脆弱性の管理）
9. ビルド・パッケージング・配布
10. 環境変数・シークレット管理
11. ツールチェーン一貫性（IDE / ターミナル / ビルドが同じランタイム・SDK を参照しているか）
12. 最新性・廃止手順の回避（公式の方針転換、古い記事の手順を踏まない）

## Reference index

staleness 判定はこの表の「最終取得日」を single source とする（各ファイルの日付表記は形式が不揃いのため使わない）。パスは skill ディレクトリ相対。

| 言語/FW | パス | 最終取得日 |
|---|---|---|
| Node.js / TypeScript | `references/node-dev-env-best-practices.md` | 2026-06-08 |
| Python | `references/python-dev-env-best-practices.md` | 2026-06-08 |
| Android (on Mac) | `references/android-dev-env-on-mac.md` | 2026-06-11 |

## フロー

1. 対象の言語/FW を特定し、index を照合する
2. **hit** → reference を Read し、観点リストと突き合わせて適用する
   - 最終取得日が現在から 1 年以上前 → 再調査を提案する（自動では始めない）。既存 reference は staleness を明示すれば暫定適用してよい。更新調査の完了後、reference を差し替えて index の日付を更新する
3. **miss** → 新規調査を提案する。完了したレポートを `references/<言語またはFW>-dev-env-best-practices.md` として skill 内に保存し、index に行を追加する

## 調査の委譲

新規・更新調査は `mattpocock-skills` plugin の `research` skill に委譲する。観点リストを調査観点として埋め込んだ調査依頼文を組み立ててから渡す (依頼文に観点を入れないと、調査軸がこの skill の網羅性基準からずれる)。同 skill は model からも起動できるので、Skill tool 経由で直接呼んでよい。

## 更新の罠

この skill は references/ も含めて swat-skills plugin 同梱。index や reference の更新は [plugin repo](https://github.com/swat9013/swat-skills) の `skills/knowledge/dev-env-best-practices/` に対して行う。install 済みの plugin ディレクトリを直接書き換えても上流には還らず、次の更新で失われる。
