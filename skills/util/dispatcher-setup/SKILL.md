---
name: dispatcher-setup
disable-model-invocation: true
description: >-
  対象 repo で dispatcher を回すための常駐環境 (宣言 config / claim label / gh 認証 / crontab) の
  充足を検査し、副作用の無い試運転を撃ち、不足だけを理由付きで提案して承認分を適用する doctor。
---

# dispatcher 初期設定

対象 repo で dispatcher (issue から CL までの自律オーケストレーション) を回す環境の充足を検査し、**不足だけを理由付きで提案する** doctor。再実行 = 再検査で、既存 config は差分提示 + 対話 merge にする (黙って上書きしない)。

**正本の分担**: 本 skill は **dispatcher の常駐環境 (cwd 外)** の充足を見る。cwd の repo 自体の充足 (settings / CONTRIBUTING.md / worktree 設定) は `/swat-skills:setup` が正本で、本 skill はそちらから案内されて起動する。

**idempotency の定義**: 整合済みの環境に再実行したとき、**提案がゼロになること**。

## スコープ (改変不可の境界)

| 対象 | 扱い |
|---|---|
| `~/.claude/dispatcher/<project>/dispatcher-project.toml` | 書き先 (dispatcher は置き場の設計上 cwd 外に宣言 config を持つ) |
| ユーザーの crontab | 手順 3 の cron 相当の試運転が通り、**ユーザーが承認したときだけ** tick 行を足す。同じ project の tick 行が既にあって食い違うなら差分を示して merge を尋ね、黙って置き換えない |
| gh の token file (`[auth].token_file`) | **有無と mode だけを見る。中身は読まない**。無ければ作り方を提示してユーザーに作ってもらう (token の発行と保存はユーザーの秘密の扱い) |
| 置き場 repo の label | **読むだけ**。不足は `gh label create` の行を提示してユーザーに実行してもらう (他 repo への副作用は doctor の改変範囲を超える) |
| cwd の repo | **読むだけ** (origin と作業ツリーの path を検査に使う)。settings を含め書き換えは `/swat-skills:setup` の担当 |

## 手順

導入と運用の正本 `${CLAUDE_SKILL_DIR}/../dispatcher/README.md` を Read してから検査に入る (手順・各 key の既定値・綴り・トラブル時の辿り方はすべて同書が正本。本書は重ねる doctor 固有の分だけを持つ)。対応 tracker は gh だけなので、置き場 repo に `gh repo view` が通らなければ停止し、理由を報告する。

検査と提示に使う 2 つの名前は、表に入る前に確定させる。

- `<project>`: 宣言 config の置き場を決める名前。既定は cwd の repo 名
- 置き場 repo: 既存 config があればその `[issue].repo`、無ければ cwd の origin の `owner/name`

### 1. 充足検査

下表を全件見てから提案に入る。

| 検査 | 見るもの | 不足のとき |
|---|---|---|
| 宣言 config | `~/.claude/dispatcher/<project>/dispatcher-project.toml` が在るか | 手順 2 で生成する。中身の綴りは doctor では判定しない (手順 3 の exit 2 が名指しで落とす) |
| claim label | `gh label list -R <置き場 repo>` に README 導入手順 2 の label 2 つが在るか | 足りない方の `gh label create` の行を提示する |
| uv の解決 | `command -v uv` (tick script の shebang が PATH で引く) | uv の導入コマンドを提示し、解決するまで手順 3 に進まない |
| gh の認証の置き場 | config に `[auth].token_file` があれば、その file が在り group / other から読めない mode か (`stat` で見る。中身は読まない)。無ければ、`gh auth status` の行に `(keyring)` が出て、かつ cron の tick が認証で通った実績が無いか (crontab 行がまだ無い初回導入か、「tick の生存」で見る log.jsonl 最終行が `result: auth_error`) | README「運用 > 死活確認」の gh 認証の項の作り方を提示する。keyring を提案に含めるのは、手順 3 の試運転が keyring の失敗を見抜けないため (README 導入手順 4)。tick が回っていれば keyring のままで充足 |
| crontab | `crontab -l` に、この project の tick 行 (`dispatcher-tick.py <project>` を含む行) が README 導入手順 5 の書式で在るか (`cd` 先は対象 repo の作業ツリーで、worktree ではない) | 手順 4 で登録する |
| tick の生存 | README「運用 > 死活確認」の判定を当てる | 同節の辿り方を添えて報告する。crontab 行が無い初回導入ではこの検査を飛ばし、手順 3 の試運転の成否で代える |
| sandbox 書込 | settings に `sandbox` block があるなら、その `filesystem.allowWrite` に `~/.claude/dispatcher` が在るか | 書き込みは本 skill の範囲外。`/swat-skills:setup` の settings step で足すようユーザーに提案する (sandbox 下では config も log も書けない) |

### 2. 宣言 config の生成

`~/.claude/dispatcher/<project>/` を作ってから書く — この dir が無いと log も cron.log も置き場が無く、失敗が stderr にしか残らない。README 導入手順 1 の TOML の各 key を対話で確定する。既定を置けるものは同所のコメントどおりの既定を示して確認だけ取る。既存 config があるなら差分提示 + 対話 merge にする。

### 3. 試運転 (doctor が撃つ)

対象 repo の作業ツリーを cwd にして、README 導入手順 3・4 の試運転を doctor 自身が順に撃つ。どちらも**次の literal な形のまま撃つ** — path は tilde のまま変数にせず、`env …` / `VAR=x` も前置しない (崩すと sandbox 内で gh が落ちる偽の失敗になる — README 導入手順 3)。

```
cd <対象 repo の作業ツリー> && ~/.claude/skills/swat-skills/skills/util/dispatcher/scripts/dispatcher-tick.py --dry-run <project>
```

```
cd <対象 repo の作業ツリー> && ~/.claude/skills/swat-skills/skills/util/dispatcher/scripts/dispatcher-tick.py --dry-run --cron-env <project>
```

試運転は claude を起動せず、`~/.claude/dispatcher/<project>/` 配下に何も書かないので、承認を取らずに撃ってよい。通れば exit 0 で stdout に `"result": "ok"` の JSON 1 行が出る — `instructions` の件数も報告に載せる (本番の tick なら orchestrator が起動する件数)。

失敗は exit code で読み分け、stderr の末尾 1 行が名指しするものを直して撃ち直す。

- exit 4 (`result=auth_error`) は gh の認証。1 本目で出たら gh 自体が未認証なので `gh auth login` を案内する。2 本目だけで出たら gh の認証が親 shell の環境変数にしか無い — 手順 1 の「gh の認証の置き場」と同じ作り方を提示し、ユーザーが file を置いたら config に `[auth]` を足す (手順 2 の対話 merge)。`[auth].token_file` を既に置いていて出るなら token が失効しているか権限が足りないので、README「運用 > 死活確認」の gh 認証の項の権限で再発行して同じ file へ置き直すよう提示する
- exit 2 は宣言 config の綴り (exit 2 が綴りを判定する唯一の面)。名指しされた key / repo / label / token file を直す
- exit 1 (`result=error`) は名指しされた依存か観測の失敗。`uv` が無いなら手順 1 の uv の解決に戻り、`claude` / `gh` が PATH に無いならその導入を提示し、観測の失敗なら 1 行をそのまま報告に載せる

両方が通るまで手順 4 に進まない。

### 4. crontab への登録

README 導入手順 5 の書式で tick 行を組み立てる (`<uv の dir>` は `dirname "$(command -v uv)"`、`cd` 先は手順 3 の作業ツリー)。`crontab -l` の出力と突き合わせる — `no crontab for` で exit 1 なら空の表として扱う。

- 同じ行が在る: 充足 (提案しない)
- この project の tick 行が在って食い違う: 行を merge する。field (周期 / `cd` 先 / `PATH=<uv の dir>` / cron.log の出力先) ごとに現行の値と組み立てた値を並べ、食い違う field ごとにどちらを採るかを尋ねて 1 行に組み直す。尋ねずに決めた field は現行の値を残す (利用者が手で変えた周期などを黙って上書きしない)
- 無い: 足してよいかを尋ねる

承認されたら、現行の表 + 変更後の行を一時 file に書き、`crontab <その file>` で入れる (`crontab` は表を丸ごと置き換えるので、現行の行を落とさない)。入れた後に `crontab -l` で行が在ることを確かめる。次のいずれかなら書き込まずに行の提示に倒し、ユーザーに `! crontab -e` で足してもらう: `command -v crontab` が無い / cron daemon が撃てると確かめられない (macOS は `launchctl print system/com.vix.cron` が service を返すか、Linux は `pgrep -x cron` か `pgrep -x crond` が返すかで見る。macOS の cron は表が入ってから起動する on-demand なので、`state = not running` は撃てない理由にしない) / 書き込みが拒まれた。

登録後は README 導入手順 6 のとおり、周期の 2 倍待って log.jsonl 最終行の `ts` が進むかで確かめるよう報告に書く (試運転が見抜けない keyring の失敗はここで表れる)。

### 5. 完了報告

全項目を表で報告する: 項目 / 結果 (**充足** = 検査を素通り / **適用** = 提案が承認され書き込んだ / **提案のみ** = 承認待ちや見送り / **未完了** = 停止・保留) / 次アクション。ユーザーに実行を委ねた label 作成・token file の作成・crontab 登録 (提示に倒した場合) は、セッション内で実行されなければ未完了として必ず載せる。
