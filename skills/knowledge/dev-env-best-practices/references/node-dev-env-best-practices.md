# 中規模Node.jsツール開発環境のベストプラクティス（2026年版）

**対象**: 写真管理ツールのような CLI + Web UI を含む中規模 Node.js/TypeScript アプリケーション
**最終更新**: 2026-06-08

> 姉妹版に [`python-dev-env-best-practices.md`](python-dev-env-best-practices.md) があります。同じ photo-manager 題材で章立てを揃えています。

---

## 1. パッケージマネージャー

### 推奨: pnpm

**結論**: 2026年時点で新規プロジェクトは **pnpm** を既定とします。npm は「最も安全なデフォルト」ですが、性能・正しさで劣後します。

- **バージョン**: v10.x 主流
- **特徴**:
  - npm の 2-3倍高速。content-addressable store + ハードリンクでディスク **50-70% 削減**
  - モノレポでは共有ストアにより 3-5倍高速・ディスク 60-80% 削減
  - **phantom dependency（幽霊依存）を構造的に防止** — 各パッケージは `package.json` で宣言した依存のみアクセス可能
  - pnpm-lock.yaml は形式変更が少なく安定

### phantom dependency とは

npm は全依存を `node_modules` ルートにホイストするため、**宣言していない依存にアクセスできてしまう**。「ローカルは通るが CI / 別環境で壊れる」の典型的原因。pnpm は厳密リンクでこれを構造的に防ぎます。

### 基本ワークフロー

```bash
# インストール（corepack 経由・後述）
corepack enable pnpm

# プロジェクト初期化
pnpm init

# 依存関係追加
pnpm add fastify pino

# 開発依存関係追加
pnpm add -D vitest typescript @types/node

# 依存関係同期（lockfile 厳密・再現性）
pnpm install --frozen-lockfile

# スクリプト実行
pnpm exec tsx src/cli.ts

# テスト実行
pnpm test
```

### npm が妥当なケース（オプション）

以下なら npm 継続でも可：

- **単一の小スクリプト**: pnpm の性能差が体感されない
- **既存プロジェクト**: npm で問題なければ移行不要
- **エコシステム最優先**: yarn PnP は性能で pnpm に匹敵するが `.pnp.cjs` 方式でセットアップ複雑性が増すため非推奨

**シンプルさ優先の原則**: 新規・中規模以上は pnpm を推奨。

**出典**: [pnpm benchmarks](https://pnpm.io/benchmarks), [npm→pnpm で幽霊依存を発見 - Mergify](https://mergify.com/blog/npm-to-pnpm-phantom-dependencies), [Package Manager 比較 - DeployHQ](https://www.deployhq.com/blog/choosing-the-right-package-manager-npm-vs-yarn-vs-pnpm-vs-bun)

---

## 2. Node.js バージョン管理

Python の uv はバージョン管理内蔵ですが、Node.js は**専用ツール + 固定ファイルの二重化**が必要です。

### バージョン管理ツール: fnm / Volta / mise

**結論**: nvm からの脱却が 2026年の流れ。nvm はシェルスクリプト実装で起動 +75ms・切替 180ms と遅い。Rust 製ツールは桁違いに速い（fnm 切替 12ms = nvm の15倍）。

| ツール | 速度 | チーム統一 | 選ぶべき状況 |
|---|---|---|---|
| **fnm** | 切替12ms | 中（`.nvmrc` 流用可） | nvm から最小コストで移行、速度最優先 |
| **Volta** | 高速 | 強（package.json で自動強制） | バージョンを git commit で強制統一したい |
| **mise** | 高速 | 中（既存ファイル全読込） | Python 等も同時管理・環境変数/タスク統合まで欲しい |

> この dotfiles 環境は **mise** を採用済み（package-management skill 参照）。多言語統一の観点で mise 継続が整合的。

### 固定は「宣言」と「切替」を分けて二重化する

3つのレイヤーは役割が違う。混同すると「固定したつもりで効いていない」が起きます。

| ファイル/フィールド | 役割 | 強制力 |
|---|---|---|
| `package.json` の `engines` | 互換性の**宣言** | デフォルト警告のみ。`.npmrc` に `engine-strict=true` で初めて install 失敗 |
| `.nvmrc` / `.node-version` | バージョン**切替**の指示 | ツール（fnm 等）が読んで切替。単体では何もしない |
| `package.json` の `volta` / `packageManager` | 切替の**強制** | Volta / corepack がプロジェクト進入時に自動適用 |

```jsonc
// package.json
{
  "engines": {
    "node": ">=22.0.0 <23.0.0",
    "pnpm": ">=9.0.0"
  },
  "packageManager": "pnpm@10.0.0+sha512.xxxxx"  // corepack 用・ハッシュで改ざん防止（pnpm は sha512 が一般的、sha224/256 も可）
}
```

```
# .nvmrc（プレーンテキスト1行・末尾改行必須・前後空白なし）
22.14.0
```

> **罠**: `engines` は単独でバージョン自動切替を**行わない**。別途 `.nvmrc` か Volta が必須。`.nvmrc` の形式を崩す（末尾空白等）と一部ツールが読まない。

### corepack でパッケージマネージャー版も固定

`package.json` の `packageManager` を corepack で管理すると、Node.js さえ入っていればチーム全員が同一マネージャー版を自動取得します。

```bash
corepack enable          # corepack 有効化
corepack use pnpm@10.0.0  # packageManager フィールドに記録
```

> **注意**: corepack は実験的ステータスで Node.js からの分離・将来削除の議論が継続中。長期安定を最優先するなら Volta の `packageManager` 統一の方が枯れている。

**出典**: [500x performance gap - nodevibe](https://nodevibe.substack.com/p/the-500x-performance-gap-between), [NVM Alternatives - BetterStack](https://betterstack.com/community/guides/scaling-nodejs/nvm-alternatives-guide/), [Corepack - Node.js Docs](https://nodejs.org/api/corepack.html), [.nvmrc - David Walsh](https://davidwalsh.name/nvmrc)

---

## 3. プロジェクト構造

### src / dist layout（推奨）

中規模以上では **`src/`（TypeScript ソース）と `dist/`（ビルド成果物）を分離**します。

```
photo-manager/
├── src/
│   ├── cli.ts            # CLI エントリーポイント
│   ├── main.ts           # Fastify app（Web UI）
│   ├── routes/           # API ルート
│   │   └── photos.ts
│   ├── core/             # コアロジック
│   │   └── storage.ts
│   ├── templates/        # サーバーサイドテンプレート
│   │   └── gallery.html
│   ├── env.ts            # 型安全な環境変数（後述）
│   └── utils/
│       └── imaging.ts
├── tests/
│   ├── api.test.ts       # API テスト（Vitest）
│   ├── cli.test.ts
│   ├── e2e.test.ts       # Playwright E2E
│   └── storage.test.ts
├── dist/                 # ビルド成果物（.gitignore）
├── docs/
├── package.json
├── tsconfig.json
├── biome.json            # or eslint.config.js + .prettierrc
└── README.md
```

### モノレポにする場合

1チームで shared lib を頻繁更新し frontend+backend を同期するなら **Turborepo + pnpm Workspaces + Changesets**。原子的コミット・設定集約・smart caching で CI を 42% 削減できます。独立チーム/異なるリリースサイクル/OSS化ならシングルレポ。

```
monorepo/
├── apps/
│   ├── web/              # Web frontend
│   └── api/              # backend
├── packages/
│   ├── ui/               # 共有コンポーネント
│   ├── utils/            # 共有ユーティリティ
│   ├── types/            # 共有型定義
│   └── config/           # 共有設定（tsconfig, biome）
├── turbo.json
├── pnpm-workspace.yaml
├── package.json
└── tsconfig.base.json
```

> **前提条件**: モノレポは Turborepo+pnpm+Changesets の3層設定で学習コスト高。小規模単一プロダクトには過剰。

**出典**: [JavaScript Monorepos - Robin Wieruch](https://www.robinwieruch.de/javascript-monorepos/)

---

## 4. package.json / tsconfig.json 構成

### package.json（ESM 標準）

```jsonc
{
  "name": "photo-manager",
  "version": "0.1.0",
  "type": "module",              // ESM をデフォルトに（2026標準）
  "engines": { "node": ">=22" },
  "packageManager": "pnpm@10.0.0+sha512.xxxxx",
  "bin": { "photo-manager": "./dist/cli.js" },
  "scripts": {
    "dev": "tsx watch src/server.ts",
    "build": "tsup src/cli.ts src/server.ts --format esm --dts",
    "test": "vitest run",
    "test:watch": "vitest",
    "lint": "biome check .",
    "format": "biome format --write ."
  },
  "dependencies": {
    "fastify": "^5.0.0",
    "zod": "^4.0.0"
  },
  "devDependencies": {
    "typescript": "^5.7.0",
    "tsx": "^4.19.0",
    "tsup": "^8.3.0",
    "vitest": "^3.0.0",
    "@types/node": "^22.0.0",
    "@biomejs/biome": "^2.1.0"
  }
}
```

### tsconfig.json（strict + α）

`strict: true` は前提。さらに実行時バグを型段階で捕捉するオプションを足します。

```jsonc
{
  "compilerOptions": {
    "target": "ES2022",
    "module": "NodeNext",
    "moduleResolution": "nodenext",  // Node.js のみ→nodenext（.js 拡張子必須）。Vite/バンドラ使用なら "bundler"
    "lib": ["ES2022"],
    "outDir": "./dist",
    "rootDir": "./src",

    "strict": true,                       // strict は前提。以下は strict 非包含なので個別指定が必要
    "noUncheckedIndexedAccess": true,     // 配列インデックスに | undefined を付与
    "exactOptionalPropertyTypes": true,
    "noImplicitOverride": true,
    "noFallthroughCasesInSwitch": true,
    "noPropertyAccessFromIndexSignature": true,

    "esModuleInterop": true,
    "skipLibCheck": true,
    "declaration": true,
    "sourceMap": true
  },
  "include": ["src/**/*"],
  "exclude": ["node_modules", "dist"]
}
```

> **モジュール解決の分岐**: Node.js のみ（バンドラなし）→ `moduleResolution: "nodenext"`（`.js` 拡張子を import 文に明記）。Vite/Next.js → `"bundler"`（TS 5.0+ 推奨、拡張子不要）。
>
> **前提条件**: strict 系を一度に全有効化すると既存コードで型エラーが大量発生。既存プロジェクトは段階導入。

> **将来**: TypeScript のネイティブ移植（Go 言語による書き直し・通称 tsgo / Project Corsa）が約10倍高速を掲げて進行中。メジャー版番号（7 とされる）と正式リリース時期は流動的なため、採用判断時に公式アナウンスを確認すること。

**出典**: [TSConfig Reference](https://www.typescriptlang.org/tsconfig/), [module resolution - BetterStack](https://betterstack.com/community/guides/scaling-nodejs/typescript-module-resolution/)

---

## 5. UI フレームワーク

> この章は調査スコープ外のため公式ドキュメントを出典とした一般的推奨です。プロジェクト要件で再検証してください。

### CLI: Commander（シンプル）/ oclif（大規模）

| フレームワーク | 用途 |
|--------------|------|
| **Commander** | 最小ボイラープレート、単機能〜中規模 CLI の定番 |
| **oclif** | プラグイン機構・複数サブコマンドの大規模 CLI（Salesforce 製） |
| **clipanion** | TypeScript ファースト・型安全（yarn が採用） |

```typescript
// src/cli.ts
import { Command } from "commander";

const program = new Command();

program
  .command("import")
  .description("Import photos from source directory")
  .argument("<source>", "source directory")
  .option("-m, --mode <mode>", "copy or move", "copy")
  .option("-v, --verbose", "verbose output")
  .action((source, opts) => {
    if (opts.verbose) console.log(`Importing from ${source} (mode: ${opts.mode})`);
    // 処理...
  });

program.parse();
```

### Web UI: Fastify or Hono + HTMX + Alpine.js

**「手軽に作れてテストもしやすい」を優先するなら、Python 版と同じく HTMX + Alpine.js の構成が有効です**（フロントエンド非依存のためそのまま使える）。

| 層 | 技術 | 役割 |
|----|------|------|
| バックエンド | **Fastify**（成熟）or **Hono**（TS ファースト・edge 対応） | API、型安全性 |
| フロントエンド | HTMX (~14KB) | サーバー通信、コンテンツ更新 |
| UI インタラクション | Alpine.js (~15KB) | モーダル、ドロップダウン等 |
| テンプレート | @fastify/view (ejs/handlebars) | サーバーサイドレンダリング |

```typescript
// src/main.ts — テストしやすいよう build() を export し、listen は分離する
import Fastify, { type FastifyServerOptions } from "fastify";

export function build(opts: FastifyServerOptions = {}) {
  const app = Fastify({ logger: true, ...opts });

  app.get("/api/photos", async () => {
    return [{ id: "1", name: "photo.jpg", url: "/static/photos/photo.jpg" }];
  });

  app.post("/api/photos", async () => {
    // 非同期画像処理
    return { id: "...", url: "/static/photos/uploaded.jpg" };
  });

  return app;
}
```

```typescript
// src/server.ts — エントリーポイント（listen はここだけ。テストからは import しない）
import { build } from "./main.js";
import { env } from "./env.js";

build().listen({ port: env.PORT });
```

```bash
# 依存関係追加
pnpm add fastify @fastify/static @fastify/view
pnpm add -D vitest @playwright/test

# 開発サーバー起動（ホットリロード）
pnpm dev

# テスト実行
pnpm test
```

**出典**: [Commander.js](https://github.com/tj/commander.js), [oclif](https://oclif.io/), [Fastify](https://fastify.dev/), [Hono](https://hono.dev/), [HTMX and Alpine.js - InfoWorld](https://www.infoworld.com/article/3856520/htmx-and-alpine-js-how-to-combine-two-great-lean-front-ends.html)

---

## 6. テスト環境

### テストフレームワーク: Vitest（推奨）

**結論**: 新規 TypeScript プロジェクトは原則 **Vitest**（ESM ネイティブ・TS ゼロコンフィグ・Jest比 5-28倍高速・Watch が HMR 駆動）。

| 状況 | 推奨 |
|-----|------|
| Vite ベース / 新規 TypeScript | **Vitest** |
| React Native / 大規模レガシー CJS | **Jest** |
| 依存ゼロの小 utility | **Node.js 組み込み `node:test`** |

```bash
pnpm add -D vitest @vitest/coverage-v8
```

```typescript
// tests/storage.test.ts
import { describe, it, expect } from "vitest";
import { PhotoStorage } from "../src/core/storage.js";

describe("PhotoStorage", () => {
  it("写真をインポートできる", () => {
    const storage = new PhotoStorage("/tmp/photos");
    const result = storage.importPhoto("test.jpg");
    // 「成功フラグが立つ」だけでなく返り値の中身を検証
    expect(result.success).toBe(true);
    expect(result.path).toContain("test.jpg");
  });
});
```

```typescript
// tests/api.test.ts（Fastify inject でサーバー起動不要）
import { it, expect } from "vitest";
import { build } from "../src/main.js";

it("写真一覧を返す", async () => {
  const app = build();
  const res = await app.inject({ method: "GET", url: "/api/photos" });
  expect(res.statusCode).toBe(200);
  expect(res.json()).toBeInstanceOf(Array);
});
```

```bash
# カバレッジ付きテスト実行
pnpm vitest run --coverage
```

> **前提条件**: Webpack ベース（非Vite）プロジェクトでは Vitest セットアップ負荷あり。`node:test` は Watch/Snapshot 等の DX が新しめの Node.js 版で安定化しつつあるが、matcher の種類は依然限定的（具体的な安定版は[公式ドキュメント](https://nodejs.org/api/test.html)で確認）。

**出典**: [node:test vs Vitest vs Jest - PkgPulse](https://www.pkgpulse.com/guides/node-test-vs-vitest-vs-jest-native-test-runner-2026), [Test runner - Node.js Docs](https://nodejs.org/api/test.html)

---

## 7. コード品質ツール

### リンター + フォーマッター: Biome（統合）/ oxlint（速度）/ ESLint（エコシステム）

**結論**: 2024-2026 はリンター転換期。3軸で割れます。新規・設定統一なら **Biome** を第一候補に。

| | oxlint | Biome | ESLint + Prettier |
|---|---|---|---|
| 相対速度 | 基準（50-100x） | ~2x遅 | 最遅 |
| プラグイン | β（2026-03） | なし | 成熟・最強 |
| format統合 | なし | **あり（lint+format 1ファイル）** | なし（別ツール） |
| 型推論 | N/A | 85%（v2.1） | 100%（typescript-eslint） |

**選定フロー**: lint時間がボトルネック → oxlint / プラグイン必須 → ESLint / それ以外で新規 → Biome。

### A. Biome（推奨：新規・設定統一）

ESLint + Prettier を1ツール（biome.json）で置換。10-25倍高速。v2 で型推論（`noFloatingPromises` 85%カバー）追加。

```bash
pnpm add -D @biomejs/biome
pnpm biome init
```

```jsonc
// biome.json
{
  "$schema": "https://biomejs.dev/schemas/2.1.0/schema.json",
  "linter": {
    "enabled": true,
    "rules": { "recommended": true }
  },
  "formatter": {
    "enabled": true,
    "indentStyle": "space",
    "lineWidth": 100
  }
}
```

```bash
pnpm biome check .          # lint + format チェック
pnpm biome check --write .  # 自動修正
```

> **弱点**: HTML/CSS/Markdown 未対応。これらのフォーマットが必須なら Prettier 併用 or ESLint+Prettier 継続。

### B. ESLint（プラグイン/カスタムルール必須）

React/Next.js 等のプラグインエコシステムが最強。ただし **v9以降は flat config（`eslint.config.js`）必須**。v10（2026-02）で `.eslintrc.*`・`.eslintignore`・`--env` 等が**完全廃止**、v9.x EOL は **2026-08-06**。

```javascript
// eslint.config.js（flat config）
import js from "@eslint/js";
import tseslint from "typescript-eslint";

export default [
  { ignores: ["dist/", "node_modules/"] },  // .eslintignore は廃止、ここに記述
  js.configs.recommended,
  ...tseslint.configs.recommended,
  {
    files: ["**/*.ts"],
    languageOptions: { parserOptions: { ecmaVersion: 2022 } },
    rules: { "no-console": "warn" },
  },
];
```

### C. oxlint（速度がボトルネック）

50-100倍高速・ゼロ設定。Airbnb の 126,000 ファイルを 7秒。プラグイン不在（2026-03 にβ）でカバー外要件に対応不可。

```bash
pnpm add -D oxlint
pnpm oxlint .
```

**出典**: [Biome v2](https://biomejs.dev/blog/biome-v2/), [oxlint 1.0 - VoidZero](https://voidzero.dev/posts/announcing-oxlint-1-stable), [ESLint v10.0.0](https://eslint.org/blog/2026/02/eslint-v10.0.0-released/), [ESLint Migration Guide](https://eslint.org/docs/latest/use/configure/migration-guide)

---

## 8. 型安全な環境変数

### Zod スキーマで起動時に検証

**結論**: `process.env` は runtime に string のみ。Zod でスキーマ定義し、import 時に `parse()` で**起動時に早期失敗**させます。フレームワーク非依存なら `@t3-oss/env-core` + Zod。

```typescript
// src/env.ts（シンプル案）
import { z } from "zod";

const envSchema = z.object({
  DATABASE_URL: z.string().url(),
  PORT: z.coerce.number().default(3000),         // string → number 自動変換
  NODE_ENV: z.enum(["development", "production"]).default("development"),
});

// import 時点で parse → 環境変数不足は起動時に fail
export const env = envSchema.parse(process.env);
```

```bash
pnpm add zod
# フレームワーク非依存の構造化なら
pnpm add @t3-oss/env-core
```

- `z.coerce.number()` で自動変換、IDE 補完、構造化エラー
- `.env` は `.gitignore` 必須、本番 secret は GitHub Secrets 等で管理
- Next.js なら `@t3-oss/env-nextjs`（client/server 自動分離で server secret の client leak 防止）

**出典**: [Create T3: env-variables](https://create.t3.gg/en/usage/env-variables), [t3-oss/t3-env](https://github.com/t3-oss/t3-env)

---

## 9. pre-commit フック（Husky + lint-staged）

### pre-commit と pre-push を分離する

**結論**: pre-commit は**ステージ済みファイルのみ** lint/format で高速化（3秒以内）。重いテスト/ビルドは pre-push へ分離。遅い hook は `--no-verify` 常態化を招き形骸化します。

```bash
pnpm add -D husky lint-staged
pnpm exec husky init
```

```jsonc
// package.json
{
  "lint-staged": {
    "*.{ts,tsx,js,jsx}": ["biome check --write"],
    "*.{json,md}": ["biome format --write"]
  }
}
```

```bash
# .husky/pre-commit — 高速（ステージファイルのみ）
pnpm exec lint-staged
```

```bash
# .husky/pre-push — 低速容認（テスト・ビルド）
pnpm test && pnpm build
```

> **CI では `HUSKY=0`** を明示し二重実行を防ぐ（GitHub Actions の env / Dockerfile の ENV）。

**出典**: [Husky and lint-staged - BetterStack](https://betterstack.com/community/guides/scaling-nodejs/husky-and-lint-staged/)

---

## 10. CI/CD（GitHub Actions）

### 推奨ワークフロー

```yaml
# .github/workflows/ci.yml
name: Node.js CI

on: [push, pull_request]

jobs:
  test:
    runs-on: ${{ matrix.os }}
    strategy:
      matrix:
        node-version: ['22', '24']
        os: [ubuntu-latest, macos-latest, windows-latest]
    env:
      HUSKY: 0                              # CI での hook 二重実行を防止

    steps:
      - uses: actions/checkout@v4

      - uses: pnpm/action-setup@v4
        with:
          version: 10

      - uses: actions/setup-node@v4
        with:
          node-version: ${{ matrix.node-version }}
          cache: 'pnpm'                     # lock file hash で自動キャッシュ

      - run: pnpm install --frozen-lockfile  # 再現性確保
      - run: pnpm biome check .
      - run: pnpm exec tsc --noEmit          # 型チェック
      - run: pnpm vitest run --coverage

      - name: Security audit
        run: pnpm audit --audit-level critical
```

### キャッシュ戦略の要点

- `actions/setup-node@v4` の `cache: 'pnpm'` でゼロコンフィグ・OS別キャッシュ自動分離
- キャッシュキーは lock file hash ベース（branch/timestamp は使わない）
- `--frozen-lockfile`（npm なら `npm ci`）で再現性確保
- モノレポは Turborepo remote cache で分散チーム 42%削減（GitHub Actions cache は 5GB 上限に注意）

**出典**: [GitHub Actions Cache Strategy - EastonDev](https://eastondev.com/blog/en/posts/dev/20260407-github-actions-cache-strategy/)

---

## 11. セキュリティ（依存関係の脆弱性管理）

### 既知CVEと novel malware は別ツールで層化する

**結論**: **単一ツールでは守れません**。CVE系（npm audit / Snyk）と behavioral系（Socket.dev）は検出原理が異なり補完関係です。

| ツール | 検出原理 | 強み / 弱み |
|-------|---------|-----------|
| **npm audit** | GitHub Advisory（既知CVE） | 無料・CI blocking 容易 / 既知CVEのみ・false positive 多 |
| **Snyk** | CVE + reachability | 自動 fix PR / novel malware 苦手・有償 |
| **Socket.dev** | behavioral 解析 | **install前**に typosquatting/supply-chain 検出 / 調整必要 |

```yaml
# 推奨ワークフロー（層化）
1. pnpm audit --audit-level critical   # CI で blocking
2. Snyk scan                            # CVE 精度 + fix PR
3. Socket.dev on package.json change    # novel malware blocking
```

> **なぜ層化が必要か**: 2025年9月の chalk/debug phishing（18パッケージ・週26億DL・maintainer アカウント乗っ取り）は behavioral 変化として Socket は検出可能だが、npm audit は false negative。CVE-first では novel malware を取りこぼします。

**出典**: [Snyk vs Socket - PkgPulse](https://www.pkgpulse.com/guides/npm-vulnerability-management-snyk-socket-2026)

---

## 12. ランタイム選択（Node.js LTS vs Bun vs Deno）

### 結論: デフォルトは Node.js 22.x LTS

| 状況 | ランタイム | 理由 |
|-----|---------|------|
| **Enterprise（fintech/healthcare）** | **Node.js 22.x LTS** | 30ヶ月サポート・npm 100%互換・governance・enterprise 85%トラフィック |
| 高速プロトタイプ/内部ツール | Bun | all-in-one（runtime+pm+bundler+test）・98%互換・deploy簡潔 |
| Edge/Serverless edge | Deno | permissions security・native TS・Web標準 |

- **Node.js LTS**: 22.x は 2024-10〜2027-04。安全なデフォルト。
- **Bun**: 98% Node.js 互換だが、残り 2%（native addon / 特定 network library）が critical な場合あり。LTS ポリシーは非公式。
- **Deno**: permissions-based security（default deny）。AWS Lambda 等で runtime 未標準のため custom layer/container が必要。

**出典**: [Bun vs Node.js vs Deno - daily.dev](https://daily.dev/blog/javascript-runtimes-bun-vs-node-js-vs-deno-comparison/)

---

## 13. ビルド・パッケージング

### バンドラ: tsup（推奨）

esbuild ベースで、TypeScript の CLI/ライブラリを最小設定でバンドル + 型定義生成。

```bash
pnpm add -D tsup
```

```bash
# ESM + 型定義 + ソースマップ
pnpm tsup src/cli.ts src/main.ts --format esm --dts --sourcemap

# 生成物
# dist/cli.js / dist/cli.d.ts / dist/main.js ...
```

| ツール | 用途 |
|-------|------|
| **tsup** | CLI / ライブラリのバンドル（esbuild ベース・最小設定） |
| **tsc** | 型定義のみ生成・素のトランスパイル |
| **Vite** | フロントエンド込みのアプリ |

**出典**: [tsup](https://tsup.egoist.dev/), [esbuild](https://esbuild.github.io/)

---

## 14. 単一実行ファイル配布（オプション）

Python の PyInstaller/Nuitka に相当。Node.js のバイナリ配布手段：

| 手段 | アプローチ | 備考 |
|------|-----------|------|
| **Node.js SEA** | `--experimental-sea-config` | Node.js 公式の Single Executable Applications（実験的） |
| **Bun compile** | `bun build --compile` | 最も簡単・高速・Bun ランタイム同梱 |
| **pkg（vercel/pkg）** | バンドル | メンテナンス縮小傾向、新規は非推奨 |

```bash
# Bun（最も簡単）
bun build src/cli.ts --compile --outfile photo-manager

# Node.js SEA
node --experimental-sea-config sea-config.json
```

**出典**: [Single Executable Applications - Node.js Docs](https://nodejs.org/api/single-executable-applications.html), [bun build --compile](https://bun.sh/docs/bundler/executables)

---

## 15. ドキュメント生成（オプション）

### TypeDoc（API リファレンス中心）

TSDoc コメントから型情報込みの API ドキュメントを生成。

```bash
pnpm add -D typedoc
pnpm typedoc src/index.ts
```

チュートリアル中心なら Python 版と同じく **VitePress** や **Docusaurus** が選択肢。

**出典**: [TypeDoc](https://typedoc.org/), [VitePress](https://vitepress.dev/)

---

## 16. 推奨構成まとめ（シンプルさ優先）

### パッケージ・バージョン管理
- **パッケージマネージャー**: pnpm（corepack で版固定）
- **Node.js バージョン**: fnm / Volta / mise + `.nvmrc` + `engines`（この dotfiles は mise）
- **ランタイム**: Node.js 22.x LTS（プロトタイプは Bun、edge は Deno）

### 言語・UI
- **言語**: TypeScript（strict + noUncheckedIndexedAccess、ESM）
- **CLI**: Commander（シンプル）/ oclif（大規模）
- **Web UI**: Fastify or Hono + HTMX + Alpine.js（ビルド不要・テスト容易）

### 開発ツール
- **テスト**: Vitest（単体・E2E は Playwright）
- **リント+フォーマット**: Biome（新規）/ oxlint（速度）/ ESLint v9 flat config（プラグイン必須）
- **環境変数**: Zod で起動時検証
- **フック**: Husky + lint-staged（pre-commit/pre-push 分離）

### ビルド・配布
- **バンドラ**: tsup
- **単一実行ファイル（オプション）**: Bun compile（簡単）/ Node.js SEA（公式）

### セキュリティ
- **層化**: pnpm audit + Snyk + Socket.dev

### クイックスタート

```bash
# プロジェクト作成
mkdir photo-manager && cd photo-manager
corepack enable pnpm
pnpm init

# Node.js バージョン固定
echo "22.14.0" > .nvmrc

# 依存関係追加
pnpm add fastify zod commander
pnpm add -D typescript tsx tsup vitest @biomejs/biome @types/node husky lint-staged

# 初期化
pnpm biome init
pnpm exec husky init

# 開発サーバー起動
pnpm dev

# テスト実行
pnpm test
```

---

## 未解決事項（確信度が低い項目）

- **corepack の長期存続**: 「Node.js v25 で削除予定」という二次ソース情報あり。一次ソース未確認。運用基盤に据える前に nodejs/corepack の最新 issue を確認すべき。
- **ベンチマーク数値の再現性**: 「Jest比5-28倍」「CI 42%削減」等はブログ計測値で計測条件がソースごとに異なる。オーダー（桁）は信頼できるが自環境では実測推奨。
- **oxlint プラグイン GA 時期**: β/α が 2026年初とされるが正式版時期は流動的。プラグイン必須要件があるなら採用は時期尚早の可能性。
- **UI フレームワーク章（第5章）**: 本調査のスコープ外であり、公式ドキュメントを出典とした一般的推奨。プロジェクト要件で再検証すること。

---

## 参考資料

- [pnpm benchmarks](https://pnpm.io/benchmarks)
- [Node.js Documentation](https://nodejs.org/docs/latest/api/)
- [Corepack - Node.js Docs](https://nodejs.org/api/corepack.html)
- [TypeScript: TSConfig Reference](https://www.typescriptlang.org/tsconfig/)
- [Biome](https://biomejs.dev/)
- [oxlint - VoidZero](https://voidzero.dev/posts/announcing-oxlint-1-stable)
- [ESLint v10 / Migration Guide](https://eslint.org/docs/latest/use/configure/migration-guide)
- [Vitest](https://vitest.dev/)
- [t3-oss/t3-env](https://github.com/t3-oss/t3-env)
- [Husky](https://typicode.github.io/husky/)
- [npm Vulnerability Management - PkgPulse](https://www.pkgpulse.com/guides/npm-vulnerability-management-snyk-socket-2026)
- [JavaScript Runtimes - daily.dev](https://daily.dev/blog/javascript-runtimes-bun-vs-node-js-vs-deno-comparison/)
- [tsup](https://tsup.egoist.dev/)
- [TypeDoc](https://typedoc.org/)

> 詳細な調査ログ（49件の出典・トレードオフ分析）は [`.ai/research/2026-06-08-154203-nodejs-dev-environment-best-practices.md`](../.ai/research/2026-06-08-154203-nodejs-dev-environment-best-practices.md) を参照。
