#!/usr/bin/env bash
# 隔離環境の構築スクリプト (Docker Sandboxes の `sbx` で microVM を作る)。
# skill なしで単独に再実行できる。値を変えるときは「構築の値」の節だけを書き換える。
#
# 不変条件:
#   1. sbx が無ければ導入を案内して止まる
#   2. 同じ名前の隔離環境が既にあれば、何も変えずに止まる (消すのは人)
#   3. global の egress policy が default deny でなければ、作る前に止まる
#   4. sandbox スコープの secret は作成の前に、egress rule は作成の後に置く
#   5. secret の値をこの file に書かない (値を持つ host の環境変数の名前だけを書く)
#   6. host の作業ツリーを mount しない (repo は host で clone し直したものを tar で流し込む)
#   7. 検証は毎回走り、1 つでも落ちたら落ちた項目を出して非 0 で終わる
set -euo pipefail

# ===== 構築の値 =====
NAME="__NAME__"   # 隔離環境の名前
# 持ち込む repo (host の repo の path か clone URL)。SSH 形式の origin は HTTPS 形式へ書き換える
REPOS=(
  __REPOS__
)
# 許可する egress 先 (sandbox スコープ。host:port)
ALLOW_HOSTS=(
  __ALLOW_HOSTS__
)
# 検証で届くことを確かめる、許可した宛先の URL (ALLOW_HOSTS に含まれる host)
ALLOW_PROBE_URL="__ALLOW_PROBE_URL__"
# sandbox スコープの secret。<sbx の service 名>=<値を持つ host の環境変数の名前>
SECRETS=(
  __SECRETS__
)
# 道具の導入の script を出す。VM の中で bash -euo pipefail として、stdin を閉じて走る。sudo が使える
vm_setup_script() {
  cat <<'VM'
:  # SETUP_COMMANDS
VM
}
# ===== 構築の値ここまで =====

# 検証の container が image を pull するのに要る宛先 (VM 内 Docker の pull も egress policy を通る)。
# ALLOW_HOSTS に加えて必ず許可するので、構築を終えた環境からもこれらへは届く
IMAGE_PULL_HOSTS=(registry-1.docker.io:443 auth.docker.io:443 production.cloudflare.docker.com:443 production.cloudfront.docker.com:443)
PROBE_IMAGE="curlimages/curl:8.11.1"
DENY_PROBE_HOST="example.com"   # 許可しない宛先の見本。ALLOW_HOSTS に入れない
VM_WORKDIR="/home/agent/workspace"
PROBE_TIMEOUT=30   # 秒。到達の probe が沈黙で止まっても、検証を止めずに判定へ進む
PULL_TIMEOUT=300   # 秒。検証の image の pull が沈黙で止まっても、検証を止めずに判定へ進む

die() { echo "build-isolated-env: $*" >&2; exit 1; }

# --- 1. 前提 (何も作らずに確かめる) ---
command -v sbx >/dev/null 2>&1 \
  || die "sbx が無い。Docker Sandboxes を導入してから再実行する: https://docs.docker.com/ai/sandboxes/"
case " $NAME ${REPOS[*]} ${ALLOW_HOSTS[*]} $ALLOW_PROBE_URL ${SECRETS[*]-} " in
  *" __"*) die "構築の値に埋めていない印 (__...__) が残っている" ;;
esac
ls_out=$(sbx ls 2>&1) || die "sbx の daemon に届かない: ${ls_out%%$'\n'*}"
if sbx inspect "$NAME" >/dev/null 2>&1; then
  die "隔離環境 $NAME が既にある。作り直すなら、人が 'sbx rm $NAME' で消してから再実行する"
fi
# check は拒否のとき非 0 で終わるので、終了コードでなく出力で判定する
check=$(sbx policy check network "$DENY_PROBE_HOST:443" 2>&1 || true)
case "$check" in
  *Denied*) ;;
  *) die "global の egress policy が default deny でない ($DENY_PROBE_HOST:443 の判定: ${check%%$'\n'*})" ;;
esac
# 拒否の見本が許可の glob に当たると、検証の拒否の項が必ず落ちる。sbx の `**` (複数の label) は
# bash の `*` に当たり、port を省いた許可はすべての port に効くので、:443 を付けた形でも照らす
for host in "${ALLOW_HOSTS[@]}"; do
  pattern=${host//\*\*/*}
  # shellcheck disable=SC2254  # 許可の glob として照らす
  case "$DENY_PROBE_HOST:443" in $pattern | ${pattern%:443}:443) die "ALLOW_HOSTS の $host が検証の拒否の見本 $DENY_PROBE_HOST に当たる" ;; esac
done

# repo は作成の前に host で clone し直しておく (clone し直すので未追跡の file (.env など) は入らない)
staging=$(mktemp -d)
trap 'rm -rf "$staging"' EXIT
repo_dirs=()
for src in "${REPOS[@]}"; do
  if [ -d "$src" ]; then origin=$(git -C "$src" remote get-url origin); else origin="$src"; fi
  # VM に SSH 鍵は無いので、[user@]host:path と ssh://[user@]host[:port]/path を https://host/path へ。
  # URL に埋め込まれた credential (https://user:token@host/...) も落とす。ssh config の Host 別名は解決しない
  origin=$(printf '%s' "$origin" | sed -E 's#^ssh://([^@/]+@)?([^/:]+)(:[0-9]+)?/#https://\2/#; /^[a-z+]+:\/\//! s#^([^@/:]+@)?([^:/]+):#https://\2/#; s#^([a-z]+://)[^@/]+@#\1#')
  # 以降の message は credential を落とした origin で出す ($src は credential を含みうる)
  case "$origin" in https://*) ;; *) die "origin を HTTPS 形式にできない: $origin" ;; esac
  repo=$(basename "$origin" .git)
  [ ! -e "$staging/$repo" ] || die "持ち込む repo の名前 $repo が重なる: $origin"
  git clone --quiet "$src" "$staging/$repo" 2>/dev/null || die "clone できない: $origin"
  git -C "$staging/$repo" remote set-url origin "$origin"
  repo_dirs+=("$repo")
done

# --- 2. sandbox スコープの secret (作成の前に置く) ---
for entry in ${SECRETS[@]+"${SECRETS[@]}"}; do
  service="${entry%%=*}" var="${entry#*=}"
  [ -n "${!var:-}" ] || die "secret $service の値を持つ環境変数 $var が host に無い"
  printf '%s' "${!var}" | sbx secret set "$service" --sandbox "$NAME" --force >/dev/null
done

# --- 3. 作成 (workspace を mount せず、host の agent skills も持ち込まない) ---
set -E
trap 'echo "build-isolated-env: 構築が途中で止まった (行 $LINENO)。検証は走っていない。隔離環境 $NAME が残っていれば消さずにある (sbx ls で確かめる)" >&2' ERR
sbx create --name "$NAME" --skills off --no-progress claude

# --- 4. sandbox スコープの egress rule (作成の後に置く) ---
allow=$(IFS=,; echo "${ALLOW_HOSTS[*]},${IMAGE_PULL_HOSTS[*]}")
sbx policy allow network --sandbox "$NAME" "$allow" >/dev/null

# --- 5. repo の持ち込み (sbx cp は host の uid/mode のまま置くので、VM の中で tar を展開する) ---
# repo の dir だけを流す (`.` を流すと作業ディレクトリ自身の mode と mtime まで書き換える)
tar -C "$staging" -cf - "${repo_dirs[@]}" | sbx exec -i "$NAME" tar -xf - -C "$VM_WORKDIR"

# --- 6. 道具の導入 (script を file に置き、stdin を閉じて走らせる。stdin を読む導入が後続の行を飲み込まない) ---
vm_setup_script | sbx exec -i "$NAME" sh -c 'cat > /tmp/build-isolated-env-setup.sh'
sbx exec "$NAME" bash -euo pipefail /tmp/build-isolated-env-setup.sh < /dev/null

# --- 7. 検証 (落ちた項目も最後まで判定してから出す) ---
trap - ERR
failed=()
pass() { echo "PASS $1"; }
fail() { echo "FAIL $1: $2"; failed+=("$1"); }
# proxy が拒否したときの body の 1 行目
is_proxy_denial() { case "$1" in "Approval required for"* | "Blocked by local rule for"*) return 0 ;; esac; return 1; }
# VM の直下で URL を取り、body (失敗なら curl のエラー) の 1 行目を出す。curl が失敗したら非 0
fetch_first_line_in_vm() {
  local out status=0
  out=$(sbx exec "$NAME" curl -sS --max-time "$PROBE_TIMEOUT" "$1" 2>&1) || status=$?
  printf '%s\n' "${out%%$'\n'*}"
  return "$status"
}

# 7-1. 環境の直下: 許可外は proxy が HTTP 403 を返し curl は exit 0 になるので、body の 1 行目で判定する
body=$(fetch_first_line_in_vm "https://$DENY_PROBE_HOST/") || true
if is_proxy_denial "$body"; then
  pass "環境の直下で許可外の宛先が拒否される"
else
  fail "環境の直下で許可外の宛先が拒否される" "$body"
fi
if ! body=$(fetch_first_line_in_vm "$ALLOW_PROBE_URL"); then
  fail "環境の直下で許可した宛先へ届く" "$body"
elif is_proxy_denial "$body"; then
  fail "環境の直下で許可した宛先へ届く" "$body"
else
  pass "環境の直下で許可した宛先へ届く"
fi

# 7-2. 環境内の container: proxy を通らず、許可外は名前解決の失敗 (6) か接続の切断 (35 / 52) になる。
#      応答なし (28) は拒否と区別できないので拒否に数えない
status=0
sbx exec "$NAME" timeout "$PULL_TIMEOUT" docker run --rm "$PROBE_IMAGE" -sS --max-time "$PROBE_TIMEOUT" -o /dev/null "$ALLOW_PROBE_URL" || status=$?
if [ "$status" -eq 0 ]; then
  pass "環境内で container が動き、許可した宛先へ届く"
else
  fail "環境内で container が動き、許可した宛先へ届く" "exit $status"
fi
status=0
sbx exec "$NAME" timeout "$PULL_TIMEOUT" docker run --rm "$PROBE_IMAGE" -sS --max-time "$PROBE_TIMEOUT" -o /dev/null "https://$DENY_PROBE_HOST/" || status=$?
case "$status" in
  6 | 35 | 52) pass "環境内の container で許可外の宛先が拒否される" ;;
  *) fail "環境内の container で許可外の宛先が拒否される" "curl exit $status" ;;
esac

# 7-3. 実際の credential が環境の中に無い: VM の環境変数・home の file・repo の remote を host へ流し、
#      値の照合は host の bash の中でだけ行う (外部 process の argv に値を載せない)
problems=""
# shellcheck disable=SC2016  # $HOME は VM の中で展開させる
# find の -size -1M は単位へ切り上げて比べ空の file にしか当たらないので、byte で書く。
# grep -I で binary を外し (NUL を含む file は bash の変数に入らない)、SSH の秘密鍵は印を付けた行で出す
if dump=$(sbx exec "$NAME" sh -c 'env; find "$HOME" -maxdepth 3 -type f -size -1048576c -not -path "*/.cache/*" -not -path "*/workspace/*" -exec grep -Ih "" {} + 2>/dev/null; cat "$0"/*/.git/config; find "$HOME/.ssh" -name "id_*" ! -name "*.pub" -exec echo "SSHKEY: {}" \; 2>/dev/null; true' "$VM_WORKDIR") && [ -n "$dump" ]; then
  for entry in ${SECRETS[@]+"${SECRETS[@]}"}; do
    var="${entry#*=}"
    case "$dump" in *"${!var}"*) problems="$problems secret ${entry%%=*} の値;" ;; esac
  done
  case $'\n'"$dump" in *$'\n'"SSHKEY: "*) problems="$problems SSH 鍵;" ;; esac
  url_with_userinfo='url = https?://[^/[:space:]]+@'
  [[ "$dump" =~ $url_with_userinfo ]] && problems="$problems remote の URL に埋め込んだ credential;"
  if [ -z "$problems" ]; then
    pass "実際の credential が環境の中に無い"
  else
    fail "実際の credential が環境の中に無い" "${problems# }"
  fi
else
  fail "実際の credential が環境の中に無い" "環境の中を読めなかった"
fi

if [ "${#failed[@]}" -gt 0 ]; then
  echo "検証が落ちた (${#failed[@]} 件)。隔離環境 $NAME は消さずに残してある:" >&2
  printf '  - %s\n' "${failed[@]}" >&2
  exit 1
fi
echo "隔離環境 $NAME を構築し、検証をすべて通した"
