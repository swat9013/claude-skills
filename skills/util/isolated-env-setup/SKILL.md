---
name: isolated-env-setup
description: AI coding agent に作業させる隔離環境 (Docker Sandboxes の microVM。repo を複数持ち込める) を組む構築スクリプトを、雛形に値を埋めて人が決めた置き場に書き、走らせて検証を通す。
argument-hint: "[持ち込む repo の path か URL]"
disable-model-invocation: true
---

# 隔離環境を組む

AI coding agent に作業させる隔離環境を、Docker Sandboxes (`sbx`) の microVM として組む。出すのは、人が決めた置き場に残り、skill なしで再実行できる構築スクリプト 1 本。スクリプトは雛形 `${CLAUDE_SKILL_DIR}/scripts/build-isolated-env.template.sh` に値を埋めて作る — 作成・削除の順序の制約と検証は雛形に固定してあり、スクリプト全体を書き起こすと順序を書き間違える。

`sbx` の挙動と、道具ごとに要る egress の宛先は [references/sbx-measured.md](references/sbx-measured.md) にある (以下、参照知識)。値を決める前に Read する。

## 1. 道具を確かめる

`command -v sbx` で有無を見る。無ければ、Docker Sandboxes の導入 (https://docs.docker.com/ai/sandboxes/) を案内して止まる。`sbx` なしで隔離環境を組む経路は無い。

あれば `sbx version` で版を見る。参照知識の実測した版の範囲の外なら、未実測の版であることを人に伝えて先へ進む。合否は手順 4 の検証で決める。

## 2. 値を聞く

次をまとめて聞く。聞く前に、相手が書いた依頼から埋まる値は埋めておき、確かめるだけにする。

| 値 | 雛形の置き場 | 決め方 |
|---|---|---|
| 隔離環境の名前 | `NAME` | `sbx ls` に既にある名前は使わない。既にある環境を作り直したいと言われても、消すのは人に任せる — `sbx rm` は中の未 push の作業ごと消し、戻せない |
| 持ち込む repo (複数可) | `REPOS` | host の repo の path か clone URL。host で clone し直してから流し込むので、未 commit の変更は入らないと伝える。path で渡す repo には `origin` が要る (VM の中ではその URL を HTTPS 形式にして push 先に使う) |
| 必要な道具 (mise・言語の runtime など) | `vm_setup_script` | Docker は VM に最初から入っている。導入のコマンドは参照知識の「道具の導入の形」を土台にする |
| 許可する egress 先 | `ALLOW_HOSTS` / `ALLOW_PROBE_URL` | 1 つ以上置く。道具の導入に要る宛先は参照知識の表から足す。雛形は検証の container の pull のために Docker Hub の宛先 (`IMAGE_PULL_HOSTS`) も必ず許可するので、そのことを伝えてから決めてもらう。`ALLOW_PROBE_URL` には、許可した宛先のうち検証で届くことを確かめる URL を 1 つ置く |
| 必要な secret の名前 | `SECRETS` | `<sbx の service 名>=<値を持つ host の環境変数の名前>`。service 名は `sbx secret set --help` の一覧から選ぶ。値は聞かず、書かず、会話にも出させない。要らなければ空の配列にする |
| 構築スクリプトの置き場 | (書き出し先) | 人が決める。既に file があれば上書きせずに聞き直す |

## 3. 構築スクリプトを書く

雛形を置き場へ写す。既に file があれば写さずに失敗するので、人に置き場を聞き直す。

```bash
[ ! -e <置き場> ] && cp "${CLAUDE_SKILL_DIR}/scripts/build-isolated-env.template.sh" <置き場> && chmod +x <置き場>
```

写した file の `===== 構築の値 =====` の節だけを Edit で埋める (`__NAME__` などの印と `:  # SETUP_COMMANDS` の行を置き換える)。節の外は書き換えない — 順序の制約と検証はそこにある。埋め終えたら、節の外が雛形と同じであることを確かめる (出力が空なら同じ):

```bash
diff <(sed '/===== 構築の値 =====/,/===== 構築の値ここまで =====/d' "${CLAUDE_SKILL_DIR}/scripts/build-isolated-env.template.sh") <(sed '/===== 構築の値 =====/,/===== 構築の値ここまで =====/d' <置き場>)
```

secret の値と、host の作業ツリーを mount する指定は書かない — file は人が決めた置き場に残って読まれ、mount すると中の agent が host の file (未追跡の `.env` を含む) を読み書きできる。

## 4. 走らせて検証を確かめる

スクリプトを走らせる。作成と image の pull で数分かかるので、最後の `PASS` / `FAIL` の行まで受け取れる timeout で走らせる。

止まったら、作成の前か後かで走らせ直し方が決まる。作成の前 (前提の確かめ・secret の配置) で止まったら、原因を直してそのまま走らせ直す。作成の後で止まったら、雛形は隔離環境を残すので、人に `sbx rm <名前>` で消してもらってから走らせ直す。原因ごとの直し方:

- secret の環境変数が無い: 値を受け取らずに、変数を設定した人の shell で走らせるよう頼む
- `sbx の daemon に届かない` (`Operation not permitted` なら Claude Code の sandbox が daemon への接続を塞いでいる): 人の shell で走らせるよう頼む
- 同じ名前の隔離環境が既にある: 消さずに人へ返す。消すかは人が決める
- global の egress policy が default deny でない: global の policy を変えずに人へ返す。global の policy はその host のすべての隔離環境に効く
- 道具の導入が 403 などで落ちる: `sbx policy log <名前>` で拒否された宛先を探し、`ALLOW_HOSTS` に足す案を人に示す

## 5. 報告する

スクリプトが最後に出す `PASS` / `FAIL` の行で報告する。

- 1 つでも `FAIL` があれば、構築できたと報告しない。落ちた項目と、隔離環境を消さずに残してあることを返す
- すべて `PASS` なら、隔離環境の名前と構築スクリプトの置き場と、許可した egress 先 (`IMAGE_PULL_HOSTS` を含む) を伝える。作り直すときは、`sbx rm <名前>` で消してからスクリプトを skill なしで走らせればよいことも伝える
