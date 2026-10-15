# Google Apps Script（clasp + TypeScript）開発環境のベストプラクティス（2026年版）

**対象**: Google Apps Script（GAS）のプロジェクト（スタンドアロン / コンテナバインド / Web アプリ / API 実行可能）を、TypeScript で書いて clasp でローカル開発・デプロイする構成
**最終更新**: 2026-10-02

> 姉妹版 `node-dev-env-best-practices.md` が TypeScript / Node.js 一般の正本です。パッケージマネージャー、Node バージョン管理の方法、formatter / linter の選定、テストフレームワークの選定、pre-commit の仕組み、依存脆弱性ツールは node reference に従い、本書では繰り返しません。本書が扱うのは GAS / clasp 固有の差分だけです（重複させると、node 側だけが更新されたときに食い違ったまま古くなるため）。観点の番号は `go-dev-env-best-practices.md` と揃えています。

---

## 0. バージョン早見表（2026-10-02 時点で確認）

| 対象 | 最新 | 日付 | 確認元 |
|---|---|---|---|
| clasp（`@google/clasp`） | **3.4.1** | 2026-08-28 | [npm](https://www.npmjs.com/package/@google/clasp)（`dist-tags.latest`）/ [GitHub Releases](https://github.com/google/clasp/releases)（tag `clasp-v3.4.1`。3.3.0 までの tag は `v3.3.0` 形式で、3.4.0 から `clasp-v` 接頭辞に変わった） |
| clasp 3 系の起点 | 3.0.0-alpha（**TypeScript 変換を廃止**）→ 最初の非 alpha は 3.1.0 | 2025-03-11 / 2025-10-15 | [CHANGELOG](https://github.com/google/clasp/blob/clasp-v3.4.1/CHANGELOG.md) |
| clasp が要求する Node | `engines.node` は **`>=20.0.0`**。README の Troubleshooting は **`>=22.0.0`**、公式ガイド本文は「20.0.0 以降」（同ページ冒頭の要約は「4.7.4 以降」のまま） | — | [package.json](https://github.com/google/clasp/blob/clasp-v3.4.1/package.json), [README](https://github.com/google/clasp/blob/clasp-v3.4.1/README.md#nodejs-version), [Use the CLI with clasp](https://developers.google.com/apps-script/guides/clasp) |
| Node.js LTS | 24.21.0（Krypton。2026-10-20 に maintenance 入り、EOL 2028-04-30）/ 22.23.3（Jod。EOL 2027-04-30）。**20 系は 2026-04-30 に EOL** | 2026-09-07 / 2026-09-23 | [nodejs/Release schedule.json](https://github.com/nodejs/Release/blob/main/schedule.json), [nodejs.org/dist/index.json](https://nodejs.org/dist/index.json) |
| `@types/google-apps-script` | **2.0.13** | 2026-09-05 | [npm](https://www.npmjs.com/package/@types/google-apps-script) / [DefinitelyTyped](https://github.com/DefinitelyTyped/DefinitelyTyped/tree/master/types/google-apps-script)（2.0.0 は 2025-08-25、Advanced Services を `\| undefined` にした 2.0.8 は 2025-11-24） |
| Rollup（推奨 bundler） | **4.63.6** | 2026-10-01 | [npm](https://www.npmjs.com/package/rollup) |
| rollup-plugin-esbuild（TS 変換） | 6.2.1 | 2025-02-27（以降リリースなし） | [npm](https://www.npmjs.com/package/rollup-plugin-esbuild) |
| esbuild | 0.28.2 | 2026-08-08 | [npm](https://www.npmjs.com/package/esbuild) |
| TypeScript | 7.0.2（6 系の最終は 6.0.3、2026-04-16） | 2026-07-08 | [npm](https://www.npmjs.com/package/typescript)。版の選定自体は node reference に従う |
| `@rollup/plugin-typescript` | 12.3.0 — **TypeScript 7 では動かない**（§4） | 2025-10-23 | [npm](https://www.npmjs.com/package/@rollup/plugin-typescript), [rollup/plugins#2016](https://github.com/rollup/plugins/issues/2016) |
| GAS 向け bundler plugin | **採用しない**（§9 の 10 行の設定で足りる）。候補: rollup-plugin-google-apps-script 2.0.2（2025-02-16）、gas-webpack-plugin 2.6.0（2024-05-23）、esbuild-gas-plugin 0.10.0（2025-07-22）、@gas-plugin/unplugin 0.2.2（2026-07-18） | — | 各 [npm](https://www.npmjs.com/package/rollup-plugin-google-apps-script) / 公式 repo（不採用の理由は §9） |
| Vitest | 5.0.3（`engines.node`: `^22.12.0 \|\| ^24.0.0 \|\| >=26.0.0`） | 2026-09-30 | [npm](https://www.npmjs.com/package/vitest)。選定は node reference に従う |
| vitest-mock-extended | 5.1.1（peer `vitest >=4.0.0`、`typescript` 7.x まで） | 2026-08-02 | [npm](https://www.npmjs.com/package/vitest-mock-extended)。§5.1 |
| actions/checkout | v7.0.1 | 2026-07-20 | [GitHub Releases](https://github.com/actions/checkout/releases) |
| actions/setup-node | v7.0.0 | 2026-07-14 | [GitHub Releases](https://github.com/actions/setup-node/releases) |
| Apps Script ランタイム | **V8**。Rhino は 2026-01-31 に deprecated | — | [Migrate scripts to V8](https://developers.google.com/apps-script/guides/v8-runtime/migration) |
| （参考）google/aside | 1.5.0 | 2026-06-18 | [GitHub Releases](https://github.com/google/aside/releases)（Google の TS テンプレート生成ツール。ただし README は「not an officially supported Google product」） |

---

## 1. ランタイム / 言語バージョン管理

GAS 開発では「どのランタイムの版を固定するか」を 3 つに分けて考える。

| 層 | 何が決めるか | 固定する場所 |
|---|---|---|
| ローカルの Node（clasp・bundler・テストが動く） | node reference の方法 | `.nvmrc` 等（node reference に従う） |
| GAS 上の JavaScript ランタイム | `appsscript.json` の `runtimeVersion` | `appsscript.json`（commit する） |
| 出力 JS の構文レベル | bundler の `target` と tsconfig の `target` / `lib` | `rollup.config.mjs` と `tsconfig.json` |

### 1.1 ローカルの Node: **22.12 以上の LTS（推奨は 24 LTS）**

**結論**: 版の固定方法（mise / fnm / Volta、`.nvmrc`、`engines`）は node reference に従う。版の下限だけが GAS 固有の論点で、**22.12 以上**にする。

**根拠**:
- clasp 3.4.1 の `engines.node` は `>=20.0.0` だが、README の Troubleshooting は「requires NodeJS version >= 22.0.0」と書き、3.4.0 の CHANGELOG には「update google-auth-library and googleapis to resolve Node 22+/25+ compatibility issues」がある。一次情報どうしが一致していない（[package.json](https://github.com/google/clasp/blob/clasp-v3.4.1/package.json), [README](https://github.com/google/clasp/blob/clasp-v3.4.1/README.md#nodejs-version), [CHANGELOG](https://github.com/google/clasp/blob/clasp-v3.4.1/CHANGELOG.md)）
- Node 20 は 2026-04-30 に EOL（[schedule.json](https://github.com/nodejs/Release/blob/main/schedule.json)）。`engines` の下限 20 は今は選べない
- Vitest 5 の `engines.node` は `^22.12.0 || ^24.0.0 || >=26.0.0`（npm レジストリ）。テストまで同じ Node で回すなら 22.12 が実質の下限
- clasp 自身の CI は Node 20 の 1 系統だけを回し、release workflow は Node 22 を使う（[`.github/workflows/ci.yaml`](https://github.com/google/clasp/blob/clasp-v3.4.1/.github/workflows/ci.yaml), [`release.yaml`](https://github.com/google/clasp/blob/clasp-v3.4.1/.github/workflows/release.yaml)）。24 / 26 での動作は上流 CI では保証されていない。本調査では clasp 3.4.1 が Node 26.10.0 で `clasp --version` / `show-file-status` / `show-authorized-user` まで動くことを手元で確かめた

```jsonc
// package.json（抜粋）— engines の書き方そのものは node reference に従う
{
  "engines": { "node": ">=22.12.0" }
}
```

### 1.2 GAS ランタイム: `appsscript.json` に `"runtimeVersion": "V8"` を明示する

**結論**: `runtimeVersion` は省略せず必ず `"V8"` を書く。

**根拠**:
- manifest リファレンスは今も「省略時は既定ランタイム（`STABLE`）。`STABLE`: Default runtime (currently Rhino)」と書く。`DEPRECATED_ES5` も Rhino（[Manifest structure](https://developers.google.com/apps-script/manifest)）
- 一方で移行ガイドは「The Rhino runtime was deprecated on January 31, 2026」「データリージョンポリシーを強制する組織では Rhino 上のスクリプトはサポートされない」と書く（[Migrate scripts to V8](https://developers.google.com/apps-script/guides/v8-runtime/migration)）
- 省略時の挙動について両ページが食い違って見えるので、明示して曖昧さを消す

### 1.3 出力 JS の構文レベル: `target` は **ES2020**、ES2022 以上にしない

**結論**: bundler の `target` と tsconfig の `target` は `es2020` にする。これは好みではなく正しさの条件で、**GAS の V8 は ES2022 のクラス構文の一部を受け付けない**。

**根拠**（[V8 runtime overview: Class limitations](https://developers.google.com/apps-script/guides/v8-runtime#class_limitations)）:
- 「Private class fields (for example, `#field`) aren't supported and cause parsing errors」
- 「Direct static field declarations within the class body (for example, `static count = 0;`) aren't supported」
- TypeScript / esbuild は target が ES2022 未満なら、private field を `WeakMap`、static field をクラス定義後の代入に下げる。本調査で `target: "es2020"` の esbuild（rollup-plugin-esbuild 経由）が `#n` と `static instances = 0` を `WeakMap` と `_Counter.instances = 0` に変換することを確かめた
- GAS の V8 がどの ECMAScript 版まで対応するかは公式に版番号で書かれていない。ES2021 の構文（論理代入演算子など）が通るかは未確認なので、確かめた ES2020 に留める（Google の [aside](https://github.com/google/aside/blob/main/tsconfig.template.json) のテンプレートも `es2020`）
- **判定**: どの transpiler を使っても、`dist/` に `#` で始まるフィールドとクラス本体内の `static x =` が残っていなければよい

---

## 2. 依存関係・パッケージ管理

**結論**: パッケージマネージャーと lockfile の扱いは node reference に従う。GAS 固有の差分は次の 4 点。

1. **clasp は devDependency として版を固定し、`npx clasp` / `npm exec clasp` で呼ぶ**（README の `npm install -g @google/clasp` を CI やチームの正本にしない）。clasp は Google アカウントの refresh token を読む CLI で、3.2.0 と 3.4.0 にパストラバーサル・OAuth CSRF の修正が入っている（[CHANGELOG](https://github.com/google/clasp/blob/clasp-v3.4.1/CHANGELOG.md)）。lockfile で版を固定し、更新はレビューを通す（この方針は本書の判断。公式ガイドの CI 例も `npm ci` 後に `npx @google/clasp` を呼ぶ）
2. **runtime の npm 依存は bundle されて GAS 上で動く**。`node_modules` は GAS へは送られず、bundler が 1 ファイルに inline する。したがって Node / ブラウザの API に依存するパッケージは GAS では動かない（`setTimeout`、`fetch`、`URL`、`TextEncoder`、`atob`、`crypto`、`process` などは「NOT available in the Apps Script V8 runtime」— [V8 runtime limitations](https://developers.google.com/apps-script/guides/v8-runtime#unavailable-apis)）。依存を足すときは GAS 上で 1 度動かして確かめる（型検査ではパッケージ内部の API 使用は検出できない。§11）
3. **型定義は `@types/google-apps-script` を devDependency に入れる**。HtmlService のクライアント側（`google.script.run`）を TS で書くなら `@types/google.script.client-side`（0.1.5、2023-11-21 から更新なし）を別の tsconfig で使う
4. **Apps Script ライブラリ（manifest の `dependencies.libraries`）より npm パッケージの bundle を優先する**。公式は「A script that uses a library doesn't run as quickly as it would if all the code were contained within a single script project」「use them sparingly in projects where speed is critical」と書く（[Libraries](https://developers.google.com/apps-script/guides/libraries)）。ライブラリを使うなら `version` を数値で固定し、`developmentMode: true`（HEAD を使う）は本番に残さない（[Manifest: Dependencies](https://developers.google.com/apps-script/manifest/dependencies)）

```bash
# 例は npm で書く。パッケージマネージャーは node reference に従って置き換える
npm i -D @google/clasp @types/google-apps-script rollup rollup-plugin-esbuild esbuild typescript
```

---

## 3. プロジェクト構造・初期化

### 3.1 推奨レイアウト: ソースと push 対象（`dist/`）を分け、`rootDir` を `dist` にする

**結論**: TypeScript は `src/`、bundle 結果と manifest のコピーは `dist/` に出し、clasp の `rootDir` を `dist` にする。clasp が見るのは `dist/` だけになる。

```
gas-app/
├── src/
│   ├── main.ts             # GAS に公開する関数だけを export する（§9）
│   ├── usecase.ts          # port だけに依存する処理（§5.1）
│   ├── ports.ts            # 表の読み書きなどの境界（§5.1）
│   ├── gas/                # GAS の Sheet / SpreadsheetApp 等に触れる薄い adapter（§5.1）
│   └── domain/             # 純粋なロジック（テストの主対象）
├── test/
│   ├── fakes/              # port の in-memory fake（§5.1）
│   └── usecase.test.ts
├── appsscript.json         # manifest の正本（build で dist/ へコピー）
├── dist/                   # bundle 結果（.gitignore）
│   ├── main.js
│   └── appsscript.json
├── .clasp.json             # dev 用（commit 方針は §3.4）
├── .clasp.prod.json        # prod 用
├── .claspignore
├── rollup.config.mjs
├── tsconfig.json
├── tsconfig.test.json      # テストも含めた型検査（§5.1）
└── package.json
```

**根拠**:
- clasp 3.x は TypeScript を変換しない。「Use Typescript + Rollup or another bundler to transpile code before pushing」（[CHANGELOG 3.0.0-alpha](https://github.com/google/clasp/blob/clasp-v3.4.1/CHANGELOG.md), [README: Drop typescript support](https://github.com/google/clasp/blob/clasp-v3.4.1/README.md#drop-typescript-support)）
- `rootDir`（`srcDir` も同義）は `.clasp.json` のあるディレクトリからの相対で解決され、その外を指すと「Security Error: srcDir ... escapes project root」で止まる（[`src/core/clasp.ts`](https://github.com/google/clasp/blob/clasp-v3.4.1/src/core/clasp.ts) の `initClaspInstance`）
- 3.0.0 から `--rootDir` を付けて create / clone しても `.clasp.json` はカレントディレクトリに作られ、`rootDir` には相対パスが入る（CHANGELOG 3.0.0-alpha の BREAKING CHANGES）
- `clasp pull` は `rootDir`（= `dist/`）に書く。Web エディタでの直接編集は TS ソースに戻らないので、公式ガイドも CI 導入後は「avoid manual edits in the Apps Script editor — the repository becomes the single source of truth」と書く（[clasp guide: CI/CD](https://developers.google.com/apps-script/guides/clasp#cicd_for_apps_script_with_clasp_and_github_actions)）

### 3.2 `.clasp.json` の主要項目

| 項目 | 内容 | 根拠 |
|---|---|---|
| `scriptId`（必須） | 対象スクリプトの ID | [README: `.clasp.json`](https://github.com/google/clasp/blob/clasp-v3.4.1/README.md#project-settings-file-claspjson) |
| `rootDir` / `srcDir` | push / pull するローカルディレクトリ。既定はカレント | 同上、`src/core/clasp.ts` |
| `projectId` | 紐づける **標準 GCP プロジェクト** の ID。`run-function` / `tail-logs` / `list-apis` などで要る | 同上 |
| `scriptExtensions` / `htmlExtensions` | 既定は `[".js", ".gs"]` / `[".html"]`。`fileExtension`（単数）は deprecated | 同上 |
| `filePushOrder` | 先に push するファイル。bundle を 1 ファイルにするなら不要 | 同上 |
| `skipSubdirectories` | `.claspignore` が無いときにサブディレクトリを無視する互換オプション | 同上 |

- `.clasp.json` は JSON5 として読まれ、コメントを書ける（3.2.0 の「Add support for comments in project settings file」。本調査で `// コメント` 入りの `.clasp.json` で `show-file-status` が通ることを確かめた）

### 3.3 `.claspignore`: push 対象を許可リストで絞る

**結論**: `**/**` で全部無視してから必要なものだけ戻す。

```text
# .claspignore（.clasp.json と同じディレクトリに置く。パターンは rootDir からの相対）
**/**
!main.js
!appsscript.json
```

**根拠**（[README: `.claspignore`](https://github.com/google/clasp/blob/clasp-v3.4.1/README.md#ignore-file-claspignore), `src/core/clasp.ts` の `findIgnoreFile` / `DEFAULT_CLASP_IGNORE`）:
- `.claspignore` は `.clasp.json` のあるディレクトリから探され、パターンは `rootDir` からの相対で適用される。`.gitignore` と違いディレクトリは `**/node_modules/**` のように書く
- 無いときの既定は `appsscript.json`・`*.gs`・`*.js`・`*.ts`・`*.html` を含め `.git` と `node_modules` を除く。ただし 3.x の既定 `scriptExtensions` は `js` / `gs` なので、`.ts` は push 対象の型に分類されない
- 本調査で `rootDir: "dist"` の構成に `show-file-status --json` をかけ、`dist/main.js` と `dist/appsscript.json` だけが push 対象、`.map` は untracked になることを確かめた。このコマンドは資格情報ファイルが無くても動く（存在しない `-A` を渡して確認）ので、PR の CI でも使える

### 3.4 複数環境（dev / prod）の切り替えと、`.clasp.json` を commit するか

**結論**: 環境ごとに別のスクリプト（別の `scriptId`）を作り、**`-P` / `--project`（または環境変数 `clasp_config_project`）で `.clasp.json` を切り替える**。`.clasp.json` は公式ガイドに従って **commit しない**のを既定にする。

**根拠**:
- `--project <file>` は「Reads project settings from a file other than `.clasp.json`. Intended to support multiple deployment targets」（[README: Global options](https://github.com/google/clasp/blob/clasp-v3.4.1/README.md#global-options)）。ディレクトリを渡すとその中の `.clasp.json` を読む。`rootDir` は渡したファイルのディレクトリ基準（`findProjectRootDir`）
- 公式ガイドは「Add `.clasprc.json` and `.clasp.json` to your `.gitignore`. These contain credentials and should never be committed」と書き、環境ごとに別の Apps Script プロジェクトを作って `CLASP_JSON_DEV` / `CLASP_JSON_PROD` のように分けるよう勧める（[clasp guide: CI/CD](https://developers.google.com/apps-script/guides/clasp#cicd_for_apps_script_with_clasp_and_github_actions)）。Google の aside も `.clasp*.json` を `.gitignore` に入れる（[aside `.gitignore`](https://github.com/google/aside/blob/main/.gitignore)）
- 一方、clasp の保守者（Steve Bazyl）の starter は `scriptId` をプレースホルダにした `.clasp.json` / `.clasp.dev.json` を commit している（[sqrrrl/apps-script-typescript-rollup-starter](https://github.com/sqrrrl/apps-script-typescript-rollup-starter)）
- **本書の判断**: `.clasp.json` に入るのは `scriptId` / `rootDir` / `projectId` で、資格情報そのものではない（スクリプトへのアクセスは Drive の共有権限で決まる）。ただし公式の既定は gitignore なので、それに合わせる。commit しない場合でも構成を再現できるよう、CI では `scriptId` だけを GitHub の変数から受けて `.clasp.json` を組み立てる（§7）
- aside は `.clasp-dev.json` を `.clasp.json` に**コピー**して環境を切り替えるが（[aside `src/config.ts`](https://github.com/google/aside/blob/main/src/config.ts)）、clasp 3.x では `-P` で足りる

```jsonc
// .clasp.json（dev）
{
  // dev 用スクリプト
  "scriptId": "<dev script id>",
  "rootDir": "dist"
}
```

```bash
npx clasp push                          # dev（.clasp.json）
npx clasp push -P .clasp.prod.json      # prod
```

```bash
# .gitignore（GAS 固有分。node 一般の項目は node reference に従う）
dist/
.clasp*.json
.clasprc.json
client_secret*.json
```

### 3.5 `appsscript.json` の主要項目

**結論**: 次の 4 項目は必ず明示する。

```json
{
  "timeZone": "Asia/Tokyo",
  "runtimeVersion": "V8",
  "exceptionLogging": "STACKDRIVER",
  "oauthScopes": [
    "https://www.googleapis.com/auth/spreadsheets.currentonly"
  ]
}
```

（バインド先のスプレッドシートを読み書きし、メニューを出すだけのスクリプトの例。`Ui` のうち `showModalDialog` / `showModelessDialog` / `showSidebar` を使うなら `script.container.ui` を足す。`createMenu` と `alert` にはスコープの記載が無い — [Class Ui](https://developers.google.com/apps-script/reference/base/ui)）

| 項目 | 推奨 | 根拠 |
|---|---|---|
| `runtimeVersion` | `"V8"`（§1.2） | [Manifest structure](https://developers.google.com/apps-script/manifest) |
| `timeZone` | `ZoneId`（例 `"Asia/Tokyo"`）。日付処理とトリガー時刻の基準 | 同上 |
| `exceptionLogging` | `"STACKDRIVER"`。未捕捉例外をスタックトレース付きで Cloud Logging に送り、Error Reporting と連携する。新規プロジェクトでは既定で有効だが、manifest に書いて明示する | 同上, [Logging: Exception logging](https://developers.google.com/apps-script/guides/logging#exception_logging) |
| `oauthScopes` | 明示して最小にする（下記） | [Authorization scopes](https://developers.google.com/apps-script/concepts/scopes) |
| `dependencies.enabledAdvancedServices` | Advanced Services を manifest で有効化する（`serviceId` / `userSymbol` / `version`） | [Advanced services](https://developers.google.com/apps-script/guides/services/advanced), [Manifest: Dependencies](https://developers.google.com/apps-script/manifest/dependencies) |
| `urlFetchWhitelist` | 外部 URL を叩く add-on 等は HTTPS の URL prefix を列挙する。「optional for test deployments but required for deployments」 | [Manifest structure](https://developers.google.com/apps-script/manifest) |
| `executionApi` / `webapp` | API 実行可能 / Web アプリとしてデプロイするときだけ書く | 同上 |

**`oauthScopes` を明示して最小権限にする**（[Authorization scopes](https://developers.google.com/apps-script/concepts/scopes)）:
- 既定ではコード中の関数呼び出しを走査してスコープを自動判定するが、「Apps Script sometimes automatically assigns permissive scopes」。add-on / Web アプリ / Chat アプリでは明示が要る
- bundle すると npm 依存のコードも走査対象に入るので、自動判定は意図より広くなりうる（本書の推論）。明示の方が bundle の中身に左右されない
- 例: スプレッドシートを読むだけなら `spreadsheets.readonly`、バインド先の 1 ファイルだけなら `spreadsheets.currentonly`
- granular consent（ユーザーがスコープを一部だけ許可できる）は全実行面で有効。必要な関数の入口で `ScriptApp.requireScopes(authMode, scopes)` を呼ぶ。インストール型トリガーは実行中に許可を求められないので、トリガー作成時に `requireScopes` しておく
- 自動判定に任せる場合のスコープ絞り込みは JSDoc の `@OnlyCurrentDoc`（[Authorization for Google Services](https://developers.google.com/apps-script/guides/services/authorization)）。ただし **bundler はこのコメントを落とす**（本調査で esbuild + Rollup の出力から消えることを確認）。使うなら Rollup の `output.banner` で bundle の先頭に付ける。`oauthScopes` を明示するなら不要（明示時に annotation が無視されるかは未確認）

**Advanced Services と型定義**:

```jsonc
// appsscript.json に足す（Drive API v3 を使う場合）
"dependencies": {
  "enabledAdvancedServices": [
    { "userSymbol": "Drive", "serviceId": "drive", "version": "v3" }
  ]
}
```

- `oauthScopes` を明示している場合は、Advanced Service で呼ぶ API メソッドが要求するスコープも列挙に足す（必要なスコープは各 Google API のリファレンスで確かめる。明示した列挙だけで認可が決まることは scopes ページの「Replace the contents of the oauthScopes array with the scopes you want the project to use」からの推論）
- 型は `@types/google-apps-script` に同梱されている（`apis/drive_v3.d.ts` など 25 API。`index.d.ts` から参照される）
- **2.0.8（2025-11-24）から Advanced Services のグローバルは `declare var Drive: GoogleAppsScript.Drive | undefined`** になった。manifest で有効化していないと実行時に未定義になるのを型に反映したもの（[DefinitelyTyped#74133](https://github.com/DefinitelyTyped/DefinitelyTyped/pull/74133)。1.0.100 では `| undefined` が無い）。`strict` では `'Drive' is possibly 'undefined'`（TS18048）になるので、ガードを書く

```ts
// tsc 7.0.2 + @types/google-apps-script 2.0.13 で型検査が通ることを確認した書き方
export function countFiles(): number {
  const drive = Drive;
  if (!drive) throw new Error("Drive advanced service is not enabled in appsscript.json");
  return drive.Files.list().files?.length ?? 0;
}
```

- 標準 GCP プロジェクトを使う場合は、Advanced Service に対応する API を Cloud console で別途有効化する（既定プロジェクトなら自動）（[Advanced services: Step 2](https://developers.google.com/apps-script/guides/services/advanced)）

---

## 4. コード品質

**結論**: formatter / linter / TypeScript 本体の選定は node reference に従う。GAS 固有の差分は、**型検査を bundler から切り離すこと**、**GAS に無い API を型で弾くこと**、**GAS に公開する関数を lint の自動修正から守ること**の 3 点。

### 4.1 型検査は `tsc --noEmit`、変換は bundler（esbuild）に分ける

**根拠**:
- TypeScript 7（2026-07-08 の 7.0.2 が `latest`）の package.json は `main` を持たず、`exports` は `.` と `./unstable/*` だけ（npm レジストリで確認）。6 系までの JS API と互換かは確かめていないが、少なくとも 6 系の API を前提にした Rollup plugin は動かない。本調査で `typescript@7.0.2` に対し `@rollup/plugin-typescript@12.3.0` が `TypeError: Cannot read properties of undefined (reading 'ES2015')`、`rollup-plugin-typescript2@0.37.0` が `tsModule.createDocumentRegistry is not a function` で失敗することを確かめた。上流でも未解決（[rollup/plugins#2016 "Support for TypeScript 7"](https://github.com/rollup/plugins/issues/2016)、open）
- esbuild は型を見ずに TS を JS へ変換するので TS の版に依存しない。型検査は `tsc --noEmit`（TS 7 の `tsc`）で別に回す。clasp README が名指しする bundler は Rollup（「use typescript with a bundler like Rollup」）なので、Rollup + rollup-plugin-esbuild にする
- TS 6 以前に固定して `@rollup/plugin-typescript` を使い続ける選択肢もある（Google の aside は `rollup-plugin-typescript2` と `typescript@^6.0.3`）。node reference の package.json 例も TS 7 リリース前の取得で `"typescript": "^5.7.0"` のままなので、node reference に従って 5 系 / 6 系を使うならこの選択肢でも動く。ただし TS の版選定は node reference の領分で、bundler 側の事情で TS の版を縛らない（どの版でも動く esbuild に変換を任せる）方を本書は推奨する

### 4.2 tsconfig: `lib` から DOM を外し、型を GAS だけにする

```jsonc
// tsconfig.json — node reference の tsconfig との差分だけを示す
{
  "compilerOptions": {
    "target": "es2020",              // §1.3。ES2022 以上にしない
    "lib": ["es2020"],               // "dom" を入れない
    "types": ["google-apps-script"], // @types/node を入れない
    "module": "esnext",
    "moduleResolution": "bundler",   // bundler が解決する
    "noEmit": true,                  // 出力は bundler が担う
    "isolatedModules": true,         // esbuild はファイル単位で変換する
    "strict": true,
    "skipLibCheck": true
  },
  "include": ["src"]
}
```

**根拠**:
- `lib` に `dom`、`types` に `node` を入れると、GAS に無い `setTimeout` / `fetch` / `URL` / `TextEncoder` / `atob` が型検査を通ってしまう（[V8 runtime limitations](https://developers.google.com/apps-script/guides/v8-runtime#unavailable-apis)）。本調査で上の設定だと 5 つとも `TS2304: Cannot find name` になり、`console` と `Logger` は `@types/google-apps-script`（`google-apps-script.base.d.ts` の `declare var console: Console`）から解決されることを確かめた
- 反例: aside のテンプレートは `"lib": ["es2020", "dom"]` と `"types": ["jest", "node", "google-apps-script"]` を入れている（[aside `tsconfig.template.json`](https://github.com/google/aside/blob/main/tsconfig.template.json)）。この構成では GAS に無い API の誤用を型で検出できない
- node reference の厳格オプション（`noUncheckedIndexedAccess` 等）はそのまま足してよい。ただし **`noUnusedLocals` は §9 の「es + entry 非 tree-shake」方式と相性が悪い**（GAS に公開するトップレベル関数が「declared but its value is never read」で全部エラーになる。本調査で確認）。§9 の推奨方式（IIFE + export）なら公開関数は export なので該当しない
- テストファイルの型検査は `include` に `test` を足した `tsconfig.test.json` で行う（§5.1）。vitest / vitest-mock-extended はテストから import するので `types` を変える必要は無い（本調査）

### 4.3 lint: GAS に公開する関数を「未使用」として消させない

**根拠**:
- GAS のトリガー・メニュー・`google.script.run` は関数を**名前の文字列**で呼ぶので、静的解析からは未使用に見える
- 本調査で Biome 2.5.15 の `recommended` が export していないトップレベル関数（`onOpen` など）を `lint/correctness/noUnusedVariables` で警告し、**`biome lint --write --unsafe` が `onOpen` を `_onOpen` に改名する**ことを確かめた。改名されると simple trigger の `onOpen` は呼ばれなくなる
- §9 の推奨方式（公開関数を entry から export する）ならこの警告自体が出ない。トップレベル宣言を直接書く方式を採るなら、entry ファイルだけ規則を切る

```jsonc
// biome.json（トップレベル宣言方式を採る場合だけ要る。linter の選定は node reference に従う）
{
  "overrides": [
    {
      "includes": ["src/main.ts"],
      "linter": { "rules": { "correctness": { "noUnusedVariables": "off" } } }
    }
  ]
}
```

- 末尾 `_` の関数名は GAS では「private」で、`google.script.run` から呼べず名前もクライアントに送られない（[HTML Service: Private functions](https://developers.google.com/apps-script/guides/html/communication#private_functions)）。命名規則の lint で末尾 `_` を禁じない

---

## 5. テスト環境

### 5.1 推奨: GAS に触れる層を port / adapter に閉じ込め、Node 上の Vitest で検証する

**結論**: テストフレームワークは node reference の推奨（Vitest）と揃える。GAS 固有の工夫は次の 3 点。

1. ドメインロジックは「2 次元配列 → 2 次元配列」の純粋関数にする（mock 不要）
2. 表の読み書きを port（`readRows` / `writeRows`）に閉じ込め、ユースケースのテストでは port の in-memory fake を自作して渡す
3. GAS の `Sheet` / `Range` に触れる薄い adapter だけを、`@types/google-apps-script` の型から作った mock（vitest-mock-extended の `mock<T>()`）で検証する。グローバル（`SpreadsheetApp`）の差し替えは entry の 1 関数だけに `vi.stubGlobal` を使う

**根拠**:
- GAS サービス全体を in-memory で再現し、保守が続いている npm パッケージは見つからなかった（2026-10-02 時点。gasmask は 2024-07、gas-mock-globals は 2021-11、gas-local は 2018-06 が最終公開）。`@mcpher/gas-fakes` は実 Google API に変換して呼ぶエミュレータで、資格情報なしでは `Utilities` 程度しか動かない（本調査）
- Apps Script の Best practices は「read all data into an array with one command, perform operations on the array data, and write the data out with one command」と書く（[Best practices](https://developers.google.com/apps-script/guides/support/best-practices)）。この形に従うと GAS に触れる箇所が `getValues` / `setValues` の 1 回ずつに集まるので、そこを port の境界にすれば fake は配列を保持するだけで済む
- `@types/google-apps-script` の型は `declare var` のグローバル宣言なので Node 上では実体が無い。型つき mock は [vitest-mock-extended](https://github.com/eratio08/vitest-mock-extended)（5.1.1、peer `vitest >=4.0.0`・`typescript` 7.x まで）の `mock<T>()` で作る。グローバルの差し替えは [`vi.stubGlobal`](https://vitest.dev/api/vi.html#vi-stubglobal)
- 本調査で下の例（テスト 5 件）が Vitest 5.0.3 / Node 26.10.0 で通り、`tsc -p tsconfig.json` と `tsc -p tsconfig.test.json`（TS 7.0.2、§4.2 の設定に `noUncheckedIndexedAccess` を足したもの）も通ることを確かめた

```bash
npm i -D vitest vitest-mock-extended
```

```jsonc
// tsconfig.test.json — テストも型検査する（§4.2 の tsconfig.json は src だけを見る）
// vitest / vitest-mock-extended はテストから import するので、types を足す必要はない
{
  "extends": "./tsconfig.json",
  "include": ["src", "test"]
}
```

```bash
# vitest run は型検査をしない。mock<T>() の型安全を効かせるには、テストを含めた tsc を別に回す（ローカルと §7.4 の check job の両方）
npx tsc -p tsconfig.test.json
```

```ts
// src/domain/overdue.ts — 純粋関数。GAS のグローバルにも Sheet 型にも触れない
export type Cell = string | number | boolean | Date;
export type Row = Cell[];

/** 列: [請求ID, 金額, 期日, 状態]。期日を過ぎた未払いの行を "overdue" にした新しい配列を返す */
export function markOverdue(rows: Row[], today: Date): Row[] {
  return rows.map((row) => {
    const [, , due, status] = row;
    if (status === "unpaid" && due instanceof Date && due.getTime() < today.getTime()) {
      const next = [...row];
      next[3] = "overdue";
      return next;
    }
    return row;
  });
}
```

```ts
// src/ports.ts — 表の読み書きの境界。getValues / setValues の 2 次元配列だけを受け渡す
import type { Row } from "./domain/overdue";

export interface Table {
  readRows(): Row[];            // ヘッダー行を除いたデータ行を一括で読む
  writeRows(rows: Row[]): void; // データ行を一括で書き戻す
}
```

```ts
// src/usecase.ts — port だけに依存する
import { markOverdue } from "./domain/overdue";
import type { Table } from "./ports";

export function updateOverdue(table: Table, today: Date): number {
  const before = table.readRows();
  const after = markOverdue(before, today);
  table.writeRows(after);
  return after.filter((r, i) => r[3] !== before[i]![3]).length;
}
```

```ts
// src/gas/sheet-table.ts — GAS の Sheet に触れるのはこの adapter だけ
import type { Row } from "../domain/overdue";
import type { Table } from "../ports";

export function sheetTable(sheet: GoogleAppsScript.Spreadsheet.Sheet): Table {
  return {
    readRows() {
      const lastRow = sheet.getLastRow();
      if (lastRow < 2) return [];
      return sheet.getRange(2, 1, lastRow - 1, sheet.getLastColumn()).getValues() as Row[];
    },
    writeRows(rows) {
      if (rows.length === 0) return;
      sheet.getRange(2, 1, rows.length, rows[0]!.length).setValues(rows);
    },
  };
}

/** entry から使う。SpreadsheetApp に触れるのはここだけ */
export function openTable(sheetName: string): Table {
  const sheet = SpreadsheetApp.getActiveSpreadsheet().getSheetByName(sheetName);
  if (!sheet) throw new Error(`sheet '${sheetName}' not found`);
  return sheetTable(sheet);
}
```

```ts
// test/fakes/in-memory-table.ts — port の in-memory fake。GAS の Sheet 全体ではなく port の 2 メソッドだけを実装する
import type { Row } from "../../src/domain/overdue";
import type { Table } from "../../src/ports";

export class InMemoryTable implements Table {
  writes = 0;
  constructor(public rows: Row[]) {}
  readRows(): Row[] {
    return this.rows.map((r) => [...r]);
  }
  writeRows(rows: Row[]): void {
    this.writes++;
    this.rows = rows.map((r) => [...r]);
  }
}
```

```ts
// test/usecase.test.ts — ドメインとユースケース。GAS の mock は要らない
import { describe, expect, it } from "vitest";
import { markOverdue } from "../src/domain/overdue";
import { updateOverdue } from "../src/usecase";
import { InMemoryTable } from "./fakes/in-memory-table";

const today = new Date("2026-10-02T00:00:00Z");

describe("markOverdue", () => {
  it("期日を過ぎた unpaid だけを overdue にする", () => {
    const rows = [
      ["A", 100, new Date("2026-09-30T00:00:00Z"), "unpaid"],
      ["B", 200, new Date("2026-10-05T00:00:00Z"), "unpaid"],
      ["C", 300, new Date("2026-09-01T00:00:00Z"), "paid"],
    ];
    expect(markOverdue(rows, today).map((r) => r[3])).toEqual(["overdue", "unpaid", "paid"]);
  });
});

describe("updateOverdue", () => {
  it("1 回読んで 1 回書く", () => {
    const table = new InMemoryTable([["A", 100, new Date("2026-09-30T00:00:00Z"), "unpaid"]]);
    expect(updateOverdue(table, today)).toBe(1);
    expect(table.writes).toBe(1); // Best practices の batch 書き込みをテストで固定する
    expect(table.rows[0]![3]).toBe("overdue");
  });
});
```

```ts
// test/sheet-table.test.ts — adapter と entry。@types/google-apps-script の型から mock を作る
import { afterEach, describe, expect, it, vi } from "vitest";
import { mock } from "vitest-mock-extended";
import { openTable, sheetTable } from "../src/gas/sheet-table";

afterEach(() => vi.unstubAllGlobals());

describe("sheetTable", () => {
  it("2 行目以降を 1 回の getRange().getValues() で読む", () => {
    const range = mock<GoogleAppsScript.Spreadsheet.Range>();
    range.getValues.mockReturnValue([["A", 100]]);
    const sheet = mock<GoogleAppsScript.Spreadsheet.Sheet>();
    sheet.getLastRow.mockReturnValue(2);
    sheet.getLastColumn.mockReturnValue(2);
    sheet.getRange.mockReturnValue(range); // getRange はオーバーロードがあるので calledWith を使わない（注意点）

    expect(sheetTable(sheet).readRows()).toEqual([["A", 100]]);
    expect(sheet.getRange).toHaveBeenCalledExactlyOnceWith(2, 1, 1, 2);
  });

  it("書き込みは 1 回の setValues", () => {
    const range = mock<GoogleAppsScript.Spreadsheet.Range>();
    const sheet = mock<GoogleAppsScript.Spreadsheet.Sheet>();
    sheet.getRange.mockReturnValue(range);

    sheetTable(sheet).writeRows([["A", 1], ["B", 2]]);
    expect(sheet.getRange).toHaveBeenCalledWith(2, 1, 2, 2);
    expect(range.setValues).toHaveBeenCalledWith([["A", 1], ["B", 2]]);
  });
});

describe("openTable", () => {
  it("calledWith でシート名ごとに戻り値を変える", () => {
    const ss = mock<GoogleAppsScript.Spreadsheet.Spreadsheet>();
    ss.getSheetByName.calledWith("invoices").mockReturnValue(mock<GoogleAppsScript.Spreadsheet.Sheet>());
    const app = mock<GoogleAppsScript.Spreadsheet.SpreadsheetApp>();
    app.getActiveSpreadsheet.mockReturnValue(ss);
    vi.stubGlobal("SpreadsheetApp", app);

    expect(openTable("invoices")).toBeDefined();
    expect(() => openTable("other")).toThrow("not found");
  });
});
```

**mock / fake ライブラリの比較**（2026-10-02 時点。最新版と公開日は npm レジストリ、月間 DL は 2026-09 の npm downloads API）:

| 候補 | 種別 | 最新版（公開日） | 判定 | 理由 |
|---|---|---|---|---|
| [vitest-mock-extended](https://github.com/eratio08/vitest-mock-extended) | 型から mock を生成 | 5.1.1（2026-08-02） | **推奨**（adapter 層） | `@types/google-apps-script` の interface からそのまま作れる。peer `vitest >=4.0.0`・`typescript` 7.x まで |
| 自作の port + in-memory fake | 設計パターン | — | **推奨**（ユースケース層） | 依存なし。読み書きの整合と batch 回数を確かめられる |
| `vi.stubGlobal` + 手書きオブジェクト | Vitest 標準 | — | 条件付き（entry の 1 関数だけ） | 手書きのオブジェクトは GAS の型と照合されない。中身は `mock<T>()` で作る |
| jest-mock-extended | 型から mock を生成 | 4.0.1（2026-04-20） | 非推奨 | Jest 用。`typescript` の peer は 6.x まで。Vitest では fork（上）を使う |
| [@ciderjs/vitest-plugin-gas-mock](https://github.com/luthpg/vitest-plugin-gas-mock) | 型から生成した Proxy スタブを global 注入 | 0.2.0（2026-02-19） | 非推奨 | peer `vitest ^4` で Vitest 5 と ERESOLVE。状態を持たない（`setProperty` 後の `getProperty` が `""`）。`mockChain` の上書きを消す API が無い。2,470 個の GAS 名を `globalThis` に生やす |
| [@dougborg/gas-test-utils](https://www.npmjs.com/package/@dougborg/gas-test-utils) | 固定値スタブを global 注入 | 0.2.0（2026-10-01、初版 2026-09-24） | 非推奨（時期尚早） | 公開から 8 日。固定値の `vi.fn()` で、型つき `mock<T>()` より得るものが少ない |
| [gasmask](https://github.com/vlucas/gasmask) | 手書きの in-memory fake | 1.3.12（2024-07-18） | 非推奨 | 更新停止。SpreadsheetApp 中心の独自クラスで `GoogleAppsScript.*` の型と合わない |
| [gas-mock-globals](https://github.com/dan-kushnaryov/google-apps-script-services) | polyfill を global 注入 | 2.2.0（2021-11-02） | 非推奨 | 更新停止。型定義なし |
| [gas-local](https://github.com/mzagorny/gas-local) | `.gs` を vm で読むローダ | 1.3.1（2018-06-12） | 非推奨 | 更新停止。TS + bundler 構成では不要 |
| [@mcpher/gas-fakes](https://github.com/brucemcpherson/gas-fakes) | 実 Google API に変換するエミュレータ | 2.5.8（2026-07-30） | 非推奨（mock 用途） | offline / mock モードが無い。資格情報なしでは `Utilities` しか動かず、Properties / Cache / `SpreadsheetApp.openById` は「Unable to detect a Project Id」で失敗した（本調査）。補完用途は §5.2 |

**注意点**:
- **オーバーロードのあるメソッドは `calledWith` を使わない**。`calledWith` の引数型は最後のオーバーロードだけになる（`Sheet.getRange` なら `(a1Notation: string)`）。本調査で `sheet.getRange.calledWith(2, 1, 1, 2)` が TS2345 になった。`mockReturnValue` と `toHaveBeenCalledWith` に分ける
- **`mockDeep()` でチェーンを作れると期待しない**。関数の戻り値は `{ funcPropSupport: true }` を付けても `undefined` だった（本調査）。チェーンは階層ごとに `mock<T>()` を作ってつなぐ
- **enum は mock されない**。`mock<SpreadsheetApp>().BorderStyle.SOLID` は `undefined` になる。enum 値で分岐するコードは mock 上で素通りしうる
- **mock は状態を持たない**。書いた値を読み返すテストは port の fake で書く。fake が GAS と食い違いうる点（日付セルが `Date` で返る等）は、§5.2 の dev スクリプトで 1 度確かめる
- Node には GAS に無い API（`setTimeout`、`fetch` など）がある。Node 上で通っても GAS で動く保証にはならない。自分のコードは §4.2 の tsconfig で弾けるが、bundle した npm 依存の内部は弾けない（§11）
- GAS 固有の挙動（6 分の実行時間上限、quotas、同時実行、権限）はローカルでは再現できない（§5.3）
- GAS サービスを型から自動でスタブにする Vitest plugin（@ciderjs/vitest-plugin-gas-mock）や、in-memory fake（gasmask）・polyfill（gas-mock-globals）は採らない。Vitest 5 の peer 範囲外、状態を持たない、更新停止、型が GAS と合わない、のいずれかに当たる

### 5.2 GAS 上の結合テストの選択肢

| 方法 | 前提 | 向き不向き |
|---|---|---|
| `clasp run-function <name>`（Apps Script API `scripts.run`） | 下記 5 条件。**service account 不可** | 関数単位のスモークテスト。CI に載せるには前提が重い |
| Head deployment（Deploy > Test deployments） | read 権限以上 | 手動確認。「Use head deployments to test code. Don't use head deployments for public use」 |
| dev 用スクリプトへ push して手動実行・トリガー確認 | §3.4 の dev / prod 分離 | 最終確認 |
| `@mcpher/gas-fakes` で Node 上から実 API を呼んで実行 | ADC / DWD 等の Google 資格情報、`appsscript.json` のスコープ。Node 22.20 以上 | 手元でデバッガを使った動作確認。**実データに触れる**ので dev アカウント・dev ファイルで使う。資格情報なしでは `Utilities` 程度しか動かないので、CI の単体テストの代わりにはならない |

**`clasp run-function` の前提**（[Execute functions with the Apps Script API](https://developers.google.com/apps-script/api/how-tos/execute), [clasp docs/run.md](https://github.com/google/clasp/blob/clasp-v3.4.1/docs/run.md)）:
1. スクリプトを**標準 GCP プロジェクト**に紐づけ（既定プロジェクトでは不可）、そのプロジェクトで **Apps Script API を有効化**する。`.clasp.json` に `projectId` を書く
2. 同じ GCP プロジェクトで **Desktop app 型の OAuth クライアント**を作り、`clasp login --user <name> --use-project-scopes --creds client_secret.json` でログインする。「This OAuth token must cover all the scopes used by the script, not just the ones used by the called function」
3. `appsscript.json` に `"executionApi": { "access": "ANYONE" }` を書いて push し、**API 実行可能としてデプロイ**する（clasp README は「Easiest done via GUI at the moment」）
4. スクリプトが**少なくとも 1 つのスコープを要求する**こと（「The API can only execute scripts that have at least one required scope」）
5. clasp は既定で `devMode: true`（最後に保存されたコード）で実行し、これは**スクリプトのオーナーしか使えない**（`src/core/functions.ts` の `runFunction(..., devMode = true)`、[Execute guide](https://developers.google.com/apps-script/api/how-tos/execute)「Only the owner of the script can execute it in development mode」）。`--nondev` で最新のデプロイを実行する
- 受け渡せる値は文字列・配列・オブジェクト・数値・真偽値だけで、`Sheet` などの GAS オブジェクトは返せない。トリガーは作れない（同 Limitations）
- 「Warning: The Apps Script API doesn't work with service accounts」（同ページ）

### 5.3 ローカルでは再現できない Apps Script 側の制限（設計に効くもの）

**結論**: 次の制限はローカルのテストでは表に出ない。処理の分割・トリガー数・設定値の大きさを、開発の初期に表と突き合わせる。

| 制限 | consumer（gmail.com） | Google Workspace | 設計への影響（本書の推論） |
|---|---|---|---|
| スクリプトの実行時間 | 6 分 / 実行 | 6 分 / 実行 | 長い処理は分割し、続きを時間主導トリガーや状態の保存で再開する |
| カスタム関数 / Workspace add-on の実行時間 | 30 秒 / 実行 | 30 秒 / 実行 | シート関数・add-on の UI 処理は重い処理を持たない |
| トリガーの合計実行時間 | 90 分 / 日 | 6 時間 / 日 | 高頻度トリガーは consumer アカウントですぐ尽きる。超えると「Service using too much computer time for one day」 |
| トリガー数 | 20 / ユーザー / スクリプト | 同左 | トリガーを動的に作る設計は上限を超えないよう掃除する |
| 同時実行 | 30 / ユーザー、1,000 / スクリプト | 同左 | カスタム関数の多用で「too many scripts running simultaneously」 |
| URL Fetch | 20,000 回 / 日、応答 50 MB / 回 | 100,000 回 / 日 | 外部 API のポーリングは回数を見積もる。並列化は `UrlFetchApp.fetchAll` |
| Properties | 値 9 KB、ストア 500 KB、読み書き 50,000 回 / 日 | 読み書き 500,000 回 / 日 | 大きな状態は Properties に置かない（§10.2） |
| バージョン | 200 / スクリプト | 同左 | リリース時だけ作る（§7.3） |
| Apps Script プロジェクト作成 | 50 / 日 | 50 / 日 | CI でスクリプトを作り捨てる設計は取らない |

**根拠**（[Quotas for Google Services](https://developers.google.com/apps-script/guides/services/quotas)）:
- 「Quotas are per user and reset 24 hours after the first request」「All quotas are subject to elimination, reduction, or change at any time, without notice」。数値は設計の目安で、保証値ではない
- 超えると例外を投げて実行が止まる（「Limit exceeded: ...」「Service invoked too many times: ...」等）
- 非同期の待ちは使えない: `setTimeout` / `setInterval` が無く、I/O はすべてブロッキング。待つなら `Utilities.sleep`（[V8 runtime: Asynchronous limitations](https://developers.google.com/apps-script/guides/v8-runtime#async-await)）
- 性能面では、他サービス呼び出しを減らす・読み書きをまとめる（`getValues` / `setValues`）・ライブラリを UI の重いスクリプトで使わない・`CacheService` を使う、が公式の推奨（[Best practices](https://developers.google.com/apps-script/guides/support/best-practices)）

---

## 6. pre-commit フック

**結論**: フックの仕組み（Husky + lint-staged 等）と lint / format の実行は node reference に従う。GAS 固有に足すのは **clasp の資格情報ファイルを commit させない検査**だけ。`clasp push` をフックで走らせない。

**根拠**:
- `~/.clasprc.json`（または `-A` で指定したファイル）には refresh token が平文で入る（§10）。clasp 2.x の `login --creds` はプロジェクト直下に `.clasprc.json` を書いた（README の Login 節は今もそう書くが、3.x のコードは `initAuth` が既定で `~/.clasprc.json` を使い、`--creds` も同じ credential store に書く — [`src/auth/auth.ts`](https://github.com/google/clasp/blob/clasp-v3.4.1/src/auth/auth.ts), [`src/commands/login.ts`](https://github.com/google/clasp/blob/clasp-v3.4.1/src/commands/login.ts)）。2.x 時代の手順やファイルが残ったリポジトリでは誤 commit が起きうる
- `clasp login --creds` に渡す OAuth クライアントの `client_secret.json` も「Please keep this file secret!」（[docs/run.md](https://github.com/google/clasp/blob/clasp-v3.4.1/docs/run.md)）
- `clasp push` は「always replaces the whole content of the online project」（[README: Push](https://github.com/google/clasp/blob/clasp-v3.4.1/README.md#push)）。commit のたびにリモートを書き換えるのはフックの責務ではない

```bash
# .husky/pre-commit（node reference の lint-staged 呼び出しに 1 段足す）
if git diff --cached --name-only | grep -E '(^|/)(\.clasprc\.json|client_secret[^/]*\.json)$'; then
  echo "clasp の資格情報ファイルは commit しない" >&2
  exit 1
fi
npx lint-staged
```

---

## 7. CI/CD（GitHub Actions）

### 7.1 認証: CI で使えるのは「ユーザーの refresh token」だけ

**結論**: CI では、デプロイ用の Google アカウントで `clasp login` して得た **refresh token を GitHub Secrets に置き、実行時に `~/.clasprc.json` を組み立てる**。service account / ADC / Workload Identity Federation では clasp を動かせない。

**根拠**:
- **service account**: 「The Apps Script API doesn't work with service accounts」（[Execute functions](https://developers.google.com/apps-script/api/how-tos/execute)）。clasp README も見出しを「Service accounts (EXPERIMENTAL/NOT WORKING)」とする（[README](https://github.com/google/clasp/blob/clasp-v3.4.1/README.md#service-accounts-experimentalnot-working)）
- **`--adc`（ADC）**: clasp は ADC から得たクライアントを `defaultCreds instanceof OAuth2Client` のときだけ使い、それ以外は `undefined`（= 未認証）にする（[`src/auth/auth.ts`](https://github.com/google/clasp/blob/clasp-v3.4.1/src/auth/auth.ts) の `createApplicationDefaultCredentials`）
- **Workload Identity Federation**: clasp 3.4.1 が依存する google-auth-library 10.5.0 では、WIF の `BaseExternalAccountClient` は `AuthClient` を継承し `OAuth2Client` を継承しない（`build/src/auth/baseexternalclient.d.ts`）。したがって上の判定で捨てられ、WIF では認証されない。WIF で得られるのが service account の権限である点でも、上の「service account 不可」に当たる
- 公式ガイドの CI 例も `~/.clasprc.json` を secret から書き出す方式（[clasp guide: Authentication in CI](https://developers.google.com/apps-script/guides/clasp#cicd_for_apps_script_with_clasp_and_github_actions)）

**`~/.clasprc.json` の中身**（clasp 3.x の V3 形式。[`src/auth/file_credential_store.ts`](https://github.com/google/clasp/blob/clasp-v3.4.1/src/auth/file_credential_store.ts), `auth.ts` の `saveOauthClientCredentials`）:

```json
{
  "tokens": {
    "default": {
      "client_id": "...",
      "client_secret": "...",
      "type": "authorized_user",
      "refresh_token": "...",
      "access_token": "..."
    }
  }
}
```

- キー `default` は `--user` 名。3.4.0 から 0600 で書き、symlink を拒否する（CHANGELOG 3.4.0）
- clasp 既定の OAuth クライアントでログインした場合、`client_id` / `client_secret` は clasp のソースに載った公開値（[`src/auth/oauth_client.ts`](https://github.com/google/clasp/blob/clasp-v3.4.1/src/auth/oauth_client.ts)）で、**秘密なのは `refresh_token` だけ**
- **このトークンは広い**: `clasp login` の既定スコープは `script.projects` / `script.deployments` / `script.webapp.deploy` / `drive.file` / `drive.metadata.readonly` / `service.management` / `logging.read` / `cloud-platform` ほか（[`src/commands/login.ts`](https://github.com/google/clasp/blob/clasp-v3.4.1/src/commands/login.ts)）。`cloud-platform` を含むので、漏れた場合の影響はそのアカウントが触れる GCP 全体に及ぶ。デプロイ専用のアカウントを作り、対象スクリプトだけを編集者で共有する（本書の推奨）

**refresh token が失効する条件**（[Google Identity: Refresh token expiration](https://developers.google.com/identity/protocols/oauth2#expiration)）:
- 6 か月使われない / ユーザーが取り消した / Gmail スコープ付きでパスワードを変えた
- **自前の OAuth クライアントを「外部」ユーザータイプ・公開ステータス「テスト」の GCP プロジェクトで作ると、refresh token は 7 日で失効する**。CI 用は「本番」ステータスか、Workspace なら「内部」ユーザータイプにする
- OAuth クライアント × Google アカウントあたり 100 本の上限があり、超えると古いものから無効化される。手元で何度も `clasp login` すると CI のトークンが押し出されうる。CI 用は専用アカウントで 1 回だけ発行する
- Cloud Platform スコープを含むため、GCP 組織のセッション制御ポリシーで再認証を求められることがある（同ページ）

**自前の OAuth クライアント**: README は「using your own project is recommend and can improve security and compliance」とする（[README: Bring your own project/credentials](https://github.com/google/clasp/blob/clasp-v3.4.1/README.md#bring-your-own-projectcredentials)）。標準 GCP プロジェクトで Desktop Application 型のクライアントを作り、Apps Script API・Service Usage API・Drive API・Cloud Logging API を有効化する。組織が第三者アプリを制限している場合は、clasp の client ID を許可リストに入れるか内部用プロジェクトを作る（同 Allow-list clasp）

**Apps Script API のユーザー設定**: CI で使うアカウントでも https://script.google.com/home/usersettings で Google Apps Script API を有効にしておく（[README: Install](https://github.com/google/clasp/blob/clasp-v3.4.1/README.md#install)、公式ガイドの Troubleshooting「"Script API not enabled"」）

### 7.2 `clasp push` は CI では必ず `--force` を付ける

**結論**: CI の `clasp push` には `--force`（`-f`）を付ける。付けないと、`appsscript.json` が変わった push が**何もせず成功終了する**。

**根拠**（[`src/commands/push.ts`](https://github.com/google/clasp/blob/clasp-v3.4.1/src/commands/push.ts), [`src/commands/utils.ts`](https://github.com/google/clasp/blob/clasp-v3.4.1/src/commands/utils.ts)）:
- push は変更ファイルに `appsscript.json` が含まれ `--force` が無いと `confirmManifestUpdate()` を呼ぶ
- `confirmManifestUpdate()` は非対話時（`isInteractive = process.stdout.isTTY` が偽）に確認せず `false` を返し、push は「Skipping push.」と出して return する。終了コードは 0 のまま
- つまり manifest（スコープ・Advanced Services・`runtimeVersion`）を変えた PR だけが、CI 上で静かに反映されない。aside の雛形の `deploy:prod` は `clasp push`（`-f` なし）で、この落とし穴に当たる（[aside `src/config.ts`](https://github.com/google/aside/blob/main/src/config.ts)）
- 公式ガイドの CI 例は `npx @google/clasp push --force` で正しい

### 7.3 push / version / deploy の違いと、deployment ID を固定した更新

| コマンド（3.x） | 別名 | 何をするか |
|---|---|---|
| `clasp push` | — | ローカルの全ファイルでリモートの**現在のコード（HEAD）**を置き換える。Head deployment と `devMode` 実行に即反映 |
| `clasp create-version [description]` | `version` | 現在のコードから**不変のバージョン**を作り、番号を返す |
| `clasp create-deployment [-V n] [-d desc]` | `deploy` | **新しい deployment（新しい ID / URL）**を作る。`-V` が無いと先にバージョンを 1 つ作る |
| `clasp update-deployment <deploymentId> [-V n] [-d desc]` | `redeploy` | **既存の deployment の指すバージョンを差し替える**（ID / URL は変わらない）。`-V` が無いと先にバージョンを 1 つ作る |
| `clasp list-deployments` / `list-versions` | `deployments` / `versions` | 一覧 |

（[README](https://github.com/google/clasp/blob/clasp-v3.4.1/README.md#deploy), `src/commands/create-deployment.ts` / `update-deployment.ts`, `src/core/project.ts` の `deploy()`。`-V` 省略時に `this.version(description)` を呼ぶことをコードで確認）

**結論**: 本番の Web アプリ / API 実行可能 / add-on は、**最初に 1 回だけ deployment を作り、以後は `update-deployment <固定 ID>` で差し替える**。`create-deployment` を毎回呼ぶと URL が変わる。

**根拠**（[Deployments](https://developers.google.com/apps-script/concepts/deployments)）:
- 「When you want to update the code used by an existing deployment (like a web app), you create a new version and then edit the deployment to point to that new version. This updates the application for all users while maintaining the same URL or deployment ID」
- versioned deployment は削除できず archive だけ。所有者の移転もできない（スクリプトの所有者を移しても deployment の所有者は変わらない）
- add-on / Editor add-on / Chat アプリ / API 実行可能の versioned deployment には標準 GCP プロジェクトが要る

**バージョンは 200 個まで**: 「Script projects can have up to 200 versions」（[Versions](https://developers.google.com/apps-script/guides/versions), [Quotas: Versions 200 / script](https://developers.google.com/apps-script/guides/services/quotas)）。Apps Script API の `projects.versions` には `create` / `get` / `list` しか無く削除メソッドが無い（[REST reference](https://developers.google.com/apps-script/api/reference/rest/v1/projects.versions)）。clasp 3.4.1 にも version の削除コマンドは無い。削除は Apps Script エディタからの手作業で、active な deployment が使っていないものだけ消せる。したがって **バージョンを作るのはリリース時だけ**にし、main への push ごとには作らない

### 7.4 推奨ワークフロー

**結論**: main への push は dev スクリプトへの `push --force` だけ。tag push で prod スクリプトへ push し、固定した deployment ID を `update-deployment` する。prod の資格情報は GitHub の environment secrets に置き、承認を挟む。

```yaml
# .github/workflows/gas.yml
name: gas
on:
  pull_request:
  push:
    branches: [main]
    tags: ["v*"]
permissions:
  contents: read
concurrency:
  group: ${{ github.workflow }}-${{ github.event.pull_request.number || github.ref }}
  cancel-in-progress: ${{ github.event_name == 'pull_request' }}

jobs:
  check:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v7
      - uses: actions/setup-node@v7
        with:
          node-version-file: .nvmrc   # Node の固定方法は node reference に従う
          cache: npm
      - run: npm ci
      - run: npx tsc --noEmit          # 型検査（bundler とは別。§4.1）
      - run: npx tsc -p tsconfig.test.json   # テストの型検査（vitest run は型を見ない。§5.1）
      - run: npm run lint              # node reference の linter
      - run: npx vitest run
      - run: npm run build             # rollup -c && cp appsscript.json dist/
      - name: List files clasp would push (no credentials needed)
        run: |
          echo '{"scriptId":"placeholder","rootDir":"dist"}' > .clasp.json
          npx clasp show-file-status --json   # dist/main.js と dist/appsscript.json だけか目で見る

  deploy:
    needs: check
    if: github.event_name == 'push'
    runs-on: ubuntu-latest
    environment: ${{ startsWith(github.ref, 'refs/tags/') && 'production' || 'development' }}
    steps:
      - uses: actions/checkout@v7
      - uses: actions/setup-node@v7
        with:
          node-version-file: .nvmrc
          cache: npm
      - run: npm ci
      - run: npm run build
      - name: Write clasp settings and credentials
        env:
          CLASP_REFRESH_TOKEN: ${{ secrets.CLASP_REFRESH_TOKEN }}
          CLASP_CLIENT_ID: ${{ vars.CLASP_CLIENT_ID }}
          CLASP_CLIENT_SECRET: ${{ secrets.CLASP_CLIENT_SECRET }}
          SCRIPT_ID: ${{ vars.SCRIPT_ID }}
        run: |
          node -e '
            const fs = require("fs"), os = require("os"), e = process.env;
            fs.writeFileSync(os.homedir() + "/.clasprc.json", JSON.stringify({ tokens: { default: {
              type: "authorized_user", client_id: e.CLASP_CLIENT_ID,
              client_secret: e.CLASP_CLIENT_SECRET, refresh_token: e.CLASP_REFRESH_TOKEN } } }), { mode: 0o600 });
            fs.writeFileSync(".clasp.json", JSON.stringify({ scriptId: e.SCRIPT_ID, rootDir: "dist" }));
          '
      - run: npx clasp push --force    # --force が無いと manifest 変更時に黙って skip される（§7.2）
      - name: Release (versioned deployment, fixed ID)
        if: startsWith(github.ref, 'refs/tags/')
        env:
          DEPLOYMENT_ID: ${{ vars.DEPLOYMENT_ID }}
        run: npx clasp update-deployment "$DEPLOYMENT_ID" --description "$GITHUB_REF_NAME"
```

**根拠と、公式ガイドの CI 例から意図的に変えた点**（公式例: [clasp guide: CI/CD for Apps Script with clasp and GitHub Actions](https://developers.google.com/apps-script/guides/clasp#cicd_for_apps_script_with_clasp_and_github_actions)）:

| 公式例 | 本書 | 理由 |
|---|---|---|
| `actions/checkout@v6.3` / `actions/setup-node@v6.3`（CI 側） | `@v7` | `v6.3` という ref は両 repo に存在しない（`gh api repos/actions/checkout/git/ref/tags/v6.3` は 404。checkout の v6 系 tag は v6.0.0〜v6.1.0、setup-node は v6.0.0〜v6.5.0）。最新は checkout v7.0.1 / setup-node v7.0.0 |
| `node-version: "20"` | `.nvmrc`（22.12 以上） | Node 20 は 2026-04-30 に EOL。Vitest 5 の下限にも届かない（§1.1） |
| `echo '${{ secrets.CLASPRC_JSON }}' > ~/.clasprc.json` | 中間の環境変数で受けて Node で書く | GitHub は「Never use structured data as a secret」（JSON をまるごと secret にするとログのマスクが効きにくい）と、インラインスクリプトでは式を直接展開せず「Use an intermediate environment variable」を勧める（[Secure use reference](https://docs.github.com/en/actions/reference/security/secure-use)）。値を 1 つずつ secret にし、秘密でない `client_id` と `SCRIPT_ID` は variables に置く |
| `CLASP_JSON` を secret にして `.clasp.json` に書く | `SCRIPT_ID` だけを variables に置いて組み立てる | 同上（構造化データを secret にしない） |
| main への push ごとに `clasp version "$(git rev-parse --short HEAD)"` | バージョンは tag のときだけ | 1 スクリプト 200 バージョンの上限があり、API からは消せない（§7.3） |
| `npx @google/clasp push --force` | 同じ | §7.2 |

- **environment secrets**: 「Secrets stored in an environment are only available to workflow jobs that reference the environment. If the environment requires approval, a job cannot access environment secrets until one of the required reviewers approves it」（[Deployments and environments](https://docs.github.com/en/actions/reference/workflows-and-actions/deployments-and-environments)）。dev / prod で別の `SCRIPT_ID` / `DEPLOYMENT_ID` / refresh token を environment 単位で持たせる
- `.clasprc.json` を V3 形式で組み立てて clasp が読めることはコード（`FileCredentialStore.load` → `GoogleAuth().fromJSON`）からの推論で、`access_token` を省いた形を実際の CI では試していない（§未確認）
- refresh 後のトークンを clasp はファイルへ書き戻すが、CI の runner は使い捨てなので影響しない

---

## 8. セキュリティ

**結論**: npm 依存の脆弱性管理（audit・Dependabot 等）は node reference に従う。GAS 固有に足すのは次の 5 点。

1. **clasp は 3.4.1 以上**: 3.2.0 で「(SECURITY) prevent path traversal in remote file synchronization」、3.4.0 で OAuth の state 欠落による CSRF、PKCE 対応、`.clasp.json` の `srcDir` によるパストラバーサル、MCP の `projectDir` 検証、資格情報ファイルの symlink 保護と 0600 化が入った（[CHANGELOG](https://github.com/google/clasp/blob/clasp-v3.4.1/CHANGELOG.md)）。悪意ある `.clasp.json` や remote のファイル名で手元のファイルを上書きされうる経路が塞がれている
2. **bundle した依存は GAS 上でユーザーの権限で動く**: runtime 依存はスクリプトに inline され、manifest の `oauthScopes` の範囲で Gmail・Drive 等に触れられる。runtime 依存は最小にし、`oauthScopes` を明示して最小にする（§3.5）
3. **clasp の資格情報の扱い**: refresh token は `cloud-platform` を含む広いスコープを持つ（§7.1）。専用アカウント、自前の OAuth クライアント、environment secrets で影響範囲を絞る
4. **外部 URL を叩くなら `urlFetchWhitelist`**: 「If present, any URL endpoint fetched must match a prefix in this list. This helps protect user data」（[Manifest structure](https://developers.google.com/apps-script/manifest)）
5. **公開範囲**: sensitive / restricted スコープを使って一般公開する Web アプリ・add-on は OAuth クライアントの検証が要る（[Authorization scopes: OAuth verification](https://developers.google.com/apps-script/concepts/scopes#oauth-verification)）。検証にも標準 GCP プロジェクトが要る（[Google Cloud projects](https://developers.google.com/apps-script/guides/cloud-platform-projects)）

- `google.script.run` から呼べるのは「トップレベルに宣言された、名前が `_` で終わらない関数」すべて（[HTML Service: Private functions](https://developers.google.com/apps-script/guides/html/communication#private_functions)）。Web アプリでは攻撃面になるので、§9 の推奨方式で内部の関数をトップレベルに出さない

---

## 9. ビルド・パッケージング・配布

### 9.1 前提: GAS は ES modules を読まない。トリガー等はトップレベル関数しか呼ばない

**根拠**:
- 「The V8 runtime doesn't support ES6 modules (import / export). To use libraries, you must either use the Apps Script library mechanism or bundle your code and its dependencies into a single script file」「All script files in your project are executed in a global scope」（[V8 runtime: Module limitations](https://developers.google.com/apps-script/guides/v8-runtime#module_limitations)）
- 関数として検出されるのはトップレベルの `function` 宣言と、`var` / `let` / `const` に関数式・アロー関数を代入したもの（[V8 runtime: Improved function detection](https://developers.google.com/apps-script/guides/v8-runtime#improved-function)）
- `google.script.run` は「functions within libraries or functions not declared at the top level of the script」を呼べない（[HTML Service: Private functions](https://developers.google.com/apps-script/guides/html/communication#private_functions)）
- 一方、インストール型トリガー・add-on の manifest トリガー・メニュー項目は `"menu.item1"` のようにオブジェクトのメソッドも指定できる（[V8 runtime: Call object methods from triggers and callbacks](https://developers.google.com/apps-script/guides/v8-runtime#call-object)）。simple trigger（`onOpen` 等）と `google.script.run` はトップレベル関数が要る

### 9.2 推奨: Rollup の IIFE 出力 + entry の export から生成したトップレベルのスタブ

**結論**: entry（`src/main.ts`）では GAS に公開する関数を **`export` する**。Rollup は `format: "iife"` で全体を 1 つの関数スコープに閉じ込め、`output.footer` で **entry の export 名からトップレベルのスタブ関数を生成**する。

```js
// rollup.config.mjs
import esbuild from "rollup-plugin-esbuild";

export default {
  input: "src/main.ts",
  output: {
    file: "dist/main.js",
    format: "iife",
    name: "__app",                 // IIFE の戻り値（entry の export）を受ける唯一のグローバル
    // banner: "/** @OnlyCurrentDoc */",   // oauthScopes を明示しないときだけ（§3.5）
    footer: (chunk) =>
      chunk.exports
        .map((n) => `function ${n}(...args) { return __app.${n}(...args); }`)
        .join("\n"),
  },
  plugins: [esbuild({ target: "es2020" })],   // §1.3
};
```

```ts
// src/main.ts — GAS から呼ばせる関数だけを export する
import { buildMenu, greet } from "./menu";

export function onOpen(): void {
  buildMenu(SpreadsheetApp.getUi());
}

export function doGet(): GoogleAppsScript.HTML.HtmlOutput {
  return HtmlService.createHtmlOutput(`<p>${greet("web")}</p>`);
}
```

本調査で生成された `dist/main.js`（Rollup 4.63.6 + rollup-plugin-esbuild 6.2.1 + esbuild 0.28.2）:

```js
var __app = (function (exports) {
  'use strict';
  function greet(name) { return `Hello, ${name}`; }
  function buildMenu(ui) { ui.createMenu("Tools").addItem("Say hello", "menuSayHello").addToUi(); }
  function onOpen() { buildMenu(SpreadsheetApp.getUi()); }
  function doGet() { return HtmlService.createHtmlOutput(`<p>${greet("web")}</p>`); }
  exports.doGet = doGet;
  exports.onOpen = onOpen;
  return exports;
})({});
function doGet(...args) { return __app.doGet(...args); }
function onOpen(...args) { return __app.onOpen(...args); }
```

**この方式を選ぶ理由**:
- **公開面が export で決まる**: 内部の関数（`greet`、`buildMenu`）は IIFE の中にあってトップレベルに出ないので、`google.script.run` から呼べず、エディタの実行メニューにも出ない（§9.1 の根拠から導いた推論）
- **公開する名前を 2 箇所に書かない**: `footer` に関数を渡すと Rollup は描画済み chunk を渡し、その `exports` が entry の export 名の一覧になる。本調査で export を足すとスタブが自動で増えることを確かめた
- **tree-shaking を切らなくてよい**: entry の export は保持されるので、未使用の内部関数は通常どおり消える
- **lint / tsc の「未使用」問題が出ない**（§4.2, §4.3）、**entry の関数をそのままテストから import できる**（§5）
- 実際の GAS 上で simple trigger / `google.script.run` がスタブ経由で動くことは本調査では確かめていない（push する環境が無かった）。スタブは公式が検出対象として挙げるトップレベルの `function` 宣言そのものなので動く想定だが、採用時に dev スクリプトで 1 度確認する（§未確認）

### 9.3 代替: ES 出力 + entry だけ tree-shaking を外す（clasp 保守者 / aside の方式）

entry に `export` を書かずトップレベル関数だけを置き、`format: "es"` で出力する方式もある。Rollup はモジュールを平らに並べるので、entry のトップレベル関数がそのまま GAS のトップレベル関数になる。ただし entry の関数はどこからも使われないので、tree-shaking を外さないと消える。

```js
// rollup.config.mjs（代替方式）
import esbuild from "rollup-plugin-esbuild";

const keepEntry = (entry) => ({
  name: "gas-keep-entry",
  // transform hook は code を返さず moduleSideEffects だけを返せる
  transform: (_code, id) => (id.endsWith(entry) ? { moduleSideEffects: "no-treeshake" } : null),
});

export default {
  input: "src/main.ts",
  output: { file: "dist/main.js", format: "es" },
  plugins: [keepEntry("src/main.ts"), esbuild({ target: "es2020" })],
};
```

- `moduleSideEffects: "no-treeshake"` は「treeshaking will be turned off for this module」（[Rollup Plugin Development: transform](https://rollupjs.org/plugin-development/#transform)）。clasp 保守者の starter は同じことを `resolveId` で行い、最後の `export {...}` を正規表現で消している（[sqrrrl starter `rollup.config.js`](https://github.com/sqrrrl/apps-script-typescript-rollup-starter/blob/main/rollup.config.js)）。aside は `rollup --no-treeshake` で全体の tree-shaking を切る（[aside `src/config.ts`](https://github.com/google/aside/blob/main/src/config.ts)）
- **代償**: すべての内部関数（`greet` 等）がトップレベルに並ぶので、`google.script.run` から呼べてしまう。Web アプリ・サイドバーを持つプロジェクトでは §9.2 を使う。entry の関数が「未使用」扱いになる問題（§4.2 の `noUnusedLocals`、§4.3 の Biome）もこちらだけに出る
- aside の README は「bundling ... keeps `export`s in the entrypoint causing `clasp` to fail pushing」（Unknown token 'export'）と注意している。この方式では entry に `export` を書かない

### 9.4 GAS 向け bundler plugin を採用しない理由

| plugin | 方式 | 不採用の理由 |
|---|---|---|
| rollup-plugin-google-apps-script 2.0.2（2025-02-16） | `global.foo = ...` の代入からトップレベル関数を生成（gas-entry-generator 利用）。`output.format` を `umd` に上書き | §9.2 は Rollup 標準機能だけで同じことができる。npm への最終公開が 2025-02 |
| gas-webpack-plugin 2.6.0（2024-05-23） | 同上（webpack 用） | webpack を前提にする。最終公開 2024-05 |
| esbuild-gas-plugin 0.10.0（2025-07-22） | 同上（esbuild 用） | clasp README が名指しするのは Rollup |
| @gas-plugin/unplugin 0.2.2（2026-07-18） | unplugin（Vite / Rollup / esbuild 等） | 0.x で star 数 2。採用実績を確認できない |

（各 npm レジストリと公式 repo の README で確認。`global.foo =` 方式は「どの関数を公開するか」がソース中の代入に散るのに対し、§9.2 は entry の export 一覧に集まる）

### 9.5 manifest のコピーと build スクリプト

```jsonc
// package.json（抜粋）
{
  "scripts": {
    "build": "rollup -c && cp appsscript.json dist/appsscript.json",
    "typecheck": "tsc --noEmit",
    "push:dev": "npm run build && clasp push --force",
    "push:prod": "npm run build && clasp push --force -P .clasp.prod.json"
  }
}
```

- clasp は manifest を `<rootDir>/appsscript.json`（コード上は `path.join(contentDir, 'appsscript.json')`）から読む（[`src/core/project.ts`](https://github.com/google/clasp/blob/clasp-v3.4.1/src/core/project.ts) の `readManifest`、[`src/core/services.ts`](https://github.com/google/clasp/blob/clasp-v3.4.1/src/core/services.ts)）。正本はリポジトリ直下に置いて build でコピーする（sqrrrl starter・aside とも同じ）
- HTML（HtmlService）のクライアント側の JS / CSS は、GAS が素の JS / CSS ファイルを配信できないので HTML に inline する必要がある。clasp 保守者の starter は `@web/rollup-plugin-html` で inline している（[sqrrrl starter README](https://github.com/sqrrrl/apps-script-typescript-rollup-starter#rollupjs-config)）

### 9.6 配布の形

| 形 | 手順 | 注意 |
|---|---|---|
| スクリプト本体（バインド / スタンドアロン） | `clasp push` | push は全置換 |
| Web アプリ / API 実行可能 / add-on | versioned deployment を 1 つ作り `update-deployment` で更新（§7.3） | add-on・API 実行可能は標準 GCP プロジェクト必須 |
| Apps Script ライブラリとして配布 | versioned deployment を作り、利用側が script ID と版を指定 | 「doesn't run as quickly」（§2）。`instanceof` が他プロジェクトのオブジェクトに効かない（[Migrate to V8: library instanceof](https://developers.google.com/apps-script/guides/v8-runtime/migration)） |

---

## 10. 環境変数・シークレット管理

### 10.1 GAS 実行時に環境変数は無い

**結論**: GAS ランタイムに `process.env` は無い（`process` は「NOT available」— [V8 runtime limitations](https://developers.google.com/apps-script/guides/v8-runtime#unavailable-apis)）。node reference の「Zod で `process.env` を起動時に検証する」方式は GAS 側のコードには当てはまらない。

- **build 時に定数を埋め込まない**（esbuild の `define` 等）: 値が `dist/main.js` に平文で入り、スクリプトの編集者がエディタで読める（推論）。埋めてよいのは環境名など秘密でない値だけ
- 環境ごとの設定（シート ID、外部 API の URL）は、§3.4 で環境ごとにスクリプトを分けた上で、次の Properties Service に置く

### 10.2 Properties Service をシークレット置き場にすることの是非

**結論**: **Script Properties は公式が「認証情報の置き場」として例示する標準の場所**で、GAS 単体で完結するならここに置く。ただし暗号化や権限分離の仕組みは公式に書かれておらず、**スクリプトの編集者には見える**前提で使う。

**根拠**（[Properties Service](https://developers.google.com/apps-script/guides/properties)）:
- Script Properties の典型用途は「App-wide configuration data, like the username and password for the developer's external database」（公式の比較表）。User Properties はユーザー別、Document Properties は add-on の文書別
- 「Properties are never shared between scripts」。dev / prod でスクリプトを分ければ値も分かれる
- Script Properties は Project Settings 画面から手で 50 個まで編集できる（=「プロジェクト設定を開ける人には見える」。編集者がどこまで読めるかの明文は見つからず、推論）
- 値は文字列のみ、1 値 9 KB、1 ストア 500 KB、読み書きは 1 日 50,000 回（consumer）/ 500,000 回（Workspace）（[Quotas](https://developers.google.com/apps-script/guides/services/quotas)）
- ライブラリでは Script Properties がライブラリ側で共有される（「The same instance is visible to all including scripts when created in the library」— [Libraries: Resource scoping](https://developers.google.com/apps-script/guides/libraries)）。ライブラリにシークレットを置かない
- clasp 3.4.1 には Properties を読み書きするコマンドが無い（コマンド一覧で確認）。値の投入は Project Settings 画面か、一度だけ実行するセットアップ関数で行い、**値そのものをリポジトリに書かない**

**本書の判断**: 漏れたときの影響が大きい鍵（決済 API の秘密鍵など）は、編集者を絞った専用スクリプトに置くか、GAS 側に置かない設計を優先する。Google Cloud Secret Manager を GAS から読む公式手順は本調査では見つけていない（§未確認）。

### 10.3 開発者側のシークレット

| ファイル | 中身 | 扱い |
|---|---|---|
| `~/.clasprc.json` | OAuth refresh token（§7.1） | リポジトリ外。CI は secret から組み立てる |
| `client_secret*.json` | 自前 OAuth クライアントの secret | commit しない（§6） |
| `.clasp.json` | `scriptId` / `rootDir` / `projectId` | 公式は gitignore を勧める（§3.4） |

---

## 11. ツールチェーン一貫性

**結論**: GAS では「どの JS ランタイムで動くか」が経路ごとに違い、Node の版を揃えるだけでは一致しない。揃えるべきは次の表の「何で揃えるか」の列。

| 経路 | 動くランタイム / 参照する型 | 何で揃えるか | 根拠 |
|---|---|---|---|
| ターミナルの clasp / bundler / tsc | ローカルの Node | node reference の版固定（22.12 以上） | §1.1 |
| CI | `actions/setup-node` の Node | `node-version-file` で同じファイルを読む | §7.4 |
| IDE の TypeScript サービス | ワークスペースの `typescript` と `tsconfig.json` の `lib` / `types` | IDE にワークスペースの TS を使わせる。`lib` から DOM、`types` から node を外す | §4.2 |
| bundler の出力 | esbuild の `target` | `target: "es2020"` を tsconfig と揃える | §1.3 |
| 単体テスト（Vitest） | **Node**（GAS の V8 ではない） | GAS に無い API は型で弾く。npm 依存の内部は弾けないので dev スクリプトで実行確認 | §5.1 |
| GAS 本番 | GAS の V8（ES 版の公式表記なし） | `runtimeVersion: "V8"` | §1.2 |

- **テストと本番でランタイムが違う**ことが GAS 固有の最大の不一致。Node にある `setTimeout` / `fetch` / `URL` / `TextEncoder` / `process` を使った依存は、ローカルのテストが通っても GAS で落ちる。§4.2 の tsconfig は自分のコードしか守らない
- **TypeScript の版と bundler plugin の組み合わせ**: TS 7 では TS の JS API を呼ぶ Rollup plugin が動かない（§4.1）。変換を esbuild に任せれば、IDE と `tsc --noEmit` の TS の版を上げても bundle は影響を受けない
- **`@types/google-apps-script` の版**: 2.0.8 以降で Advanced Services が `| undefined` になった。版を上げると型エラーが増えることがある（§3.5）
- 確認コマンド

```bash
node -v && npx clasp --version && npx tsc -v
npx clasp show-file-status --json      # push 対象が dist/main.js と dist/appsscript.json だけか
grep -nE 'this\.#|^\s*#[A-Za-z_$]|^\s*static [A-Za-z_$]+ *=' dist/main.js   # ES2022 クラス構文が残っていないか（§1.3）。何も出なければよい
```

---

## 12. 最新性・廃止手順の回避（古い記事の手順を踏まない）

Qiita / Zenn / 個人ブログには clasp 2.x 時代の「`.ts` をそのまま `clasp push` する」手順が多く残っている。下表の左列はその種の古い手順と、一次情報の中に残る古い記述。

| 古い手順 | 現行の手順 | 根拠 |
|---|---|---|
| `.ts` をそのまま `clasp push` して clasp（ts2gas）に変換させる。`.clasp.json` に `"fileExtension": "ts"`、`rootDir` に `tsconfig.json` | clasp 3.0.0-alpha（2025-03-11）で TS 変換を廃止。bundler で JS にしてから push（§9）。`fileExtension` は deprecated | [CHANGELOG 3.0.0-alpha](https://github.com/google/clasp/blob/clasp-v3.4.1/CHANGELOG.md), [README](https://github.com/google/clasp/blob/clasp-v3.4.1/README.md#drop-typescript-support)。2.4.2 の依存には `typescript` と `ts2gas` があり、3.x には無い（npm レジストリ） |
| developers.google.com の「TypeScript」ガイド（`/apps-script/guides/typescript`） | このページは clasp ガイドへリダイレクトされ、TS の手順は無い | `https://developers.google.com/apps-script/guides/typescript` が `/guides/clasp` へ転送されることを確認 |
| clasp 内の `docs/config-files.md` の「Typescript configuration file `<rootDir>/tsconfig.json`」行、「`.clasprc.json` をプロジェクト直下に置くローカル認証」 | 3.x の `initAuth` は `-A` / `clasp_config_auth` が無ければ `~/.clasprc.json` だけを使う | [`src/auth/auth.ts`](https://github.com/google/clasp/blob/clasp-v3.4.1/src/auth/auth.ts)（doc が 2.x のまま） |
| `clasp login --creds <file>`（「Saves a `.clasprc.json` file to current working directory」） | `clasp login --user <name> --creds <file>`。同じ credential store に名前付きで保存 | README の Command renames 表、`src/commands/login.ts` |
| `--auth <file>` で資格情報ファイルを切り替える | `--user <name>`（`--auth` は DEPRECATED） | [README: Global options](https://github.com/google/clasp/blob/clasp-v3.4.1/README.md#global-options) |
| 2.x のコマンド: `clasp open` / `open --web` / `apis enable` / `deploy -i <id>` / `logs --open` / `settings` | `open-script` / `open-web-app` / `enable-api` / `update-deployment <id>` / `open-logs` / なし | [README: Command renames](https://github.com/google/clasp/blob/clasp-v3.4.1/README.md#command-renames)。`create` / `clone` / `deploy` / `version` / `run` / `logs` 等は別名として残る（`clasp help` で確認） |
| `npm i -D typescript @rollup/plugin-typescript`（または `rollup-plugin-typescript2`） | TS 7 では両 plugin が失敗する。rollup-plugin-esbuild + `tsc --noEmit`（§4.1） | 本調査での実行結果、[rollup/plugins#2016](https://github.com/rollup/plugins/issues/2016) |
| bundle の entry に `export` を残したまま `format: "es"` で push（Unknown token 'export'） | IIFE + footer スタブ（§9.2）か、entry に export を書かない es 方式（§9.3） | [V8 runtime: Module limitations](https://developers.google.com/apps-script/guides/v8-runtime#module_limitations), [aside README](https://github.com/google/aside#unknown-token-export) |
| `appsscript.json` の `runtimeVersion` を省略 / `DEPRECATED_ES5` | `"V8"` を明示 | Rhino は 2026-01-31 に deprecated（§1.2） |
| tsconfig `target: "ES2022"` 以上（node reference の値をそのまま流用） | `es2020` | GAS V8 は `#private` と static field 宣言を受け付けない（§1.3） |
| tsconfig の `lib` に `"dom"`、`types` に `"node"` | 外す | GAS に無い API を型で弾くため（§4.2） |
| `@types/google-apps-script` 1.x 前提で `Drive.Files...` をそのまま呼ぶ | 2.0.8 以降は `\| undefined`。ガードを書く | [DefinitelyTyped#74133](https://github.com/DefinitelyTyped/DefinitelyTyped/pull/74133)（§3.5） |
| CI で `clasp push`（`--force` なし） | `clasp push --force`。無いと manifest 変更時に exit 0 で skip | `src/commands/push.ts`（§7.2） |
| main への push ごとに `clasp version` / `clasp deploy` | バージョンはリリース時だけ。deployment は ID を固定して `update-deployment` | 200 バージョン上限、API に削除なし（§7.3） |
| 公式ガイドの CI 例（`actions/checkout@v6.3`、`node-version: "20"`、`echo '${{ secrets.CLASPRC_JSON }}'`） | §7.4 の表のとおり | 存在しない ref・EOL の Node・GitHub の secure-use に反する書き方 |
| service account / `--adc` / Workload Identity Federation で CI 認証 | 専用ユーザーの refresh token | §7.1（Apps Script API は service account 非対応、clasp の ADC 判定） |
| 自前 OAuth クライアントを「外部・テスト」のまま CI に使う | 「本番」または「内部」に | テスト状態の refresh token は 7 日で失効（§7.1） |
| `.clasp-dev.json` を `.clasp.json` にコピーして環境を切り替える（aside の雛形） | `clasp push -P .clasp.prod.json` | [README: Global options](https://github.com/google/clasp/blob/clasp-v3.4.1/README.md#global-options) |
| clasp 3.3.0 以前 | 3.4.1 以上（パストラバーサル・CSRF 修正） | §8 |
| 公式ガイド冒頭の要約「Node.js version 4.7.4 or later」 | 22.12 以上（§1.1） | 同ページ本文は 20.0.0、clasp の `engines` は 20、README は 22 |

### 12.1 検討して採らなかった選択肢

> **glasp（Go 製の clasp 互換 CLI）は採らない**。`.clasp.json` / `.claspignore` は読めるが、TS は esbuild でファイルごとに変換するだけで bundle しない（clasp 2.x / ts2gas と同じモデル。v0.5.1 の `transform.go` は `Bundle = false`、`import` / `export` は警告のみ）。`push --force` は `.claspignore` を無視する意味、`deploy` は `update-deployment` の別名、`version` は glasp 自身の版表示で、`create-version` と `show-file-status` は無い。§7 の CI を差し替えると挙動が変わる。トークンはプロジェクト内の `.glasp/access.json` に置かれ、§6 の pre-commit 検査では捕まらない。`--auth` が読む `.clasprc.json` の形は clasp 2.x の `token` で、3.x の `tokens.<user>` を読むフィールドはソースに無い。npm で配布されないので lockfile での版固定にも乗らない。要求スコープが clasp より狭い（`cloud-platform` を含まない）点は利点だが、上の差を埋めるほどではない（[glasp v0.5.1](https://github.com/takihito/glasp/tree/v0.5.1)）

> **JS + JSDoc + `tsc --checkJs --noEmit`（bundler なし）は、npm の runtime 依存が無く、単体テストをほとんど書かない小さなスクリプトに限って選べる**。`import` / `export` の無い `.js` は tsc でもグローバルスクリプトとして扱われ、ファイルをまたいだ関数・`const`・`@typedef` の参照と `@types/google-apps-script` による型検査が効く（TS 7.0.2 / 6.0.3 で確認）。ただし、別ファイルの同名 `function` の重複とファイルの読み込み順（トップレベルの `const` の前方参照）は tsc が検出しない。`moduleDetection: "force"` は付けない。ファイルが module ではないので Vitest から import できず、§5.1 のテスト構成が使えない。テストを書く、または npm 依存を使うなら TS + bundler（§9）を選ぶ

> **@mcpher/gas-fakes はテスト基盤にしない**。GAS の呼び出しを実 Google API に変換して Node 上で動かすエミュレータで、mock ではない（§5.1 の比較表）。dev アカウントでデバッガを使った動作確認の補助としてだけ使う（§5.2）

---

## 13. 推奨構成まとめ（シンプルさ優先）

### バージョン管理
- ローカル Node は 22.12 以上の LTS（固定方法は node reference）。`appsscript.json` に `"runtimeVersion": "V8"`。出力は `es2020`

### 依存関係
- clasp・`@types/google-apps-script`・bundler は devDependency で版固定。runtime 依存は GAS で動くものだけ。ライブラリより bundle

### 構造
- `src/` → `dist/`、`.clasp.json` の `rootDir: "dist"`、`.claspignore` は許可リスト。環境ごとに別スクリプト + `-P`。`.clasp*.json` / `.clasprc.json` / `client_secret*.json` は gitignore

### 開発ツール
- **型検査**: `tsc --noEmit`（`lib: ["es2020"]`、`types: ["google-apps-script"]`）
- **bundle**: Rollup + rollup-plugin-esbuild、IIFE + entry の export からスタブ生成
- **lint / format / テスト / フック**: node reference。GAS 固有は「公開関数を未使用扱いで消させない」「ドメインは純粋関数、表の読み書きは port + in-memory fake、adapter は vitest-mock-extended の `mock<T>()`、グローバルは entry だけ `vi.stubGlobal`」「資格情報ファイルの commit を止める」

### CI
- PR: 型検査（src とテスト）・lint・テスト・build・`show-file-status`
- main: dev スクリプトへ `clasp push --force`
- tag: prod スクリプトへ `clasp push --force` + `update-deployment <固定 ID>`（environment の承認つき）
- 認証は専用アカウントの refresh token を値ごとの secret に分けて渡す

### クイックスタート

```bash
mkdir gas-app && cd gas-app
# パッケージマネージャー・Node の固定は node reference に従う（例は npm）
npm init -y
npm i -D @google/clasp @types/google-apps-script rollup rollup-plugin-esbuild esbuild typescript vitest vitest-mock-extended

npx clasp login                                   # ~/.clasprc.json に保存（Apps Script API をユーザー設定で有効化しておく）
npx clasp create-script --type sheets --title "gas-app" --rootDir dist
# → カレントに .clasp.json（rootDir: "dist"）ができ、manifest が書き出される（本調査では実際の作成は未実行。出力先を確かめる）
# manifest はリポジトリ直下へ移して正本にし、runtimeVersion / timeZone / exceptionLogging / oauthScopes を §3.5 のとおり書く

# src/main.ts（export のみ）、rollup.config.mjs（§9.2）、tsconfig.json（§4.2）、.claspignore（§3.3）を置く
npx tsc --noEmit
npx rollup -c && cp appsscript.json dist/
npx clasp show-file-status
npx clasp push --force
```

---

## 未確認のまま残った主張

- §9.2 の IIFE + footer スタブ方式で、simple trigger（`onOpen`）・`google.script.run`・メニュー項目が実際の GAS 上で動くこと（bundle の生成までは確認、push しての実行は未実施）
- GAS の V8 が対応する ECMAScript の版（公式に版番号の記載なし）。ES2021 構文（論理代入演算子など）が通るか
- `oauthScopes` を明示したときに `@OnlyCurrentDoc` が無視されるか（公式に明文なし）
- bundle すると自動スコープ判定が依存コードまで走査して広くなる、という推論（§3.5）
- CI で `access_token` を省いた V3 形式の `~/.clasprc.json` から clasp が refresh して動くこと（コードからの推論で、CI では未実行。§7.4）
- `--adc` に `authorized_user` 型の ADC（`gcloud auth application-default login` 等）を渡した場合に clasp が動くか。`UserRefreshClient` は `OAuth2Client` を継承するので判定は通るが、必要なスコープを付けて発行できるかを含め未確認
- Script Properties がスクリプト編集者・閲覧者からどこまで読めるかの公式の明文（§10.2 は推論）。Google Cloud Secret Manager を GAS から使う公式手順
- GAS サービスをローカルで模倣する公式ライブラリ・公式のテスト手順の有無（developers.google.com では見つからなかった。非公式ライブラリの比較は §5.1）
- `@mcpher/gas-fakes` で bundle 済みの `dist/main.js`（IIFE + footer スタブ、§9.2）を読み込んで実行できるか
- `@mcpher/gas-fakes` を CI（ADC / DWD / WIF）で動かす手順の中身（例 repo の存在までは確認、内容は未読）
- glasp に clasp 3.x 形式の `~/.clasprc.json` を `--auth` で渡したときの実際の挙動（ソース上は `tokens.<user>` を読むフィールドが無いことまでは確認、実行はしていない）
- glasp の `update-deployment` / `create-deployment` が `-V` 無しでバージョンを自動作成するか、`run-function` に必要なスコープが 3 つのスコープで足りるか
- vitest-mock-extended の上流が Vitest 5 で CI を回しているか（devDependencies は vitest 4.1.2。Vitest 5 対応の根拠は peer の範囲と本調査での実行だけ）
- @ciderjs/vitest-plugin-gas-mock の `mockChain` 上書きが別のテストファイルへ漏れるか（同一ファイル内で消せないことはソースで確認。Vitest の既定 `isolate: true` ならファイル単位で module が分かれるはずだが試していない）
- @dougborg/gas-test-utils の型定義が `@types/google-apps-script` と整合しているか（README のみ確認）
- JS + JSDoc 方式で、別ファイルの同名トップレベル関数が GAS 上でどちらに解決されるか。JSDoc の `import("...")` 型参照を使ってもファイルがグローバルスクリプトのまま扱われるか
- JS + JSDoc 方式のファイル群を `node:vm` で 1 つの context に読み込んで Vitest でテストする方法の実際の書き方
- clasp 3.4.1 の Node 24 / 26 での動作保証（上流 CI は Node 20 のみ。本調査で Node 26.10.0 で基本コマンドが動くことは確認）
- `create-deployment` で作る deployment の種類（Web アプリ / API 実行可能）が manifest の `webapp` / `executionApi` で決まるかの明文（clasp README は API 実行可能のデプロイを「Easiest done via GUI」とする）
- `@types/google.script.client-side` 0.1.5（2023-11-21）が現行の `google.script` API と一致しているか
- `oauthScopes` を明示したとき、認可が列挙したスコープだけで決まる（Advanced Service の分も足す必要がある）こと（§3.5。scopes ページの記述からの推論）
- TypeScript 7 の公開 API（`exports['.']`）が 6 系までの JS API とどこまで互換か（§4.1。確かめたのは 2 つの Rollup plugin が失敗することだけ）
- `clasp create-script --rootDir dist` が manifest をどこへ書き出すか（クイックスタート。実スクリプトの作成は実行していない）

## 参考資料（一次情報）

- clasp: [README](https://github.com/google/clasp/blob/clasp-v3.4.1/README.md) / [CHANGELOG](https://github.com/google/clasp/blob/clasp-v3.4.1/CHANGELOG.md) / [docs/run.md](https://github.com/google/clasp/blob/clasp-v3.4.1/docs/run.md) / [docs/config-files.md](https://github.com/google/clasp/blob/clasp-v3.4.1/docs/config-files.md) / [package.json](https://github.com/google/clasp/blob/clasp-v3.4.1/package.json) / [GitHub Releases](https://github.com/google/clasp/releases) / [npm](https://www.npmjs.com/package/@google/clasp)
- clasp ソース（tag `clasp-v3.4.1`）: [src/commands/push.ts](https://github.com/google/clasp/blob/clasp-v3.4.1/src/commands/push.ts), [src/commands/utils.ts](https://github.com/google/clasp/blob/clasp-v3.4.1/src/commands/utils.ts), [src/commands/program.ts](https://github.com/google/clasp/blob/clasp-v3.4.1/src/commands/program.ts), [src/commands/login.ts](https://github.com/google/clasp/blob/clasp-v3.4.1/src/commands/login.ts), [src/auth/auth.ts](https://github.com/google/clasp/blob/clasp-v3.4.1/src/auth/auth.ts), [src/auth/file_credential_store.ts](https://github.com/google/clasp/blob/clasp-v3.4.1/src/auth/file_credential_store.ts), [src/auth/oauth_client.ts](https://github.com/google/clasp/blob/clasp-v3.4.1/src/auth/oauth_client.ts), [src/core/clasp.ts](https://github.com/google/clasp/blob/clasp-v3.4.1/src/core/clasp.ts), [src/core/project.ts](https://github.com/google/clasp/blob/clasp-v3.4.1/src/core/project.ts), [src/core/functions.ts](https://github.com/google/clasp/blob/clasp-v3.4.1/src/core/functions.ts)
- Apps Script: [Use the CLI with clasp（CI/CD 節を含む）](https://developers.google.com/apps-script/guides/clasp), [V8 runtime overview](https://developers.google.com/apps-script/guides/v8-runtime), [Migrate scripts to V8](https://developers.google.com/apps-script/guides/v8-runtime/migration), [Manifest structure](https://developers.google.com/apps-script/manifest), [Manifest: Dependencies](https://developers.google.com/apps-script/manifest/dependencies), [Authorization scopes](https://developers.google.com/apps-script/concepts/scopes), [Authorization for Google Services](https://developers.google.com/apps-script/guides/services/authorization), [Advanced services](https://developers.google.com/apps-script/guides/services/advanced), [Libraries](https://developers.google.com/apps-script/guides/libraries), [Properties Service](https://developers.google.com/apps-script/guides/properties), [Quotas](https://developers.google.com/apps-script/guides/services/quotas), [Deployments](https://developers.google.com/apps-script/concepts/deployments), [Versions](https://developers.google.com/apps-script/guides/versions), [Google Cloud projects](https://developers.google.com/apps-script/guides/cloud-platform-projects), [Logging](https://developers.google.com/apps-script/guides/logging), [HTML Service: Communicate with server functions](https://developers.google.com/apps-script/guides/html/communication), [google.script.run reference](https://developers.google.com/apps-script/guides/html/reference/run), [Execute functions with the Apps Script API](https://developers.google.com/apps-script/api/how-tos/execute), [REST: projects.versions](https://developers.google.com/apps-script/api/reference/rest/v1/projects.versions), [Best practices](https://developers.google.com/apps-script/guides/support/best-practices)
- Google Identity: [Using OAuth 2.0 to Access Google APIs: Refresh token expiration](https://developers.google.com/identity/protocols/oauth2#expiration)
- 型定義: [@types/google-apps-script（npm）](https://www.npmjs.com/package/@types/google-apps-script), [DefinitelyTyped types/google-apps-script](https://github.com/DefinitelyTyped/DefinitelyTyped/tree/master/types/google-apps-script), [DefinitelyTyped#74133](https://github.com/DefinitelyTyped/DefinitelyTyped/pull/74133), [DefinitelyTyped#73466](https://github.com/DefinitelyTyped/DefinitelyTyped/pull/73466)
- Rollup: [Configuration Options](https://rollupjs.org/configuration-options/), [Plugin Development](https://rollupjs.org/plugin-development/), [npm](https://www.npmjs.com/package/rollup), [rollup-plugin-esbuild](https://github.com/egoist/rollup-plugin-esbuild), [esbuild](https://esbuild.github.io/), [rollup/plugins#2016](https://github.com/rollup/plugins/issues/2016), [rollup/plugins#2012](https://github.com/rollup/plugins/issues/2012)
- テンプレート（clasp README が挙げるもの）: [google/aside](https://github.com/google/aside), [sqrrrl/apps-script-typescript-rollup-starter](https://github.com/sqrrrl/apps-script-typescript-rollup-starter)
- GAS 向け plugin（不採用）: [mato533/rollup-plugin-gas](https://github.com/mato533/rollup-plugin-gas), [fossamagna/gas-webpack-plugin](https://github.com/fossamagna/gas-webpack-plugin), [mahaker/esbuild-gas-plugin](https://github.com/mahaker/esbuild-gas-plugin), [wakita181009/gas-plugin](https://github.com/wakita181009/gas-plugin)
- Node.js: [Release schedule.json](https://github.com/nodejs/Release/blob/main/schedule.json)
- Vitest: [vi.stubGlobal](https://vitest.dev/api/vi.html#vi-stubglobal), [npm](https://www.npmjs.com/package/vitest)
- テスト用 mock / fake: [vitest-mock-extended](https://github.com/eratio08/vitest-mock-extended), [jest-mock-extended](https://github.com/marchaos/jest-mock-extended), [@ciderjs/vitest-plugin-gas-mock](https://github.com/luthpg/vitest-plugin-gas-mock), [@dougborg/gas-test-utils](https://www.npmjs.com/package/@dougborg/gas-test-utils), [gasmask](https://github.com/vlucas/gasmask), [gas-mock-globals](https://github.com/dan-kushnaryov/google-apps-script-services), [gas-local](https://github.com/mzagorny/gas-local), [@mcpher/gas-fakes](https://github.com/brucemcpherson/gas-fakes)（[notes/sandbox.md](https://github.com/brucemcpherson/gas-fakes/blob/main/notes/sandbox.md)）
- 採らなかった clasp 代替: [glasp v0.5.1](https://github.com/takihito/glasp/tree/v0.5.1)（[internal/transform/transform.go](https://github.com/takihito/glasp/blob/v0.5.1/internal/transform/transform.go), [internal/auth/payload.go](https://github.com/takihito/glasp/blob/v0.5.1/internal/auth/payload.go), [internal/auth/token_store.go](https://github.com/takihito/glasp/blob/v0.5.1/internal/auth/token_store.go)）
- GitHub Actions: [actions/checkout](https://github.com/actions/checkout), [actions/setup-node](https://github.com/actions/setup-node), [Secure use reference](https://docs.github.com/en/actions/reference/security/secure-use), [Deployments and environments](https://docs.github.com/en/actions/reference/workflows-and-actions/deployments-and-environments)
