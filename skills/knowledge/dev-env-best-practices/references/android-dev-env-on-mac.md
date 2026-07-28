---
作成日: 2026-06-11
調査目的: Mac (主に Apple Silicon) 上で Android アプリを開発する際の環境構築ベストプラクティスを、公式推奨を軸に体系化する
調査モード: ベストプラクティス
ステータス: Completed
references: []
---

## 調査概要

**調査範囲:**

- Android Studio のインストール / バージョン管理（DMG vs Homebrew Cask）
- JDK 戦略（bundled JBR vs 外部 JDK、複数バージョン管理）
- Android SDK / 環境変数の管理
- Gradle 設定（JAVA_HOME 一貫性、Wrapper、キャッシュ）
- エミュレータ (AVD) の Apple Silicon 最適化
- Kotlin Multiplatform / Compose Multiplatform の要件
- macOS 固有設定（ファイルディスクリプタ上限など）と CI/CD ランナー選択

**調査観点:**

| 観点 | なぜ重要か |
|------|----------|
| 公式推奨 vs コミュニティ慣習 | Android 周辺は公式が頻繁に方針転換する（bundled JDK 化、x86 廃止）。公式に従わないと将来の互換性を失う |
| Apple Silicon ネイティブ性 | ARM ネイティブか Rosetta 2 経由かで体感速度が桁違い。ツール選定の最優先軸 |
| 一貫性（JDK パス） | Android Studio / ターミナル / Gradle が別々の JDK を見ると無言でビルドが壊れる。最頻出のハマりどころ |
| 廃止済み手順の回避 | 古い記事の `android-sdk` cask や `STUDIO_JDK` を踏むと環境が壊れる |

> **前提:** 本レポートは Apple Silicon (M1〜M4)・macOS Ventura 以降を主対象とする。Intel Mac / 物理デバイス実機テストは別途検討が必要。

## 定石

### 定石 1: Android Studio は DMG が公式推奨、Homebrew Cask も可

- **公式推奨**: developer.android.com から DMG を取得し Applications にドラッグ。初回起動でセットアップウィザードが走る。
- **Homebrew**: `brew install --cask android-studio` も有効。`/opt/homebrew/bin/studio` にリンクされ `studio` コマンドで起動可能。アップデートを brew で一元管理したい場合に有利。
- **Apple Silicon**: M シリーズでは必ず **ARM64 ビルド** を選ぶ。Homebrew Cask は arm64 を自動解決する。Intel ビルドを Rosetta 2 で動かすと体感速度が落ちる。
- **適用条件**: チームでバージョンを固定したい / 手動管理を好むなら DMG。dotfiles で宣言的に管理したいなら Cask。どちらも公式に成立する選択。
  - 出典: [Install Android Studio](https://developer.android.com/studio/install) (2026-04) / [Homebrew Formulae: android-studio](https://formulae.brew.sh/cask/android-studio)

### 定石 2: JDK は bundled JetBrains Runtime (JBR) を使い、`STUDIO_JDK` を設定しない

- **公式推奨**: Android Studio Arctic Fox (2020.3.1) 以降は JBR を同梱。追加インストール不要で、Google が Studio をテストしたのと同じ JDK で動作保証される。
- **アンチパターン**: `STUDIO_JDK` 環境変数を設定すると外部 JDK が強制される。設定しないこと。
- **bundled JDK パス** (macOS Catalina 以降):
  ```
  /Applications/Android Studio.app/Contents/jbr/Contents/Home
  ```
  （古い資料では `Contents/jre/...` 表記。Studio のバージョンで `jre` / `jbr` が異なるため、実際のパスを確認すること → 未解決事項参照）
- **外部 JDK (Eclipse Temurin 等) を使う場合**: `JAVA_HOME` と Studio 設定の JDK を**必ず同一に**する。不一致は Gradle ビルドエラーの典型原因。
- **適用条件**: 通常の Android 開発は bundled JBR で十分。外部 JDK が要るのは、CI と完全に同じ JDK ベンダを揃える等の特殊要件のみ。
  - 出典: [Android Studio 設定](https://developer.android.com/studio/intro/studio-config) / [Java versions in Android builds](https://developer.android.com/build/jdks)

### 定石 3: ターミナル用の JDK は複数バージョン管理ツールで切り替える

- Studio 内は JBR で完結するが、ターミナルから Gradle/`sdkmanager` を叩くなら別途 JDK が要る。
- **ツール選定**:

  | ツール | 役割 | シェル起動コスト | 向くケース |
  |---|---|---|---|
  | **mise** | 多言語版マネージャ (Java/Ruby/Python…) | ~10ms | モダン環境・KMP で Ruby も要る場合の統一管理 |
  | asdf | 多言語・プラグイン式 | 50-150ms | 既存 asdf エコシステムがある場合 |
  | jEnv | Java 専用・切替のみ | ~50ms | Homebrew で入れた JDK の切替だけしたい場合 |
  | SDKMAN | Java 専用・インストール機能付き | 30-300ms | スタンドアロンな Java 環境 |

- **推奨**: 新規なら **mise**（速度・多言語対応）。Java バージョンは用途で選ぶ（後述の定石7）。
- **適用条件**: 単一プロジェクト・Studio 完結なら管理ツールは不要。複数プロジェクトで JDK バージョンが分かれる / KMP で Ruby(CocoaPods) も管理したい場合に効く。
  - 出典: [Java Version Managers · Mac Install Guide 2026](https://mac.install.guide/java/version-managers) / [Java with Mise](https://mac.install.guide/mise/mise-java-mac)

### 定石 4: Android SDK は `~/Library/Android/sdk` を `ANDROID_HOME` と `ANDROID_SDK_ROOT` 両方で指す

- **デフォルトパス**: `~/Library/Android/sdk`
- **`.zprofile` への設定**（macOS デフォルトシェルは zsh）:
  ```bash
  export ANDROID_HOME=$HOME/Library/Android/sdk
  export ANDROID_SDK_ROOT=$HOME/Library/Android/sdk   # 同じ値を指す
  export PATH=$PATH:$ANDROID_HOME/platform-tools:$ANDROID_HOME/cmdline-tools/latest/bin
  ```
- **両方設定する理由**: 古いツールは `ANDROID_HOME`、新しいツールは `ANDROID_SDK_ROOT` を参照する。別名関係なので同一ディレクトリを指す。
- **廃止注意**: 旧 `tools/bin` パスは非推奨。現行は `cmdline-tools/latest/bin`。古い記事の `$ANDROID_HOME/tools` は読み替えること。
- **SDK 管理**: GUI は Studio > Tools > SDK Manager。CLI は `sdkmanager --list` / `sdkmanager "platforms;android-35"`。
  - 出典: [Environment variables](https://developer.android.com/tools/variables) / [Android SDK Manager](https://developer.android.com/tools/sdkmanager)

### 定石 5: Homebrew の SDK 管理は `android-commandlinetools` に統一済み（`android-sdk` cask は廃止）

- **廃止**: 旧 `android-sdk` cask は 2024 年末に Homebrew から削除。`android-commandlinetools` へ移行が必須。
- **Studio 不要のヘッドレス/CI 環境向け**:
  ```bash
  brew install --cask android-commandlinetools
  # 配置先: /opt/homebrew/share/android-commandlinetools (Apple Silicon)。Java 依存あり
  ```
- **ADB/fastboot のみ欲しい場合**:
  ```bash
  brew install --cask android-platform-tools
  adb devices
  ```
- **macOS の利点**: Windows と違い USB ドライバ不要でプラグ&プレイ。`adb devices` でデバイスが見えれば接続成立。
- **適用条件**: GUI 込みの開発機なら Studio 同梱の SDK Manager で足りる。CI / ヘッドレスビルド機で commandlinetools を使う。
  - 出典: [Homebrew Formulae: android-commandlinetools](https://formulae.brew.sh/cask/android-commandlinetools)

### 定石 6: Gradle は「JDK の一貫性」が最優先。Wrapper は `-bin`、キャッシュは削除可

- **JDK 決定の優先順位**（ここが最頻出ハマり所）:
  1. Studio UI から Gradle 実行 → **Studio 設定の Gradle JDK**
  2. 素のターミナルから実行 → **`JAVA_HOME`**（未設定なら PATH の `java`）
  3. Studio 内蔵ターミナルから実行 → IDE の JDK 設定（`JAVA_HOME` は無視）
  - → 3 経路が別 JDK を見ると無言で壊れる。`JAVA_HOME` と Studio の Gradle JDK 設定を揃える。
- **Gradle Wrapper**: `-bin` 分布を使う（公式・CI 推奨、~130MB）。`-all` はソース/docs 同梱で IDE 補完が良くなるが開発機限定。
  ```properties
  distributionUrl=https\://services.gradle.org/distributions/gradle-X.Y-bin.zip
  ```
- **キャッシュ**: `~/.gradle/caches` は**安全に削除可能**。再ビルド時に依存が再 DL される（初回だけ遅い）。Gradle 自身も 7〜30 日で再取得可能ファイルを自動掃除。
- **エンコーディング**: プロジェクト全体を UTF-8 に統一（プラットフォーム依存エンコーディング差によるキャッシュ問題回避）。
  - 出典: [Gradle Wrapper](https://docs.gradle.org/current/userguide/gradle_wrapper.html) / [Build Cache](https://docs.gradle.org/current/userguide/build_cache.html)

### 定石 7: エミュレータは ARM64 system image + Quick Boot で 10 倍速くなる

- **System Image**: Apple Silicon では必ず **`arm64-v8a`** を選ぶ。x86 image は Rosetta 2 経由で著しく遅い。API 35 (Android 15) が現行推奨。
- **ハードウェアアクセラレーション**: ARM 上では macOS Hypervisor Framework + MoltenVK (Vulkan) が自動で効く。Intel 時代の HAXM は不要。
- **Quick Boot / Snapshot**: 初回起動後にスナップショットを作ると次回以降 Cold Boot 比で最大 10 倍速。
  - M4 Mac 16GB 実績: Cold Boot ~22-30秒 → Quick Boot ~2-3秒。
  - **使い分け**: 開発は Quick Boot、CI は Cold Boot（スナップショットが隠れた状態不整合バグを招くため）。
- **リソース割当**: CPU 2〜4 コア / RAM 4〜6GB から開始。複数 AVD 並行なら本体 16GB 以上を確保。
  - 出典: [Configure hardware acceleration](https://developer.android.com/studio/run/emulator-acceleration) / [Snapshots](https://developer.android.com/studio/run/emulator-snapshots)

### 定石 8: KMP / Compose Multiplatform は最低 Java 11、iOS ターゲットには Xcode 必須

- **JDK 要件**: Gradle JVM 最低 Java 11、推奨 **JetBrains Runtime 17 以上**。Desktop (Compose) ターゲットは Skia バインディングのメモリ管理上 JDK 11+ 必須。
- **IDE 要件**: IntelliJ IDEA 2025.2.2 以上 または Android Studio Otter (2025.2.1) 以上 + Kotlin Multiplatform プラグイン。
- **iOS ターゲット (Mac 必須要件)**:
  - Xcode 最新版（iOS Simulator は Apple 要件で macOS のみ実行可）
  - `xcode-select --install` でコマンドラインツール導入後、Xcode 本体を一度起動して toolchain を確定
  - 依存管理に CocoaPods（または SPM）
- **環境検証**: **KDoctor** で KMP 環境の構成不備をチェックできる。
- **適用条件**: Android 単体なら Xcode/CocoaPods は不要。iOS 共有が要る瞬間に macOS + Xcode が必須化する（これが「Android 開発に Mac を選ぶ」最大の動機）。
  - 出典: [Kotlin Multiplatform quickstart](https://kotlinlang.org/docs/multiplatform/quickstart.html) / [KMP Roadmap Aug 2025](https://blog.jetbrains.com/kotlin/2025/08/kmp-roadmap-aug-2025/)

### 定石 9: macOS 固有のファイルディスクリプタ上限に注意

- 大規模プロジェクトで Studio / VSCode が多数のファイルを監視すると、macOS のファイルディスクリプタ上限 (`ulimit -n`、デフォルト 256 または 10,240) に当たることがある。
- 一時対処: `ulimit -n 20000`。永続化は `/Library/LaunchDaemons/limit.maxfiles.plist`（Sonoma 以降は完全解は未確立）。
- **注意**: `.zshrc`/`.zprofile` に低い `ulimit` 記述があると後続の引き上げが失敗する。
- **適用条件**: 中小規模では遭遇しにくい。モノレポ / 多モジュール構成で「Too many open files」が出たら対処する。
  - 出典: [Increasing File Descriptor Ulimit on macOS - Hiltmon](https://hiltmon.com/blog/2023/01/01/increasing-file-descriptor-ulimit-on-macos/)

### 定石 10: CI は Android 単体なら Linux ランナー、iOS 共有時のみ macOS ランナー

- **GitHub Actions Linux ランナー（第一選択）**: 2024 年 4 月から KVM ハードウェアアクセラレーション対応。`ubuntu-latest` は macOS ランナー比 2〜3 倍高速・低コスト。`reactivecircus/android-emulator-runner` と併用。
- **macOS ランナー**: 必要になるのは iOS ビルド/テストを同居させる場合。遅く高価なので Android 単体では避ける。
- **Bitrise Build Hub（新オプション）**: 既存 GitHub Actions ワークフローを Bitrise マネージド macOS(M4 Pro)/Linux で実行。iOS+Android 並行開発でキャッシュ集約・ゼロメンテを狙う場合に検討。
- **適用条件**: Android のみ → Linux + KVM。KMP で iOS も回す → macOS ランナー or Bitrise。
  - 出典: [GitHub Actions: Hardware accelerated Android virtualization](https://github.blog/changelog/2024-04-02-github-actions-hardware-accelerated-android-virtualization-now-available/)

## 判断材料

| 状況 / 条件 | 推奨・結論 | 理由 |
|-------------|-----------|------|
| 新規に Mac で Android 環境を組む | Studio (DMG or Cask) + bundled JBR + ARM64 emulator | 公式が最もテストした構成。Apple Silicon ネイティブで最速 |
| dotfiles で宣言的管理したい | Cask (android-studio / commandlinetools / platform-tools) + mise で JDK | brew + mise で再現可能・宣言的。本リポジトリの方針と整合 |
| ターミナルで複数プロジェクトの JDK が分かれる | mise で per-project `.tool-versions` | 起動コスト最小・多言語対応。KMP の Ruby も同居管理できる |
| ビルドが無言で壊れる | `JAVA_HOME` と Studio の Gradle JDK 設定の一致を最初に疑う | JDK 不一致が最頻出原因。3 実行経路が別 JDK を見る |
| エミュレータが遅い | arm64-v8a image + Quick Boot snapshot | x86 image の Rosetta 2 経由が遅さの主因 |
| iOS も共有する (KMP) | macOS + Xcode + CocoaPods + KDoctor 検証 | iOS Simulator は macOS 専有。これが Mac を選ぶ最大の理由 |
| CI を組む | Android 単体は Linux+KVM、iOS 同居は macOS/Bitrise | Linux が 2-3 倍速・安価。macOS は iOS 必須時のみ |

**長期的含意:** Android ツールチェーンは Apple Silicon ネイティブ化（emulator ARM 化、Rosetta 2 依存の解消）と bundled JDK 化の方向で安定してきており、「公式同梱物をそのまま使い、外部で固定するのは JDK バージョンとブランドだけ」という構成が今後 2-3 年の保守コストを最小化する。古い記事の `android-sdk` cask / `tools/bin` パス / `STUDIO_JDK` を踏まないことが、最も費用対効果の高い予防策。

## 参考資料

- [Install Android Studio](https://developer.android.com/studio/install) (2026-04) — インストール公式手順
- [Android Studio リリースノート](https://developer.android.com/studio/releases) (2026-04) — 最新版・リリース日
- [Android Studio 設定 (studio-config)](https://developer.android.com/studio/intro/studio-config) (2026) — bundled JDK / STUDIO_JDK
- [Java versions in Android builds](https://developer.android.com/build/jdks) (2026) — JDK 互換性
- [Environment variables](https://developer.android.com/tools/variables) (2026) — ANDROID_HOME / ANDROID_SDK_ROOT
- [Android SDK Manager (sdkmanager)](https://developer.android.com/tools/sdkmanager) (2026) — CLI ドキュメント
- [Configure hardware acceleration for the Android Emulator](https://developer.android.com/studio/run/emulator-acceleration) (2026) — エミュレータ最適化
- [Snapshots | Android Studio](https://developer.android.com/studio/run/emulator-snapshots) (2026) — Quick Boot / Snapshot
- [Gradle Wrapper](https://docs.gradle.org/current/userguide/gradle_wrapper.html) (2026) — Wrapper 公式
- [Gradle Build Cache](https://docs.gradle.org/current/userguide/build_cache.html) (2026) — キャッシュ管理
- [Kotlin Multiplatform quickstart](https://kotlinlang.org/docs/multiplatform/quickstart.html) (2026) — KMP 要件
- [KMP Roadmap Aug 2025](https://blog.jetbrains.com/kotlin/2025/08/kmp-roadmap-aug-2025/) (2025-08) — 最新ロードマップ
- [Homebrew Formulae: android-studio](https://formulae.brew.sh/cask/android-studio) — Cask
- [Homebrew Formulae: android-commandlinetools](https://formulae.brew.sh/cask/android-commandlinetools) — SDK cask 統一先
- [Java Version Managers · Mac Install Guide 2026](https://mac.install.guide/java/version-managers) (2026) — mise/asdf/jEnv/SDKMAN 比較
- [Java with Mise · Mac Install Guide 2026](https://mac.install.guide/mise/mise-java-mac) (2026) — mise で JDK 管理
- [GitHub Actions: Hardware accelerated Android virtualization](https://github.blog/changelog/2024-04-02-github-actions-hardware-accelerated-android-virtualization-now-available/) (2024-04) — Linux KVM 加速
- [Ship iOS and Android builds twice as fast on GitHub Actions - Bitrise](https://bitrise.io/blog/post/ship-ios-and-android-builds-twice-as-fast-on-github-actions) — Bitrise Build Hub
- [Increasing File Descriptor Ulimit on macOS - Hiltmon](https://hiltmon.com/blog/2023/01/01/increasing-file-descriptor-ulimit-on-macos/) (2023-01) — ulimit 永続化
- [Android Development on MacBook Silicon: 2026 Baseline](https://www.buildwithmatija.com/blog/android-development-macbook-silicon-2026-baseline) (2026) — Apple Silicon 開発基準

## 未解決事項

- [ ] **bundled JDK パスが `jre` か `jbr` か** — 2 ソースで `Contents/jre/...` と `Contents/jbr/...` が混在。Studio のバージョンで異なるため、実機で `ls /Applications/Android\ Studio.app/Contents/` を確認するのが確実。`JAVA_HOME` 設定時の正誤に直結するので影響度: 中。
- [ ] **ターミナル用 JDK の推奨メジャーバージョン** — KMP 公式は「JBR 17 以上」、別ソースは「JDK 21/25 LTS」と幅がある。Android Gradle Plugin の要求 JDK はバージョン依存のため、使う AGP のリリースノートで最終確認すべき。影響度: 中（不一致でビルド不可になりうる）。
- [ ] **未来日付バージョンの確証** — Android Studio Quail 1 (2026.1.1, 2026-04-28) / JDK 25 LTS (2025-09) 等は調査時点 (2026-06-11) で時系列整合はするが、二次情報由来で一次リリースノートの突き合わせが未完。正確なバージョン番号が要る場合は公式リリースノートで再確認。影響度: 低〜中。
- [ ] **本 dotfiles への組み込み可否** — 本リポジトリは chezmoi 管理。Android ツール群 (Cask 3 種 + mise の Java) を Brewfile / mise 設定にどう落とすかは未調査。実装時は `package-management` skill を参照。影響度: 低（調査スコープ外）。
