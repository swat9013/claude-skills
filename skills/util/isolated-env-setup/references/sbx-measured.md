# sbx の実測

Docker Sandboxes (`sbx`) で隔離環境を組むときに踏む挙動。実測した版は v0.45.1 〜 v0.54.0。雛形 (`scripts/build-isolated-env.template.sh`) が固定している項には「雛形」と添えた。

## 作成・削除の順序

- sandbox スコープの secret は VM の作成**前**に置く (`sbx secret set <service> --sandbox <名前>`。値は stdin から渡す)。雛形
- sandbox スコープの egress rule は VM の作成**後**に置く (`sbx policy allow network --sandbox <名前> <host:port,...>`)。雛形
- `sbx stop` は使用中の session も切断する。人が中で作業している環境を止める前に確かめる
- `sbx rm` はその sandbox スコープの secret も消す。作り直すたびに secret を置き直すのはこのため。雛形
- `sbx create` に path を渡さなければ、host の作業ツリーを mount しない。`claude` の agent の VM では user が `agent`、作業ディレクトリが `/home/agent/workspace` になり、Docker と `sudo` が最初から使える。雛形
- `sbx secret set --force` は既にある secret を確かめずに上書きし、`sbx create --no-progress` は進捗の表示を止める (v0.54.0 で確かめた)。雛形

## egress の制御と検証

- global policy が default deny であることを前提にする (`sbx policy check network example.com:443` が `Denied` になる)。`check` は拒否のとき非 0 で終わるので、終了コードでなく出力で判定する。雛形
- VM の直下 (proxy 経由) は、許可外の宛先へ HTTP 403 を返すので、素の `curl` は exit 0 になる。body の 1 行目 (`Approval required for ...` / `Blocked by local rule for ...`) で拒否を判定する。雛形
- VM 内の Docker container (proxy を通らない) は、許可外の宛先で名前解決の失敗 (exit 6) か接続の切断 (exit 35 / 52) になる。雛形
- VM 内の Docker の image pull も egress policy を通る。Docker Hub の blob は `production.cloudflare.docker.com:443` か `production.cloudfront.docker.com:443` から来る。雛形
- 拒否された宛先は `sbx policy log <名前>` の BLOCKED REQUESTS に出る。導入が 403 で落ちたら、ここで足りない宛先を探す
- agent の kit (`claude` など) は、その agent が使う宛先 (`api.anthropic.com` など) を sandbox スコープで自動で許可する

道具ごとに要る宛先:

| 道具 | 宛先 |
|---|---|
| mise の導入 (`curl https://mise.run \| sh`) | `mise.run:443`・`mise.jdx.dev:443`・`mise-versions.jdx.dev:443` |
| GitHub の clone・release の取得 | `github.com:443`・`**.github.com:443`・`**.githubusercontent.com:443` |
| Claude Code の plugin の marketplace 追加 | GitHub の宛先 (marketplace の repo を HTTPS で clone する) |

## credential の差し込みと git

- VM 内に実際のトークンや SSH 鍵を置かず、proxy による placeholder の差し込みを使う (`sbx secret set`)。VM 内の環境変数には placeholder だけが入る
- 持ち込んだ repo の `origin` が SSH 形式なら HTTPS 形式へ書き換える (VM 内に SSH 鍵が無いので)。雛形
- 成果物の回収は origin への push を基本にし、host の作業ツリーを直接 mount しない

## file の持ち込み・権限・host の設定の分離

- `sbx cp` は host の uid / mode のまま置くので、`tar -cf - ... | sbx exec -i <名前> tar -xf - -C <dir>` で VM の中で展開する。雛形
- host の作業ツリーをそのまま流すと未追跡の file (`.env` など) まで入るので、host で clone し直したものを流す。雛形
- host の agent skills を持ち込まないときは `--skills off` (`sandboxOptions.skills: off`) を指定する。雛形

## template (キャッシュ層) を使うとき

- `sbx template save` は稼働中の VM を拒むので、先に `sbx stop` する
- ローカルに save した template から作るときは `pullPolicy: never` (`sbx create --pull never`) を指定する

## 道具の導入の形

雛形の `vm_setup_script` に書く。VM の中で `bash -euo pipefail` として stdin を閉じて走り、`sudo` が使える。

GitHub release の CLI を checksum で確かめて入れる (VM の arch は host に従う):

```bash
case "$(uname -m)" in
  x86_64) arch=amd64 ;;
  aarch64 | arm64) arch=arm64 ;;
  *) echo "unsupported arch: $(uname -m)" >&2; exit 1 ;;
esac
base=https://github.com/<owner>/<repo>/releases/latest/download
(
  cd "$(mktemp -d)"   # subshell に閉じ、後続の導入の cwd を変えない
  curl -fsSLO "$base/checksums.txt"
  file=$(grep -o "<tool>_[^ ]*_linux_${arch}\.tar\.gz" checksums.txt | head -n 1)
  curl -fsSLO "$base/$file"
  sha256sum -c checksums.txt --ignore-missing
  tar xzf "$file" <tool> && sudo install -m 755 <tool> /usr/local/bin/
)
```

Claude Code の plugin を有効にする (依存先の plugin も、その marketplace ごと明示して入れる):

```bash
claude plugin marketplace add <owner>/<marketplace の repo>
claude plugin install <plugin>@<marketplace>
claude plugin list
```
