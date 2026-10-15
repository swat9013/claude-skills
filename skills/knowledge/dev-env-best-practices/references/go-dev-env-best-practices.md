# 中規模Goツール開発環境のベストプラクティス（2026年版）

**対象**: 写真管理ツールのような CLI + HTTP API サーバーを含む中規模 Go アプリケーション（ライブラリとして公開するモジュールとの差分は各節で注記）
**最終更新**: 2026-09-28

> 姉妹版に `python-dev-env-best-practices.md` / `node-dev-env-best-practices.md` があります。同じ photo-manager 題材で章立てをできるだけ揃えています。

---

## 0. バージョン早見表（2026-09-28 時点で確認）

| 対象 | 最新 | 日付 | 確認元 |
|---|---|---|---|
| Go（最新メジャー） | **1.27.1** | 2026-09-01（1.27.0 は 2026-08-19） | golang/website `internal/history/release.go` |
| Go（1つ前のメジャー） | 1.26.8 | 2026-09-01（1.26.0 は 2026-02-10） | 同上 |
| サポート対象 | **1.27 系 と 1.26 系**（1.25 は 1.27 リリースでサポート終了） | — | [Release Policy](https://go.dev/doc/devel/release#policy)「各メジャーは 2 つ新しいメジャーが出るまでサポート」 |
| golangci-lint | **v2.14.0** | 2026-09-24 | [GitHub Releases](https://github.com/golangci/golangci-lint/releases)（v2.0.0 は 2025-03-24） |
| golangci-lint-action | **v9.3.0** | 2026-06-29 | [GitHub Releases](https://github.com/golangci/golangci-lint-action/releases) |
| Staticcheck | **2026.2.1 (v0.8.1)** | 2026-08-21 | [GitHub Releases](https://github.com/dominikh/go-tools/releases)（2026.2 で Go 1.27 対応） |
| govulncheck (golang.org/x/vuln) | **v1.8.0** | 2026-09-08（tag の commit 日。golang/vuln は GitHub Release を更新しておらず "Latest" 表示の v1.1.4 は古い） | [golang/vuln tags](https://github.com/golang/vuln/tags) |
| govulncheck-action | v1.1.0 | 2026-07-10 | [GitHub Releases](https://github.com/golang/govulncheck-action/releases) |
| gopls | **v0.23.0** | 2026-07-07（tag の commit 日） | [golang/tools tags](https://github.com/golang/tools/tags) |
| gofumpt | v0.12.0 | 2026-09-07 | [GitHub Releases](https://github.com/mvdan/gofumpt/releases) |
| GoReleaser | **v2.18.2** | 2026-09-17（v2.19 は nightly のみ） | [GitHub Releases](https://github.com/goreleaser/goreleaser/releases) |
| goreleaser-action | v7.2.3 | 2026-06-29 | [GitHub Releases](https://github.com/goreleaser/goreleaser-action/releases) |
| ko | v0.19.1 | 2026-06-29 | [GitHub Releases](https://github.com/ko-build/ko/releases) |
| actions/setup-go | **v7.0.0** | 2026-07-16 | [GitHub Releases](https://github.com/actions/setup-go/releases) |
| actions/checkout | v7.0.1 | 2026-07-20 | [GitHub Releases](https://github.com/actions/checkout/releases) |
| mise | v2026.9.15 | 2026-09-27 | [GitHub Releases](https://github.com/jdx/mise/releases) |
| pre-commit | 4.6.2 | 2026-08-10（`language: unsupported` は 4.4.0 から） | [CHANGELOG](https://github.com/pre-commit/pre-commit/blob/main/CHANGELOG.md) |
| gitleaks | v8.30.1 | 2026-03-21 | [GitHub Releases](https://github.com/gitleaks/gitleaks/releases) |
| gitleaks-action | v3.0.0 | 2026-05-30（v2.0.0 以降は MIT でなく EULA。§8） | [GitHub Releases](https://github.com/gitleaks/gitleaks-action/releases) |

---

## 1. ランタイム / 言語バージョン管理

### 推奨: go.mod の `go` / `toolchain` 行を唯一の正本にし、Go 本体の `GOTOOLCHAIN=auto` に切り替えを任せる

**結論**: Go 1.21 以降は **Go 自身がプロジェクト単位のバージョン固定機構を持つ**。pyenv / nvm 相当の外部ツールは必須ではない。`go.mod` に最低要件（`go` 行）と推奨 toolchain（`toolchain` 行）を書けば、手元の `go` コマンドが必要に応じて該当バージョンを自動ダウンロードして実行する。

**根拠**（[Go Toolchains](https://go.dev/doc/toolchain)）:
- `go` 行 = **最低要件**。Go 1.21 から必須要件になり、それより古い toolchain はモジュールの読み込みを拒否する
- `toolchain` 行 = **推奨 toolchain**。既定 toolchain より新しければ `go` コマンドがそちらを実行する。省略時は `go` 行と同じ版とみなす
- `GOTOOLCHAIN` の既定値は `auto`（= `local+auto`）。`local` は同梱 toolchain 固定、`go1.27.1` のような名前指定はその版を強制、`<name>+path` はダウンロードせず PATH 上の `go1.27.1` 等のみ探す
- ダウンロードされる toolchain は `golang.org/toolchain` モジュールとして取得され、**GOPROXY 経由・checksum DB で検証**される（`GOSUMDB=off` だと検証できず失敗。`GOPRIVATE`/`GONOSUMDB` は toolchain には効かない）
- `go` / `toolchain` 行は手編集せず `go get go@1.27.1` / `go get toolchain@go1.27.1` で変える（`go mod tidy -go=` より推奨）
- Go 1.25 から、`go` 行を更新する際に `toolchain` 行を自動で書き足さなくなった（[Go 1.25 Release Notes](https://go.dev/doc/go1.25#go-command)）
- Go 1.26 から、`go mod init` は **1 つ前のメジャー**（Go 1.27 なら `go 1.26.0`）を `go` 行に書く。サポート中の Go と互換なモジュールを促すため（[Go 1.26 Release Notes](https://go.dev/doc/go1.26#go-command)）
- 選択過程は `GODEBUG=toolchaintrace=1 go version` で追跡できる（Go 1.24+）

**アプリとライブラリで方針を分ける**（`go` 行と `toolchain` 行の役割分担から導いた推奨。公式が明示するのは「依存されるモジュールは推奨 toolchain より低い最低要件を置いてよい」まで）:

| 種別 | `go` 行 | `toolchain` 行 |
|---|---|---|
| アプリ（CLI / サーバー） | 実際に使う言語機能の版（例 `go 1.27.0`） | 開発・CI で使う正確なパッチ版（例 `toolchain go1.27.1`）。セキュリティ修正ごとに上げる |
| ライブラリ | サポート中の最古（現在は `go 1.26.0`）まで下げる | 省略可（利用者の `go` 行・toolchain は利用側が決める） |

```bash
# 新規
mkdir photo-manager && cd photo-manager
go mod init github.com/example/photo-manager   # Go 1.27 だと go 1.26.0 が書かれる
go get go@1.27.0 toolchain@go1.27.1            # アプリなら言語版と toolchain を明示

# 確認
go version                      # 選択された toolchain
GOTOOLCHAIN=local go version    # 同梱 toolchain
GODEBUG=toolchaintrace=1 go version
```

### mise / asdf との関係

**結論**: 使うなら「Go 本体の初回インストール手段」に限定し、**バージョンの正本は go.mod に置く**。mise に別バージョンを書くと、mise が入れた Go と `GOTOOLCHAIN=auto` のダウンロード版が食い違う。

**根拠**（[mise: Go](https://github.com/jdx/mise/blob/main/docs/lang/go.md)）:
- mise は idiomatic version file を有効化すると `.go-version` / `go.mod` / `go.work` を読む（`mise settings add idiomatic_version_file_enable_tools go`）
- `go.mod` / `go.work` の **`toolchain goX.Y.Z` 行を正確な版指定として読む**。`go` 行は `go.work` では無視し、`go.mod` から読むのは **非推奨**
- よって mise を併用するなら `toolchain` 行を書くのが整合的

asdf 固有の Go 挙動は一次情報で未確認（本調査では扱わない）。

---

## 2. 依存関係・パッケージ管理

### 推奨: Go modules（go.mod + go.sum）を commit、go.work は原則 commit しない

**結論**: 依存管理は Go 標準の modules のみで完結する（Poetry/pnpm 相当の外部ツールは不要）。`go.mod` と `go.sum` は必ず commit する。

**根拠**:
- `go.sum` は直接・間接依存の暗号学的ハッシュ（現在 SHA-256 の `h1:`）を持ち、ダウンロード時に照合される。`go mod tidy` が不足分を追加・不要分を削除する（[ref/mod: go.sum files](https://go.dev/ref/mod#go-sum-files)）
- 公開モジュールは checksum DB（sum.golang.org）でも照合され、proxy やオリジンが後からタグを差し替えても検知できる（[ref/mod: Checksum database](https://go.dev/ref/mod#checksum-database)）
- ビルド系コマンドの既定は `-mod=readonly`（vendor/ があり go 1.14+ なら `-mod=vendor`）。つまり `go build` / `go test` は go.mod を勝手に書き換えない（[cmd/go](https://pkg.go.dev/cmd/go)）
- **CI での差分検知**: `go mod tidy -diff`（Go 1.23+）はファイルを変更せず差分を出し、更新が必要なら非ゼロ終了する（[Go 1.23 Release Notes](https://go.dev/doc/go1.23)）
- Go 1.27 から、`go 1.27` 以上のモジュールでは `go mod tidy` が require ブロックを「直接依存」「間接依存」の最大 2 ブロックに自動統合する（[Go 1.27 Release Notes](https://go.dev/doc/go1.27#go-command)）

```bash
go get github.com/example/lib@v1.4.2   # 追加・更新（go.mod/go.sum を更新）
go get -u ./...                        # 依存を一括更新（要レビュー）
go list -m -u all                      # 更新可能な依存の確認
go mod tidy                            # 不要依存の削除・go.sum 整理
go mod tidy -diff                      # CI: 整っていなければ失敗
go mod verify                          # モジュールキャッシュの改ざん検査
go get example.com/old@none            # 依存の削除
```

### go.work（ワークスペース）

**結論**: 複数モジュールを手元で同時編集するための **ローカル用**。原則 commit せず、CI では `GOWORK=off` で単体モジュールとして検証する。

**根拠**（[ref/mod: go.work](https://go.dev/ref/mod#workspaces)）: 「go.work を VCS に commit するのは一般に勧めない」。理由は (1) 開発者の親ディレクトリの go.work を上書きして混乱させる、(2) CI が誤った依存バージョンでテストしてしまう（他モジュールから require されたときの挙動を検証できない）。例外として、リポジトリ内のモジュール群が常に一緒にしか開発されない場合は commit してよい。

```bash
go work init ./api ./worker
go work use ./lib
GOWORK=off go test ./...   # CI 相当の検証
```

### vendoring

**結論**: 既定では **不要**。オフラインビルドやビルド入力を単一ツリーに閉じ込めたい監査要件がある場合のみ採用する。

**根拠**（[ref/mod: Vendoring](https://go.dev/ref/mod#vendoring)）: `go mod vendor` が `vendor/` と `vendor/modules.txt` を作る。go 1.14+ で `vendor/` があれば自動で使われ、go.mod と不整合ならエラー。`go mod tidy` / `go get` は vendoring 有効時もネットワークとキャッシュを使う点に注意。ワークスペース用には `go work vendor`（Go 1.22+）。

### GOPROXY / GOPRIVATE / GONOSUMDB（プライベートモジュール）

**結論**: 社内モジュールは `GOPRIVATE` にプレフィックスを設定するだけでよい（proxy と checksum DB の両方を迂回する）。`GOPROXY` 自体は既定のまま。

**根拠**（[ref/mod: Private modules](https://go.dev/ref/mod#private-modules), [Managing dependencies](https://go.dev/doc/modules/managing-dependencies#proxy_server)）:
- `GOPROXY` 既定値は `https://proxy.golang.org,direct`。カンマ区切りは 404/410 のときのみ次へ、パイプ区切りはエラー種別を問わず次へ
- `GOPRIVATE` は `GONOPROXY` と `GONOSUMDB` の既定値を兼ねる
- `GONOSUMDB` は checksum DB 照合のみを外したい場合（社内 proxy を経由させつつ等）
- 認証は Git の資格情報（`.gitconfig` / `.netrc`）で行い、対話プロンプトは `GIT_TERMINAL_PROMPT=0` で無効化されている。Go 1.24 から `GOAUTH` で認証方法を柔軟に指定可能（`go help goauth`）

```bash
go env -w GOPRIVATE='github.com/example-corp/*'
go env GOPROXY GOPRIVATE GONOSUMDB
```

---

## 3. プロジェクト構造・初期化

### 推奨: 公式 "Organizing a Go module" に従い、`cmd/` + `internal/` で始める

**結論**: サーバー / 複数コマンドを持つアプリは **`cmd/<コマンド名>/main.go` + `internal/`** が公式推奨。小さく始めるなら `main.go` をルートに置くだけでもよい。

**根拠**（[Organizing a Go module](https://go.dev/doc/modules/layout)）:
- `internal/` 配下は他モジュールから import できないため、API を自由にリファクタできる。「可能な限り internal に置く」ことが推奨されている
- サーバープロジェクトは外部公開パッケージを持たないのが普通なので、ロジックは `internal/`、コマンドは `cmd/` にまとめる
- 他プロジェクトと共有したいパッケージが育ったら **別モジュールに切り出す**
- 複数コマンドは `go install github.com/someuser/modname/cmd/prog1@latest` で個別にインストールできる

```
photo-manager/
├── go.mod
├── go.sum
├── cmd/
│   ├── photo-manager/        # CLI
│   │   └── main.go
│   └── photo-server/         # HTTP API
│       └── main.go
├── internal/
│   ├── storage/
│   │   ├── storage.go
│   │   └── storage_test.go   # テストは同じディレクトリに置く（Go の慣習）
│   ├── imaging/
│   └── httpapi/
├── testdata/                 # go ツールが無視する予約名
├── .golangci.yml
├── .goreleaser.yaml
└── README.md
```

### golang-standards/project-layout の位置付け

**結論**: **非公式**。`pkg/` `api/` `configs/` 等の多数のディレクトリを最初から切る必要はない。

**根拠**: 同リポジトリ README 自身が「`NOT an official standard defined by the core Go dev team`」と明記し、公式の [Organizing a Go module](https://go.dev/doc/modules/layout) を参照するよう案内している（[golang-standards/project-layout README](https://github.com/golang-standards/project-layout)）。Go チームの rsc も issue [#117 "this is not a standard Go project layout"](https://github.com/golang-standards/project-layout/issues/117)（2021-04）で同趣旨を表明している。

### パターン除外: go.mod の `ignore` ディレクティブ（Go 1.25+）

`node_modules/` などを含むフロントエンド同居リポジトリでは、`./...` のマッチから外したいディレクトリを `ignore` で指定できる（モジュール zip には含まれる）（[ref/mod: ignore](https://go.dev/ref/mod#go-mod-file-ignore)）。

```
ignore ./web/node_modules
```

---

## 4. コード品質

### フォーマッター: gofmt（必須）+ goimports、厳格にしたいなら gofumpt

**結論**: `gofmt` は議論の余地なく必須。import 整理に `goimports`、より厳しい整形が欲しければ `gofumpt`（gofmt 互換の上位集合）。いずれも **golangci-lint v2 の `formatters` から一括実行**するのが管理しやすい。

**根拠**:
- gofumpt は「gofmt より厳格で後方互換。gofumpt の出力に gofmt をかけても変化しない」。v0.12.0 時点で Go 1.27.0 の gofmt からの fork で、Go 1.26+ を要求。go.mod の `ignore` ディレクティブにも従う（[gofumpt README](https://github.com/mvdan/gofumpt)）
- golangci-lint v2 で `gci` / `gofmt` / `gofumpt` / `goimports` は `linters` から **`formatters` セクションへ移動**した（[golangci-lint Migration guide](https://golangci-lint.run/docs/product/migration-guide/)）

### 静的解析: go vet → staticcheck → golangci-lint v2

**結論**: `go vet` は `go test` が高信頼サブセットを自動実行する。追加の静的解析は **golangci-lint v2 に staticcheck を含めて集約**する。

**根拠**:
- `go test` は vet の高信頼サブセット（atomic, bools, buildtag, directive, errorsas, ifaceassert, nilfunc, printf, stdversion, stringintconv, tests）を実行し、問題があればテストを走らせない。全チェックは `-vet=all`（[cmd/go](https://pkg.go.dev/cmd/go#hdr-Test_packages)）。`stdversion`（go.mod の `go` 行より新しい標準ライブラリ API の使用を検出）は Go 1.27 で追加（[Go 1.27 Release Notes](https://go.dev/doc/go1.27#go-command)）
- Go 1.26 で `go fix` が **modernizer（最新イディオムへの自動書き換え）の置き場**として作り直された。vet と同じ analysis framework 上に構築（[Go 1.26 Release Notes](https://go.dev/doc/go1.26#go-command)）。Go 1.27 で modernizer が追加された一方 `fmtappendf` は削除、`waitgroup` は `waitgroupgo` に改名
- Staticcheck 2026.2 で Go 1.27 の新構文（generic methods 等）に対応（[Staticcheck 2026.2 release](https://github.com/dominikh/go-tools/releases/tag/2026.2)）
- golangci-lint v2 では `stylecheck` / `gosimple` / `staticcheck` が **`staticcheck` に統合**された（Migration guide）
- `linters.default` の既定値は `standard`（= errcheck, govet, ineffassign, staticcheck, unused。golangci-lint の `pkg/lint/lintersdb/builder_linter.go` で `GroupStandard` に属するもの）

```bash
go vet ./...
go fix -diff ./...   # modernizer の提案を unified diff で表示（差分があれば非ゼロ終了）
golangci-lint run
golangci-lint fmt
```

> `go fix -diff` は「修正を適用せず unified diff を出力し、差分があれば非ゼロ終了」（[cmd/go: go fix](https://pkg.go.dev/cmd/go#hdr-Apply_fixes_suggested_by_static_checkers)）。CI で modernizer 未適用を検出する用途にも使える。

### golangci-lint v2 設定例

```yaml
# .golangci.yml
version: "2"

linters:
  default: standard          # errcheck, govet, ineffassign, staticcheck, unused
  enable:
    - bodyclose
    - errorlint
    - gosec
    - misspell
    - nolintlint
    - revive
  exclusions:
    presets:
      - comments
      - std-error-handling
      - common-false-positives

formatters:
  enable:
    - gofumpt
    - goimports
  settings:
    goimports:
      local-prefixes:
        - github.com/example/photo-manager
```

**根拠**: `version: "2"`、`linters.default` の値（`standard` / `all` / `none` / `fast`）、`exclusions.presets`（`comments` / `std-error-handling` / `common-false-positives` / `legacy`）、`formatters.settings.goimports.local-prefixes`（v2 でリスト化）は [.golangci.reference.yml](https://github.com/golangci/golangci-lint/blob/main/.golangci.reference.yml) で確認。追加 linter の選定（bodyclose 等）は本レポートの推奨であり、公式推奨セットではない。

**再現性の注意**: `default: all` は新 linter 追加や上流更新で CI が一斉に落ちる原因になると公式が警告している。バージョンを固定する（[CI Installation](https://golangci-lint.run/docs/welcome/install/ci/)）。

### gopls（エディタ / LSP）

**結論**: VS Code の Go 拡張等に管理を任せるか `go install golang.org/x/tools/gopls@latest`。**PATH 上の `go` がサポート対象（最新 2 メジャー）であること**が条件。

**根拠**（[gopls docs](https://go.dev/gopls/)）:
- gopls は実行時に `$PATH` 上の `go` を呼んでワークスペース情報を得る。サポートは Go のリリースポリシーに合わせ **最新 2 メジャーのみ**
- 各ファイルの言語版は go.mod の `go` 行と `//go:build go1.xx` から判定する
- gopls はエディタの環境変数を継承する（エディタの PATH / GOTOOLCHAIN がターミナルと違うと挙動が変わる）
- マイナー版はおおむね 3 か月ごと（1/4/7/10 月）

---

## 5. テスト環境

### 推奨: 標準 `go test` のみ。テーブル駆動 + サブテスト + `-race`

**結論**: pytest/vitest 相当の外部ランナーは不要。`testing` パッケージと `go test` で完結する。

**根拠と要点**:
- **テーブル駆動テスト**: 入力と期待値を表にして 1 本のテストで回す Go の定番手法（[Go Wiki: TableDrivenTests](https://go.dev/wiki/TableDrivenTests)）。`t.Run` でサブテスト化すると個別実行・並列化できる
- Go 1.22 から for ループ変数が **反復ごとに新しい変数**になったため、`tt := tt` のコピーは不要（[Go 1.22 Release Notes](https://go.dev/doc/go1.22#language)。go.mod の `go` 行が 1.22 以上のとき）
- **レース検出**: `go test -race`。実行された経路のみ検出する。**cgo 必須・非 Darwin では C コンパイラが必要**、対応プラットフォームは限定（[Security Best Practices](https://go.dev/doc/security/best-practices), [Data Race Detector](https://go.dev/doc/articles/race_detector#Requirements)）。したがって `CGO_ENABLED=0` のジョブでは `-race` を回せない
- **ファジング**: `func FuzzXxx(f *testing.F)` + `go test -fuzz=FuzzXxx -fuzztime=30s`（Go 1.18+。[Fuzzing](https://go.dev/security/fuzz), [cmd/go](https://pkg.go.dev/cmd/go#hdr-Testing_flags)）。見つかった入力は `testdata/fuzz/` に保存され通常の `go test` で回帰テストになる
- **カバレッジ**: `-coverprofile` / `-covermode` / `-coverpkg`。統合テスト用に `go build -cover` したバイナリを `GOCOVERDIR` で計測できる（Go 1.20+。[Coverage profiling support for integration tests](https://go.dev/doc/build-cover)）
- **テスト順序依存の検出**: `-shuffle=on`
- **新しめの標準機能**:
  - `testing/synctest`（Go 1.25 で正式化、1.24 では `GOEXPERIMENT=synctest`）: 隔離された "bubble" 内で偽の時計を使い、時間・並行処理のテストを決定的・高速にする。`synctest.Test` / `synctest.Wait`（[Go 1.25 Release Notes](https://go.dev/doc/go1.25#new-testingsynctest-package)）。Go 1.27 で `synctest.Sleep` 追加、`net/http/httptest` に synctest 向けの偽ネットワークが追加（[Go 1.27 Release Notes](https://go.dev/doc/go1.27)）
  - `T.Context`（1.24）: テスト終了時に cancel される context。`T.Chdir`（1.24）
  - `B.Loop`（1.24）: `for b.Loop() { ... }` でベンチマーク。1.26 でインライン化阻害が解消され、`b.N` 形式からの移行が推奨される状態に（[Go 1.24](https://go.dev/doc/go1.24), [Go 1.26](https://go.dev/doc/go1.26)）
  - `T.Attr`（1.25）、`T.ArtifactDir` + `go test -artifacts`（1.26）

```go
// internal/storage/storage_test.go
func TestNormalizeName(t *testing.T) {
	tests := []struct {
		name string
		in   string
		want string
	}{
		{"lowercase", "IMG_0001.JPG", "img_0001.jpg"},
		{"trim", "  a.jpg ", "a.jpg"},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			t.Parallel()
			if got := NormalizeName(tt.in); got != tt.want {
				t.Errorf("NormalizeName(%q) = %q, want %q", tt.in, got, tt.want)
			}
		})
	}
}

func FuzzNormalizeName(f *testing.F) {
	f.Add("IMG_0001.JPG")
	f.Fuzz(func(t *testing.T, s string) {
		_ = NormalizeName(s) // panic しないこと
	})
}
```

```bash
go test ./...
go test -race -shuffle=on ./...
go test -coverprofile=cover.out ./... && go tool cover -html=cover.out
go test -run='^$' -fuzz=FuzzNormalizeName -fuzztime=30s ./internal/storage
go test -bench=. -benchmem ./...
```

---

## 6. pre-commit フック

**結論**: Go 公式のフック機構はない。**pre-commit（または lefthook 等）から golangci-lint の公式フック**を呼ぶのが最小構成。ただし公式フック定義は `language: golang`（＝ソースからビルド）で、golangci-lint 自身が非推奨とするインストール経路にあたる点に注意し、**ローカルにバイナリで入れた golangci-lint を `repo: local` + `language: unsupported`（4.4.0 より前の名前は `language: system`）で呼ぶ**方が §11 の方針と整合する。`go mod tidy` / `go vet` のような自分のコードへの Go 標準コマンドも同じく `language: unsupported` で書き、`language: golang` では書かない。

**根拠**:
- golangci-lint リポジトリの [.pre-commit-hooks.yaml](https://github.com/golangci/golangci-lint/blob/main/.pre-commit-hooks.yaml) は `golangci-lint`（`run --new-from-rev HEAD --fix`）、`golangci-lint-full`（全ファイル。CI で pre-commit を回す場合用）、`golangci-lint-fmt`、`golangci-lint-config-verify` を提供。いずれも `language: golang`
- 既定の `golangci-lint` フックは変更ファイルのみ対象のため、**`unused` のような全体走査型 linter は期待通り動かない**と定義自身の description に明記されている
- golangci-lint は `go install` / tools パターン / `tool` ディレクティブでのインストールを「動作保証しない。バイナリインストールを推奨」としている（[Local Installation](https://golangci-lint.run/docs/welcome/install/local/)）
- **`language: system` は pre-commit 4.4.0 で `language: unsupported` に改名された**（`script` も `unsupported_script` に）。公式 doc は「previously `language: system`. the alias will be removed in a future version」と書く。旧名は内部で `unsupported` に読み替えられるので今も動くが、逆に **4.4.0 より前の pre-commit は `unsupported` を知らずに設定エラーになる**。新名を使うなら top-level の `minimum_pre_commit_version: '4.4.0'` で下限を宣言する（[pre-commit: unsupported](https://pre-commit.com/#unsupported), [CHANGELOG 4.4.0](https://github.com/pre-commit/pre-commit/blob/main/CHANGELOG.md), `pre_commit/clientlib.py` の `_translate_language()`, [pre-commit: top-level minimum_pre_commit_version](https://pre-commit.com/#top_level-minimum_pre_commit_version)）
- **`language: golang` は「フックの repo を `go install ./...` でビルドして隔離した GOPATH に入れる」ための言語**。`language_version` の既定は「PATH に `go` があれば `system`」で、そのときは環境をそのまま継承する。`language_version` を指定した場合（または PATH に `go` が無い場合）は pre-commit が go.dev/dl から Go を取得し、**専用の `GOROOT` と `GOTOOLCHAIN=local` を設定して実行する**ので、自分の go.mod の `toolchain` 行は効かない。`system` のままでも、CI では setup-go が export した `GOTOOLCHAIN=local`（§7）が継承され、フック repo の `go` 行が CI の Go より新しいとビルドに失敗しうる（[pre-commit: golang](https://pre-commit.com/#golang), [pre_commit/languages/golang.py](https://github.com/pre-commit/pre-commit/blob/main/pre_commit/languages/golang.py) の `get_env_patch()` / `install_environment()`）
- **go.mod の `toolchain` 行による自動切り替えは `go` コマンドだけの仕組み**（[Go Toolchains](https://go.dev/doc/toolchain) が説明するのは `go` コマンドの切り替えのみ）。`go mod tidy` / `go vet` のフックは `go` コマンドの選択に従う（手元の Go が `toolchain` 行より古ければその版へ切り替わり、新しければ手元の版のまま。§1）が、**`gofmt` を直接呼ぶフックは切り替わらず、PATH 上の `gofmt`、つまり手元にインストールした Go に同梱の版**で動く。`golangci-lint fmt` もバイナリに組み込まれた整形器を使うので切り替えの対象外。整形結果の版差は、CI（setup-go 後）でも同じフックを回して検出する
- **`go mod tidy -diff` フック**は、ファイルを書き換えずに差分を出して非ゼロ終了する（§2）。ファイル引数を取らないので `pass_filenames: false`、import の変化も tidy 結果を変えるので `files` に `.go` も含める

```yaml
# .pre-commit-config.yaml
minimum_pre_commit_version: '4.4.0'   # language: unsupported を使うため
repos:
  - repo: https://github.com/pre-commit/pre-commit-hooks
    rev: v6.0.0
    hooks:
      - id: trailing-whitespace
      - id: end-of-file-fixer
      - id: check-yaml

  - repo: local
    hooks:
      - id: golangci-lint-fmt
        name: golangci-lint fmt
        entry: golangci-lint fmt
        language: unsupported      # 4.4.0 より前の名前は system
        types: [go]
        pass_filenames: false
      - id: golangci-lint
        name: golangci-lint
        entry: golangci-lint run --new-from-rev HEAD --fix
        language: unsupported
        types: [go]
        pass_filenames: false
      - id: go-mod-tidy
        name: go mod tidy -diff
        entry: go mod tidy -diff   # 検査だけ。書き換えたいなら go mod tidy
        language: unsupported
        files: '(\.go|go\.mod|go\.sum)$'
        pass_filenames: false

  - repo: https://github.com/gitleaks/gitleaks
    rev: v8.30.1
    hooks:
      - id: gitleaks
```

```bash
pre-commit install
```

（pre-commit-hooks v6.0.0 は 2025-08-09、gitleaks v8.30.1 は 2026-03-21 が最新であることを GitHub Releases で確認。）

---

## 7. CI/CD（GitHub Actions）

### 推奨ワークフロー

**結論**: `actions/setup-go@v7` に **`go-version-file: go.mod`** だけを渡して（`go-version` は併記しない）go.mod を唯一の版の正本にする。lint は `golangci/golangci-lint-action@v9`（版を固定）、脆弱性は **自前の setup-go の後に `tool` ディレクティブで版を固定した `go tool govulncheck`**（`golang/govulncheck-action@v1` を使うなら既定値の罠を回避する）。concurrency は入口の workflow にだけ置き、pull_request のときだけ `cancel-in-progress` にする。

**根拠**:
- setup-go v6.0.0（2025-09-04）から `go-version-file: go.mod` は **`toolchain` 行を優先**し、無ければ `go` 行を使う（v5 以前は `toolchain` 行を読まない）。v7 は内部の ESM 化のみで入出力・挙動の変更なし（[setup-go README](https://github.com/actions/setup-go), [v6.0.0 release](https://github.com/actions/setup-go/releases/tag/v6.0.0)）
- **`go-version` と `go-version-file` を両方渡すと `go-version` が勝つ**（警告 "Both go-version and go-version-file inputs are specified, only go-version will be used" を出すだけで失敗しない。`src/main.ts` の `resolveVersionInput()`, [advanced usage](https://github.com/actions/setup-go/blob/main/docs/advanced-usage.md#using-the-go-version-file-input)）
- `go` 行にパッチ番号があればその版をそのまま入れ、無ければ（`go 1.26`）その系列の最新パッチを探す（advanced usage）。`go mod init` の既定は `go 1.(N-1).0`（§1）なので、**`toolchain` 行が無いと CI は X.Y.0 を入れ**、手元の新しい Go とずれる。アプリは §1 のとおり `toolchain` 行を書く
- setup-go は **v6.0.0 以降、後続 step に `GOTOOLCHAIN=local` を export する**（`src/main.ts` の `setGoToolchain()`。v5.0.0 / v5.5.0 の `main.ts` には無く、README にも書かれていない）。インストールした Go がそのまま使われ、CI 中に toolchain の自動ダウンロードは起きない。したがって `go-version` を手書きして go.mod の `go` 行より古い版を入れると、CI はエラーで止まる（`go` 行以上で `toolchain` 行と違う版を入れた場合は、エラーにならずその版で走る）
- **同じ job で setup-go を 2 回目に呼ぶと `toolchain` 行が読まれない**: `parseGoVersionFile()` は環境の `GOTOOLCHAIN` が既に `local` なら「後方互換のため」`toolchain` 行を飛ばして `go` 行を返す（`src/installer.ts`）。`run()` は版の解決（`resolveVersionInput()`）の後に `setGoToolchain()` を呼ぶので、1 回目は `toolchain` 行を読み、1 回目が export した `GOTOOLCHAIN=local` を見た 2 回目以降（内部で setup-go を呼ぶ composite action を含む）は `go` 行の版を入れる。workflow の `env` に `GOTOOLCHAIN: local` を置いた場合も同じ。setup-go は 1 job 1 回にし、`GOTOOLCHAIN` を workflow の `env` に書かない
- **キャッシュ**: 既定で有効。**v6 から既定のキャッシュキーは go.mod のハッシュ**になったため、依存更新を確実に反映させるなら `cache-dependency-path: go.sum` を指定する（setup-go README "Breaking changes in V6"）
- golangci-lint-action は v4+ で事前の setup-go が必須、v9 は node24 ランタイム必須、キャッシュを内蔵し PR 上に annotation を出す（[golangci-lint-action README](https://github.com/golangci/golangci-lint-action)）。golangci-lint 公式も GitHub では action の利用を推奨（[CI Installation](https://golangci-lint.run/docs/welcome/install/ci/)）
- `GOWORK=off`: §2 の go.work 方針（CI はワークスペースを使わない）

```yaml
# .github/workflows/ci.yml
name: Go CI
on: [push, pull_request]
concurrency:
  # PR は PR 番号でまとめて古い run を中断する。push は run_id で一意にし、互いに中断しない
  group: ${{ github.workflow }}-${{ github.event.pull_request.number || github.run_id }}
  cancel-in-progress: ${{ github.event_name == 'pull_request' }}
permissions:
  contents: read

env:
  GOWORK: "off"   # YAML 1.1 で off が bool 化されないよう引用

jobs:
  test:
    strategy:
      matrix:
        os: [ubuntu-latest, macos-latest, windows-latest]
    runs-on: ${{ matrix.os }}
    steps:
      - uses: actions/checkout@v7
      - uses: actions/setup-go@v7
        with:
          go-version-file: go.mod
          cache-dependency-path: go.sum
      - run: go mod tidy -diff
      - run: go mod verify
      - run: go build ./...
      - run: go test -shuffle=on -coverprofile=cover.out ./...
      - name: race
        if: runner.os != 'Windows'   # -race は cgo + C コンパイラ必須 (§5)。Windows runner の C コンパイラ有無は未確認
        run: go test -race ./...

  lint:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v7
      - uses: actions/setup-go@v7
        with:
          go-version-file: go.mod
          cache-dependency-path: go.sum
      - uses: golangci/golangci-lint-action@v9
        with:
          version: v2.14

  vulncheck:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v7
      - uses: actions/setup-go@v7
        with:
          go-version-file: go.mod   # toolchain 行の Go で標準ライブラリを検査する
          cache-dependency-path: go.sum
      - run: go tool govulncheck ./...   # go.mod の tool ディレクティブで版を固定（§11.2）
```

> golangci-lint-action の README の例は `actions/checkout@v6` / `setup-go@v6` / `version: v2.13` のままで、本例は 2026-09-28 時点の最新版（checkout v7.0.1, setup-go v7.0.0, golangci-lint v2.14.0）に合わせて意図的に変えている。
> ライブラリの場合は `go-version: [stable, oldstable]` の matrix でサポート中 2 系統を検証する（`stable` / `oldstable` エイリアスは setup-go README で確認）。

### govulncheck-action の既定値の罠

**結論**: `golang/govulncheck-action@v1` を既定値のまま使うと、**go.mod の `toolchain` 行ではなく最新 stable の Go で検査される**。上の例のように自前の setup-go（`go-version-file: go.mod`）の後に `go tool govulncheck` を走らせるのを第一候補にする。action を使い続けるなら、**setup-go を呼んでいない job で** `go-version-input: ''` と `go-version-file: go.mod` を渡す。

**根拠**（[action.yml](https://github.com/golang/govulncheck-action/blob/master/action.yml), [README](https://github.com/golang/govulncheck-action)）:
- action は checkout（`repo-checkout` 既定 true）→ setup-go（v6.2.0 に pin）→ `go install golang.org/x/vuln/cmd/govulncheck@latest` → 実行、の composite action。**`go-version-input` の既定値は `'stable'`、`check-latest` の既定は true** で、setup-go へは `go-version` と `go-version-file` の両方を渡す。setup-go は `go-version` を優先する（上記）ので、既定のままでは `go-version-file` は使われない
- source mode の govulncheck は「PATH 上の `go` コマンドの Go 版」を標準ライブラリの版として検査する（[govulncheck doc](https://pkg.go.dev/golang.org/x/vuln/cmd/govulncheck)）。最新 stable で検査すると、**出荷するバイナリの Go（`toolchain` 行）にある標準ライブラリの脆弱性を見逃しうる**
- govulncheck 本体は `@latest` で入るので、版が固定されない。README は action を「currently experimental」としている
- `go-version-input: ''` は効く: runner は `with:` に書かれた input には action.yml の既定値を当てない（[actions/runner `ActionRunner.cs`](https://github.com/actions/runner/blob/main/src/Runner.Worker/ActionRunner.cs) は `!inputs.ContainsKey(key)` のときだけ既定値を評価し、`PipelineTemplateConverter.ConvertToStepInputs()` は空文字の値も key ごと残す）。setup-go は空の `go-version` を未指定として扱い `go-version-file` に戻る
- ただし action 内部の setup-go は同じ job の **2 回目の setup-go** になりうる。先に自前の setup-go を呼んだ job では `GOTOOLCHAIN=local` が export 済みなので、内部の setup-go は `toolchain` 行を飛ばして `go` 行の版を入れる（上記）。action は setup-go を呼ばない独立した job に置く
- 自前の `go tool govulncheck` は、govulncheck の版を go.mod で固定でき、Go の版も lint / test と同じ setup-go の結果に揃う。`tool` ディレクティブの依存は本体と同じ module graph に入る（§11.2）

### concurrency と reusable workflow

**結論**: `concurrency` は **入口の workflow（`pull_request` / `push` / tag push を受けるもの）にだけ置き**、`workflow_call` で呼ばれる本体には置かない。中断は `cancel-in-progress: ${{ github.event_name == 'pull_request' }}` で PR のときだけにする。

**根拠**（[Reusing workflow configurations](https://docs.github.com/en/actions/reference/workflows-and-actions/reusing-workflow-configurations), [Control workflow concurrency](https://docs.github.com/en/actions/how-tos/write-workflows/choose-when-workflows-run/control-workflow-concurrency)）:
- 「reusable workflow が caller から起動されたとき、**`github` コンテキストは常に caller workflow のもの**」。呼ばれた側の `github.workflow` は caller の workflow 名、`github.event_name` も caller のイベントになる。呼ばれた側では `github.event_name` で「workflow_call で呼ばれたか」を判定できない
- 公式の注意: `cancel-in-progress: true` を使うとき、caller と called で同じ `concurrency.group` を使うと実行中の workflow が中断される。「called workflow は `${{ github.workflow }}` に caller の名前を使うので、この値を両方の group に使うと、called workflow が走ったときに caller が中断される」
- `cancel-in-progress` には式を書ける。`github.head_ref` のように pull_request でしか定義されない値を group に使うときは、`github.run_id` 等のフォールバックを付ける（公式例）
- 同じ group では既定で pending は 1 つまで（新しい run が来ると既存の pending は取り消される）。`queue: max` で最大 100 件まで待たせられるが、`cancel-in-progress: true` とは併用できない

```yaml
# .github/workflows/checks.yml — 本体。workflow_call 専用で concurrency を持たない
name: checks
on:
  workflow_call:
permissions:
  contents: read
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v7
      - uses: actions/setup-go@v7
        with:
          go-version-file: go.mod
      - run: go test -race -shuffle=on ./...
```

```yaml
# .github/workflows/release.yml — tag push の入口。checks を呼んでから release する
name: release
on:
  push:
    tags: ["v*"]
concurrency:
  group: ${{ github.workflow }}-${{ github.ref }}
  cancel-in-progress: false   # release は途中で中断しない
permissions:
  contents: read
jobs:
  checks:
    uses: ./.github/workflows/checks.yml   # 呼ばれた側に concurrency が無いので caller を中断しない
  goreleaser:
    needs: checks
    permissions:
      contents: write
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v7
        with:
          fetch-depth: 0
      - uses: actions/setup-go@v7
        with:
          go-version-file: go.mod
      - uses: goreleaser/goreleaser-action@v7
        with:
          version: "~> v2"
          args: release --clean
        env:
          GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}
```

PR / main push の入口（上の `ci.yml`）も、本体を `checks.yml` に寄せるなら `jobs.checks.uses: ./.github/workflows/checks.yml` だけを持ち、`concurrency` は入口側に残す。

---

## 8. セキュリティ

### 推奨: govulncheck を CI に組み込み、Go 本体のパッチ版を追従する

**結論**: 依存脆弱性は **govulncheck**（Go 公式）で、実際に到達する脆弱コードに絞って検出する。Go 本体のパッチリリースも脆弱性修正を含むので `toolchain` 行を上げ続ける。

**根拠**（[Security Best Practices for Go Developers](https://go.dev/doc/security/best-practices)）:
- govulncheck は Go 脆弱性 DB を使い、**呼び出し解析で「実際に影響するもの」を判定**する。CI 用に GitHub Action と `-json` 出力がある
- ソース解析はモジュールディレクトリで `govulncheck ./...`、バイナリは `govulncheck -mode binary <path>`（シンボル表で判定）（[govulncheck doc](https://pkg.go.dev/golang.org/x/vuln/cmd/govulncheck)）
- Go のパッチリリースはセキュリティ修正のために出るので **最新マイナーへ更新する**。セキュリティ修正は golang-announce で事前告知される
- 依存更新は必要だが、無審査の最新化もリスク（悪性コード混入等）。各更新はレビューとテストを経る
- ファジング・`-race`・`go vet` も同ページで推奨されている（§4, §5）
- 改ざん耐性は go.sum + checksum DB（§2）、toolchain ダウンロードも checksum DB で検証（§1）

```bash
go install golang.org/x/vuln/cmd/govulncheck@latest   # または go get -tool（§11）
govulncheck ./...
govulncheck -mode binary ./bin/photo-server
govulncheck -json ./... > vuln.json   # 記録用。検出があっても exit 0
```

- **CI のゲートには text 形式（既定）を使う**。脆弱性があれば非ゼロ終了するが、`-json` / `-format sarif` / `-format openvex` のときは検出数によらず 0 で終わる（[govulncheck doc: Exit codes](https://pkg.go.dev/golang.org/x/vuln/cmd/govulncheck#hdr-Exit_codes)）
- 標準ライブラリの脆弱性は Go の版で決まる。source mode は PATH 上の `go` の版、binary mode はバイナリをビルドした版を基準にする（同 doc）。CI では setup-go に `go-version-file: go.mod` を渡した後で走らせる（§7「govulncheck-action の既定値の罠」）

- 依存更新の自動 PR は Dependabot（`gomod` ecosystem）や Renovate が使えるが、各サービス側の Go 対応詳細は本調査では一次情報未確認。

### gitleaks を CI で PR の commit 範囲に掛ける

**結論**: 秘密情報の検査は、手元の pre-commit（§6）に加えて CI で **PR の commit 範囲**に掛ける。CI では **gitleaks CLI（MIT）を版固定で入れて直接実行する**。`gitleaks/gitleaks-action` は Organization 所有の repo ではライセンスキーが要り、PR の commit 一覧の取り方にも欠落がある。

**根拠**:
- **gitleaks-action の EULA**: v2.0.0 以降は MIT ではなく独自の EULA（v2.0.0 より前は MIT のまま）。**Organization アカウント所有の repo を検査するには `GITLEAKS_LICENSE`（無料だが gitleaks.io での登録が必要）が要り**、Personal アカウント所有なら不要。この要件は action 自身が強制する。EULA は tier ごとに検査できる repo 数の上限を定め、無断の改変を禁じている（[gitleaks-action README](https://github.com/gitleaks/gitleaks-action), [LICENSE.txt](https://github.com/gitleaks/gitleaks-action/blob/master/LICENSE.txt)）。gitleaks CLI 本体は MIT（[gitleaks](https://github.com/gitleaks/gitleaks)）
- **gitleaks-action の PR 範囲の欠落**: pull_request では `GET /repos/{owner}/{repo}/pulls/{pull_number}/commits` を **ページングも `per_page` も付けずに 1 回だけ**呼び、返った配列の先頭と末尾から `--log-opts=--no-merges --first-parent <先頭>^..<末尾>` を組む（[src/gitleaks.js](https://github.com/gitleaks/gitleaks-action/blob/master/src/gitleaks.js)）。この API の `per_page` 既定は 30 なので、**31 個以上の commit を持つ PR では 31 個目以降が検査範囲から落ちる**（commit が古い順に返ることは、action が配列の先頭を起点・末尾を終点に使っていることからの推論）。API 自体も PR あたり最大 250 commit までしか返さない（[REST: List commits on a pull request](https://docs.github.com/en/rest/pulls/pulls#list-commits-on-a-pull-request)）
- gitleaks-action v3.0.0（2026-05-30）は runtime を Node 20 から Node 24 に移しただけで、入出力・挙動は v2 と同じ（README "Migrating from v2 to v3"）。上の 2 点は v3 でも変わらない
- **CLI での範囲指定**: `gitleaks git` は内部で `git log -p` を使い、`--log-opts` で範囲を渡せる。公式例は `gitleaks git -v --log-opts="--all commitA..commitB" path_to_repo`（[gitleaks README](https://github.com/gitleaks/gitleaks)）。PR の base / head の SHA は event payload から取れるので、API を介さず範囲が決まる
- 旧コマンド `gitleaks detect` / `protect` は v8.19.0 で非推奨になった（まだ動くが help から隠れた）。現行の走査モードは `git` / `dir` / `stdin`（gitleaks README）

```yaml
# .github/workflows/gitleaks.yml
name: gitleaks
on: pull_request
permissions:
  contents: read
jobs:
  gitleaks:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v7
        with:
          fetch-depth: 0        # 範囲指定には履歴が要る
      - name: Install gitleaks (pinned)
        env:
          V: 8.30.1
        run: |
          curl -sSfLO "https://github.com/gitleaks/gitleaks/releases/download/v${V}/gitleaks_${V}_linux_x64.tar.gz"
          curl -sSfLO "https://github.com/gitleaks/gitleaks/releases/download/v${V}/gitleaks_${V}_checksums.txt"
          grep " gitleaks_${V}_linux_x64.tar.gz\$" "gitleaks_${V}_checksums.txt" | sha256sum -c -
          tar -xzf "gitleaks_${V}_linux_x64.tar.gz" gitleaks
          sudo install gitleaks /usr/local/bin/gitleaks
      - name: Scan PR commits
        env:
          BASE_SHA: ${{ github.event.pull_request.base.sha }}
          HEAD_SHA: ${{ github.event.pull_request.head.sha }}
        run: gitleaks git --redact --verbose --log-opts="--no-merges --first-parent ${BASE_SHA}..${HEAD_SHA}" .
```

> `--no-merges --first-parent` は gitleaks-action が PR を検査するときの引数に合わせた。`BASE_SHA..HEAD_SHA` の形は本書が組み立てたもので、gitleaks 公式の例ではない。release asset 名（`gitleaks_<版>_linux_x64.tar.gz` / `gitleaks_<版>_checksums.txt`）は v8.30.1 の release で確認した。

---

## 9. ビルド・パッケージング・配布

### go build の基本と再現性フラグ

**結論**: 配布バイナリは `CGO_ENABLED=0`（純 Go で済む場合）+ `-trimpath` + 必要に応じ `-ldflags="-s -w"`。**バージョン埋め込みは Go 1.24+ なら ldflags 不要**になった。

**根拠**:
- `-trimpath` はバイナリからファイルシステムの絶対パスを除去し、`module@version` 形式に置き換える（[cmd/go](https://pkg.go.dev/cmd/go#hdr-Compile_packages_and_dependencies)）
- Go 1.24 から `go build` は VCS のタグ / commit から **main モジュールのバージョンをバイナリに記録**する（未 commit 変更があれば `+dirty`）。`runtime/debug.ReadBuildInfo()` の `Main.Version` で読める。除外は `-buildvcs=false`（[Go 1.24 Release Notes](https://go.dev/doc/go1.24#go-command)）。したがって従来の `-ldflags "-X main.version=..."` は必須ではない（GoReleaser は既定で引き続き `-X main.version` 等を付与する）
- `go version -m <binary>`（1.25 で `-json` 追加）で埋め込まれた依存とビルド設定を確認できる
- クロスコンパイルは `GOOS` / `GOARCH` の指定のみ（純 Go の場合）

```bash
CGO_ENABLED=0 go build -trimpath -ldflags="-s -w" -o bin/photo-manager ./cmd/photo-manager
GOOS=linux GOARCH=arm64 CGO_ENABLED=0 go build -trimpath -o bin/photo-server-linux-arm64 ./cmd/photo-server
go version -m bin/photo-manager
go install github.com/example/photo-manager/cmd/photo-manager@latest   # 利用者側のインストール
```

```go
// バージョン表示（Go 1.24+）
if bi, ok := debug.ReadBuildInfo(); ok {
	fmt.Println(bi.Main.Version)
}
```

### GoReleaser（バイナリ配布）

**結論**: GitHub Releases / Homebrew / パッケージ等へのマルチプラットフォーム配布は **GoReleaser v2** を使う。

**根拠**（[GoReleaser Quick Start](https://goreleaser.com/getting-started/quick-start/), [Go builds](https://goreleaser.com/customization/builds/builders/go/)）:
- `goreleaser init` で設定生成、`goreleaser check` で検証、`goreleaser release --snapshot --clean` でタグなしローカル試行、`goreleaser build --single-target` で手元向け単体ビルド
- 既定 ldflags は `-s -w -X main.version={{.Version}} -X main.commit={{.Commit}} -X main.date={{.Date}} -X main.builtBy=goreleaser`、既定 goos は darwin/linux/windows、goarch は 386/amd64/arm64
- 再現可能ビルド向けに `mod_timestamp: "{{ .CommitTimestamp }}"` や `-trimpath` を設定例に持つ
- GitHub への公開には `GITHUB_TOKEN` が必要

```yaml
# .goreleaser.yaml（抜粋）
version: 2
builds:
  - id: photo-manager
    main: ./cmd/photo-manager
    env: [CGO_ENABLED=0]
    flags: [-trimpath]
    mod_timestamp: "{{ .CommitTimestamp }}"
```

```bash
goreleaser check
goreleaser release --snapshot --clean
```

> `.goreleaser.yaml` 冒頭の `version: 2` は GoReleaser v2 の設定形式を示す（`goreleaser init` の雛形 [internal/static/config.yaml](https://github.com/goreleaser/goreleaser/blob/main/internal/static/config.yaml) も `version: 2` で始まることを確認）。

### ko（コンテナイメージ）

**結論**: Go サーバーを **Dockerfile なし**でコンテナ化するなら ko。

**根拠**（[ko: Get Started](https://ko.build/get-started/)）: `KO_DOCKER_REPO` に push 先を設定し `ko build ./cmd/photo-server` で `package main` をビルドしてイメージを push、digest を出力する。Docker デーモン不要、認証は Docker config または `ko login`。

```bash
export KO_DOCKER_REPO=ghcr.io/example/photo-manager
ko build ./cmd/photo-server
```

---

## 10. 環境変数・シークレット管理

**結論**: Go 固有の仕組みは薄い。**アプリ設定は `os.Getenv` / `os.LookupEnv` で環境変数から読む**のが標準ライブラリでできる範囲。`.env` ファイル読み込みは標準ライブラリに無い。

**根拠と注意**:
- 標準ライブラリに dotenv 相当はない（`os` パッケージの `Getenv` / `LookupEnv` のみ）。よく使われる `github.com/joho/godotenv` は **最新安定版が v1.5.1（2023-02-05）で、以降は v1.6.0 の pre-release のみ**（GitHub Releases で確認）。採用するなら保守状況を踏まえ、本番では実環境変数・シークレットストアから注入し `.env` は開発用に限定するのが安全（この方針は本レポートの推奨）
- **`go env -w` と混同しない**: `go env -w` が書くのは Go ツールチェーン用の設定（GOPRIVATE 等）で、アプリの環境変数ではない（[cmd/go: go env](https://pkg.go.dev/cmd/go#hdr-Print_Go_environment_information)）
- **ldflags `-X` でシークレットを埋め込まない**: 文字列はバイナリに平文で残る（`-X` が文字列変数を設定する仕組みからの推論。一次情報での明示は未確認）
- Go 固有のシークレット面:
  - プライベートモジュールの資格情報は Git 資格情報 / `.netrc` / `GOAUTH`（Go 1.24+）で渡し、リポジトリに置かない（[ref/mod: private module auth](https://go.dev/ref/mod#private-module-repo-auth), [Go 1.24](https://go.dev/doc/go1.24#go-command)）
  - CI では `GOPRIVATE` + トークンを secrets 経由で git 設定に注入する
- 漏洩検知は gitleaks（§6 の pre-commit と §8 の PR 範囲検査）

```go
port, ok := os.LookupEnv("PHOTO_SERVER_PORT")
if !ok {
	port = "8080"
}
```

```bash
# .gitignore
.env
.env.*
!.env.example
```

---

## 11. ツールチェーン一貫性

### 11.1 Go 本体: IDE / gopls / ターミナル / CI が同じ Go を使うか

**結論**: go.mod の `toolchain` 行を正本にすると、各経路は次のように揃う。

| 経路 | どの Go が使われるか | 根拠 |
|---|---|---|
| ターミナル | PATH 上の `go` → `GOTOOLCHAIN=auto` により `toolchain` 行の版へ自動切替 | [Go Toolchains](https://go.dev/doc/toolchain) |
| gopls / IDE | gopls は PATH 上の `go` を呼ぶ → 同じく自動切替。ただし **エディタの環境変数を継承**するので、エディタ起動時の PATH / GOTOOLCHAIN が異なると別の Go になる | [gopls docs](https://go.dev/gopls/) |
| mise | idiomatic version file 有効時、`toolchain` 行を読む | [mise: Go](https://github.com/jdx/mise/blob/main/docs/lang/go.md) |
| CI (setup-go v6+) | `go-version-file: go.mod` で `toolchain` 行を読み、後続 step に `GOTOOLCHAIN=local` を export。`go-version` を併記すると `go-version` が勝ち、同じ job の 2 回目の setup-go は `go` 行を読む（§7） | [setup-go](https://github.com/actions/setup-go) |
| pre-commit | `language: unsupported` の `go` コマンドは手元と同じ切り替え。`gofmt` 直接呼び出しは手元の Go の版。`language: golang` + `language_version` は専用 `GOROOT` と `GOTOOLCHAIN=local` で go.mod を無視（§6） | [pre-commit: golang](https://pre-commit.com/#golang) |
| govulncheck-action | 既定の `go-version-input: stable` が `go-version-file` に勝ち、最新 stable で検査（§7） | [action.yml](https://github.com/golang/govulncheck-action/blob/master/action.yml) |

- 確認コマンド: `go version`（選択後）、`go env GOTOOLCHAIN`、`GODEBUG=toolchaintrace=1 go version`
- **静的解析ツールも Go の版に縛られる**: golangci-lint は「ビルドに使った Go の言語版が、対象コードの `go` 行より低い」とエラーにする（`pkg/goutil/version.go`: `the Go language version (%s) used to build golangci-lint is lower than the targeted Go version (%s)`）。golangci-lint のリリースバイナリは v2.12.0 が Go 1.26、**v2.13.0 以降（v2.14.0 含む）が Go 1.27.0 でビルド**されている（各タグの `.github/workflows/release.yml` の `GO_VERSION`）。よって `go 1.27` のコードには v2.13.0 以上が必要。staticcheck も Go 1.27 の新構文対応は 2026.2 から。**`go` 行を上げたら lint ツールも新しい Go でビルドされた版に上げる**必要がある

### 11.2 開発ツールのバージョン固定: `tool` ディレクティブ（Go 1.24+）vs tools.go

**結論**: `stringer` / `mockgen` / `govulncheck` のような **Go 製の補助ツールは go.mod の `tool` ディレクティブで固定**する（tools.go 慣習は不要になった）。ただし **golangci-lint は例外**で、公式が `tool` での導入を推奨していないので、**バージョン固定したバイナリ**（CI は action、手元はインストールスクリプト / Homebrew / mise(aqua)）で入れる。

**根拠**:
- Go 1.24 で `tool` ディレクティブが追加され、「`tools.go` にブランク import する従来の回避策が不要になった」。`go get -tool` で追加、`go tool <名前>` で実行、`tool` メタパターンで一括更新（`go get tool`）・一括インストール（`go install tool`）。`go tool` で実行するツールはビルドキャッシュに乗る（[Go 1.24 Release Notes](https://go.dev/doc/go1.24#go-command), [Managing dependencies: Tool dependencies](https://go.dev/doc/modules/managing-dependencies#tools)）
- ツールの依存も通常の require として **MVS に参加**する（プロジェクト本体の依存バージョンに影響し得る）（同上）
- golangci-lint は「`go install` / tools パターン / `tool` ディレクティブは動作保証しない」「ツールの依存が他ツールやプロジェクトの依存を書き換え、未テストのバイナリになる」「`go tool` は推奨しない」と明記。どうしても `go tool` を使うなら **専用 modfile（`-modfile=golangci-lint.mod`）や専用モジュールで隔離**する方法を示している（[Local Installation](https://golangci-lint.run/docs/welcome/install/local/)）

```bash
# 補助ツールを go.mod に固定（Go 1.24+）
go get -tool golang.org/x/tools/cmd/stringer@latest
go get -tool golang.org/x/vuln/cmd/govulncheck@v1.8.0
go tool stringer -type=Kind ./internal/storage
go tool govulncheck ./...
go get tool          # 全ツールを更新
go tool              # 利用可能なツール一覧

# golangci-lint はバイナリで版固定
curl -sSfL https://golangci-lint.run/install.sh | sh -s -- -b "$(go env GOPATH)/bin" v2.14.0
# または
mise use golangci-lint@2.14.0

# どうしても go tool で使う場合（公式の隔離方法）
go mod init -modfile=golangci-lint.mod github.com/example/photo-manager/golangci-lint
go get -tool -modfile=golangci-lint.mod github.com/golangci/golangci-lint/v2/cmd/golangci-lint@v2.14.0
go tool -modfile=golangci-lint.mod golangci-lint run
```

```
// go.mod（抜粋）
tool (
	golang.org/x/tools/cmd/stringer
	golang.org/x/vuln/cmd/govulncheck
)
```

---

## 12. 最新性・廃止手順の回避（古い記事の手順を踏まない）

| 古い手順 | 現行の手順 | 根拠 |
|---|---|---|
| `GOPATH` 配下にソースを置く / `GO111MODULE=off` | モジュールモードのみを使う。GOPATH モードでの `go get` は Go 1.22 で廃止（`go build`/`go test` は動作継続） | [Go 1.22 Release Notes](https://go.dev/doc/go1.22) |
| `go get example.com/cmd` でバイナリをインストール | `go install example.com/cmd@latest`（`go get` は Go 1.18 から依存調整専用で、ビルド・インストールしない） | [Go 1.18 Release Notes](https://go.dev/doc/go1.18) |
| `tools.go` にブランク import してツール版を固定し `go run` で呼ぶ | go.mod の `tool` ディレクティブ + `go tool`（Go 1.24+）。golangci-lint のみバイナリ固定（§11.2） | [Go 1.24 Release Notes](https://go.dev/doc/go1.24) |
| `.golangci.yml` の v1 形式（`linters-settings`、`enable-all`/`disable-all`、`gosimple`/`stylecheck` 個別指定、`linters` 内の `gofmt`/`goimports`、`run.timeout` 前提） | v2 形式（`version: "2"`、`linters.default`、`linters.settings`、`formatters`、`linters.exclusions`）。`golangci-lint migrate` で自動変換（**コメントは移行されない**、非推奨オプション・不明フィールドも移行されない） | [Migration guide](https://golangci-lint.run/docs/product/migration-guide/) |
| golangci-lint v2 での注意 | v2 はタイムアウト既定なし、`issues.exclude-generated` 既定が `lax` → `strict`（migrate は旧挙動維持のため明示追加）、`typecheck` は linter ではなくなった | 同上 |
| `go install github.com/golangci/golangci-lint/cmd/golangci-lint`（v1 のパス） | v2 のモジュールパスは `github.com/golangci/golangci-lint/v2/cmd/golangci-lint`。そもそもバイナリ導入推奨 | [Local Installation](https://golangci-lint.run/docs/welcome/install/local/) |
| `go mod tidy -go=1.xx` で Go 版を上げる | `go get go@1.xx`（旧手順も有効だが後者を推奨） | [Go Toolchains](https://go.dev/doc/toolchain#get) |
| go.mod の `go` 行を上げると `toolchain` 行も自動で入る前提 | Go 1.25 から自動では付かない。必要なら `go get toolchain@go1.x.y` | [Go 1.25 Release Notes](https://go.dev/doc/go1.25) |
| `go tool doc` | `go doc`（`cmd/doc` と `go tool doc` は Go 1.26 で削除） | [Go 1.26 Release Notes](https://go.dev/doc/go1.26) |
| 旧 `go fix` の fixer（全て陳腐化） | Go 1.26 で `go fix` は modernizer 基盤に刷新 | 同上 |
| `for _, tt := range tests { tt := tt; ... }` | 不要（Go 1.22 のループ変数セマンティクス変更。`go` 行 1.22+） | [Go 1.22 Release Notes](https://go.dev/doc/go1.22#language) |
| ベンチマークの `for i := 0; i < b.N; i++` | `for b.Loop() { ... }`（Go 1.24+、1.26 でインライン化問題解消） | [Go 1.24](https://go.dev/doc/go1.24), [Go 1.26](https://go.dev/doc/go1.26) |
| `GOEXPERIMENT=synctest` | `testing/synctest` は Go 1.25 で正式化 | [Go 1.25 Release Notes](https://go.dev/doc/go1.25) |
| setup-go v5 以前の前提（node20、キャッシュキー go.sum、`toolchain` 行を読まない） | setup-go v6+（node24、toolchain 行対応、キャッシュキーは go.mod 既定 → `cache-dependency-path: go.sum` 推奨）、最新は v7 | [setup-go README](https://github.com/actions/setup-go) |
| pre-commit の `language: system` / `language: script` | `language: unsupported` / `unsupported_script`（4.4.0 で改名。旧名の alias は将来削除予定。新名を使うなら `minimum_pre_commit_version: '4.4.0'`） | [pre-commit: unsupported](https://pre-commit.com/#unsupported) |
| `golang/govulncheck-action` を既定値のまま使う | 自前の setup-go（`go-version-file: go.mod`）+ `go tool govulncheck`（§7） | [action.yml](https://github.com/golang/govulncheck-action/blob/master/action.yml) |
| gitleaks-action を Organization 所有 repo で使う / `gitleaks detect` / `protect` | 版固定した gitleaks CLI の `gitleaks git --log-opts=...` で PR 範囲を検査（§8）。`detect` / `protect` は v8.19.0 で非推奨 | [gitleaks-action README](https://github.com/gitleaks/gitleaks-action), [gitleaks README](https://github.com/gitleaks/gitleaks) |
| golangci-lint-action の `skip-pkg-cache` / `skip-build-cache` | v5 で削除（Go のキャッシュは setup-go が担当）。v4+ は setup-go 事前実行必須、v9 は node24 必須 | [golangci-lint-action README](https://github.com/golangci/golangci-lint-action) |
| Bazaar (`bzr`) ホストのモジュール取得 | Go 1.27 で `bzr` サポート削除 | [Go 1.27 Release Notes](https://go.dev/doc/go1.27#go-command) |
| macOS 12 以前での Go 開発 | Go 1.27 は macOS 13 Ventura 以上が必須 | [Go 1.27 Release Notes](https://go.dev/doc/go1.27#darwin) |
| golang-standards/project-layout を「標準」として採用 | 公式 [Organizing a Go module](https://go.dev/doc/modules/layout) に従う（§3） | 同リポジトリ README |

---

## 13. 推奨構成まとめ（シンプルさ優先）

### バージョン管理
- **go.mod の `go` + `toolchain` 行が正本**、`GOTOOLCHAIN=auto` に任せる。mise は任意（使うなら `toolchain` 行を読ませる）

### 依存関係
- **Go modules のみ**。go.mod / go.sum を commit、go.work は原則 commit しない、vendoring は要件がある時のみ
- プライベートは `GOPRIVATE`

### 構造
- `cmd/<name>/main.go` + `internal/`（公式レイアウト）

### 開発ツール
- **フォーマット**: gofmt + goimports（任意で gofumpt）を golangci-lint `formatters` から
- **静的解析**: go vet（go test が自動実行）+ golangci-lint v2（`default: standard` に staticcheck 内包）
- **テスト**: 標準 `go test`、テーブル駆動、`-race`、fuzz、`testing/synctest`
- **補助ツール**: go.mod `tool` ディレクティブ。golangci-lint のみバイナリで版固定
- **フック**: pre-commit から golangci-lint と `go mod tidy -diff`（`repo: local` + `language: unsupported`）+ gitleaks

### CI
- setup-go@v7（`go-version-file: go.mod` のみ、`cache-dependency-path: go.sum`）、golangci-lint-action@v9（版固定）、`go tool govulncheck`（自前の setup-go の後）、`go mod tidy -diff`、`GOWORK=off`
- concurrency は入口の workflow にだけ置き、pull_request のときだけ `cancel-in-progress`
- gitleaks は版固定の CLI で PR の commit 範囲を検査

### 配布
- `CGO_ENABLED=0 go build -trimpath`、バージョンは BuildInfo（Go 1.24+）
- GoReleaser v2（バイナリ）、ko（コンテナ）

### クイックスタート

```bash
mkdir photo-manager && cd photo-manager
go mod init github.com/example/photo-manager
go get go@1.27.0 toolchain@go1.27.1
mkdir -p cmd/photo-manager internal/storage

go get -tool golang.org/x/vuln/cmd/govulncheck@v1.8.0
curl -sSfL https://golangci-lint.run/install.sh | sh -s -- -b "$(go env GOPATH)/bin" v2.14.0

go build ./...
go test -race ./...
golangci-lint run
go tool govulncheck ./...
```

---

## 未確認のまま残った主張

- asdf の Go プラグインの挙動（§1。扱っていない）
- Dependabot / Renovate の Go modules 対応詳細（§8）
- ldflags `-X` で埋めた文字列がバイナリに平文で残ることの一次情報での明示（§10。仕組みからの推論）
- gopls v0.23.0 / govulncheck v1.8.0 の日付は GitHub Release ではなく tag の commit 日（公式アナウンス日と数日ずれる可能性）
- Windows runner（windows-latest）で `-race` が動くか（C コンパイラの有無。§7 では Windows を除外）
- アプリ/ライブラリで `go` 行・`toolchain` 行を使い分ける表（§1）は公式記述からの推論を含む
- reusable workflow（`workflow_call` で呼ばれる側）の **workflow レベルの `concurrency`** が評価されるか・どう振る舞うかは GitHub Docs に明記が無い（§7 は入口の workflow にだけ置く構成で回避）
- `language: golang` のフックを CI でビルドするとき、フック repo の `go` 行が setup-go の Go より新しいと失敗する点は `GOTOOLCHAIN=local` の継承からの推論で、実測していない（§6）

## 参考資料（一次情報）

- [Go Toolchains](https://go.dev/doc/toolchain)
- [Go Modules Reference](https://go.dev/ref/mod)
- [Managing dependencies](https://go.dev/doc/modules/managing-dependencies)
- [Organizing a Go module](https://go.dev/doc/modules/layout)
- [Release History / Policy](https://go.dev/doc/devel/release)
- Release Notes: [Go 1.27](https://go.dev/doc/go1.27) / [1.26](https://go.dev/doc/go1.26) / [1.25](https://go.dev/doc/go1.25) / [1.24](https://go.dev/doc/go1.24) / [1.23](https://go.dev/doc/go1.23) / [1.22](https://go.dev/doc/go1.22) / [1.18](https://go.dev/doc/go1.18)
- [Security Best Practices for Go Developers](https://go.dev/doc/security/best-practices)
- [Data Race Detector](https://go.dev/doc/articles/race_detector)
- [Coverage for integration tests](https://go.dev/doc/build-cover)
- [cmd/go](https://pkg.go.dev/cmd/go)
- [govulncheck](https://pkg.go.dev/golang.org/x/vuln/cmd/govulncheck)
- [gopls](https://go.dev/gopls/)
- [Go Wiki: TableDrivenTests](https://go.dev/wiki/TableDrivenTests)
- [golangci-lint docs](https://golangci-lint.run/) / [.golangci.reference.yml](https://github.com/golangci/golangci-lint/blob/main/.golangci.reference.yml)
- [golangci-lint-action](https://github.com/golangci/golangci-lint-action)
- [actions/setup-go](https://github.com/actions/setup-go)
- [golang/govulncheck-action](https://github.com/golang/govulncheck-action) / [action.yml](https://github.com/golang/govulncheck-action/blob/master/action.yml)
- [actions/runner: ActionRunner.cs（input の既定値の適用）](https://github.com/actions/runner/blob/main/src/Runner.Worker/ActionRunner.cs)
- [pre-commit](https://pre-commit.com/) / [pre_commit/languages/golang.py](https://github.com/pre-commit/pre-commit/blob/main/pre_commit/languages/golang.py)
- [gitleaks](https://github.com/gitleaks/gitleaks) / [gitleaks-action](https://github.com/gitleaks/gitleaks-action)
- GitHub Docs: [Reusing workflow configurations](https://docs.github.com/en/actions/reference/workflows-and-actions/reusing-workflow-configurations), [Control workflow concurrency](https://docs.github.com/en/actions/how-tos/write-workflows/choose-when-workflows-run/control-workflow-concurrency), [REST: List commits on a pull request](https://docs.github.com/en/rest/pulls/pulls#list-commits-on-a-pull-request)
- [Staticcheck](https://staticcheck.dev/) / [releases](https://github.com/dominikh/go-tools/releases)
- [gofumpt](https://github.com/mvdan/gofumpt)
- [GoReleaser](https://goreleaser.com/)
- [ko](https://ko.build/)
- [mise: Go](https://mise.jdx.dev/lang/go.html)
