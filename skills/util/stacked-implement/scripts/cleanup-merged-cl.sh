#!/usr/bin/env bash
# merged か closed の CL 1 本の後片付けを決定的に行う。規則・出力・exit code は --help が正本。
# set -e を付けない: 1 つの削除が失敗しても、残りを試して「残したもの」として報告するため。
set -uo pipefail

usage() {
  cat <<'EOF'
usage: cleanup-merged-cl.sh [--remote <名前>] [--default-branch <名前>] [--keep-remote-branch] <CL の branch> <worktree の絶対 path>

cwd の repo (worktree の外。通常は main checkout) で呼ぶ。次を順に行う。
  1. worktree に未 commit の変更 (未追跡の file を含む) があれば、何も消さずに終わる (exit 3)。
  2. remote の CL の branch を消す (git push <remote> --delete)。--keep-remote-branch で省く。
     消す前に git fetch <remote> を 1 回走らせ (手順 4 の fetch と兼ねる)、<remote>/<CL の branch> が
     あれば git merge-base --is-ancestor <remote>/<CL の branch> <remote>/<既定ブランチ> を見る。
     exit 0 でなければ (merge が終わっていない・auto-merge の予約だけ・squash merge) と、fetch が
     失敗したときは、remote の branch を消さずに残し、未 merge の作業の置き場を守るため
     worktree とローカルの branch にも触れずに終わる (exit 1)。<remote>/<CL の branch> が無ければ
     そのまま push --delete を試し、"remote ref does not exist" は merge で既に消えているので
     消し終えたものとして扱う。その他の失敗でも、残して終わる。
  3. worktree を unlock して remove する。path が既に無ければ飛ばす。
  4. CL の branch と、worktree 作成時の branch (worktree-<worktree の basename>。無ければ飛ばす) を
     git branch -d で消す。-d が拒んだ branch があれば、git fetch <remote> の結果を使い (手順 2 で走らせていればその結果。fetch は全体で 1 回)、拒まれた
     branch ごとに git merge-base --is-ancestor <branch> <remote>/<既定ブランチ> が exit 0 のときだけ
     git branch -D で消す。fetch が失敗していたら確かめずに拒まれた branch を全部残す。
     exit 0 でない branch (squash merge・merge されずに closed など) と、-D が拒まれた branch は残す。

--remote の既定は origin。--default-branch の既定は <remote>/HEAD が指す branch。
呼び出し側が確かめること: 手順 2 の前に、CL の branch を target にしている open の CL が無いこと
(付け替えより先に消すと、後続の CL は close されうる)。merge の完了は script が remote で確かめるので、
auto-merge の予約だけが済んだ段階で呼んでも remote の branch は消えない。残っていて付け替え前なら、
--keep-remote-branch を付けて呼ぶ (closed の CL も remote の branch を消さないので付ける)。

出力は 1 行 1 件で、行頭が "deleted:" (消した) か "kept:" (残した。理由付き)。
"note:" は補足 (失敗したコマンドの出力など)。
exit code: 0 = 全部消した / 1 = 残したものがある / 2 = 引数か前提の誤り / 3 = 未 commit の変更で何も消していない。
EOF
}

remote=origin
default_branch=
keep_remote=0
positional=()
while [ $# -gt 0 ]; do
  case "$1" in
    -h|--help) usage; exit 0 ;;
    --remote) remote="${2:?--remote には名前が要る}"; shift 2 ;;
    --default-branch) default_branch="${2:?--default-branch には名前が要る}"; shift 2 ;;
    --keep-remote-branch) keep_remote=1; shift ;;
    --) shift; positional+=("$@"); break ;;
    -*) echo "unknown option: $1" >&2; usage >&2; exit 2 ;;
    *) positional+=("$1"); shift ;;
  esac
done
if [ "${#positional[@]}" -ne 2 ]; then
  usage >&2
  exit 2
fi
cl_branch="${positional[0]}"
worktree="${positional[1]}"
case "$worktree" in
  /*) ;;
  *) echo "worktree は絶対 path で渡す: $worktree" >&2; exit 2 ;;
esac

if [ -z "$default_branch" ]; then
  head_ref=$(git symbolic-ref --short "refs/remotes/$remote/HEAD" 2>/dev/null) || {
    echo "$remote の既定ブランチを引けない。--default-branch で渡す" >&2
    exit 2
  }
  default_branch="${head_ref#"$remote"/}"
fi

# fetch は全体で 1 回。結果 (0 = 成功) と出力を持ち回る
fetch_done=0
fetch_rc=0
fetch_out=
ensure_fetch() {
  if [ "$fetch_done" -eq 0 ]; then
    fetch_out=$(git fetch "$remote" 2>&1)
    fetch_rc=$?
    fetch_done=1
  fi
  return "$fetch_rc"
}

kept=0
keep() { echo "kept: $1"; kept=1; }

# 1. 未 commit の変更があれば何も消さない
if [ -d "$worktree" ]; then
  if ! dirty=$(cd "$worktree" && git status --porcelain 2>&1); then
    echo "kept: worktree $worktree: git status が失敗した ($dirty)。何も消していない"
    exit 3
  fi
  if [ -n "$dirty" ]; then
    echo "kept: worktree $worktree: 未 commit の変更がある。何も消していない"
    printf 'note: %s\n' "$dirty"
    exit 3
  fi
fi

# 2. remote の branch。merge の完了を remote で確かめてから消す
if [ "$keep_remote" -eq 0 ]; then
  if ! ensure_fetch; then
    keep "remote branch $remote/$cl_branch: git fetch $remote が失敗したので merge の完了を確かめていない。worktree とローカルの branch も残す"
    printf 'note: git fetch %s: %s\n' "$remote" "$fetch_out"
    exit 1
  fi
  if git show-ref --verify --quiet "refs/remotes/$remote/$cl_branch" &&
    ! git merge-base --is-ancestor "$remote/$cl_branch" "$remote/$default_branch" 2>/dev/null; then
    keep "remote branch $remote/$cl_branch: $remote/$default_branch に含まれない (merge が終わっていないか squash merge)。worktree とローカルの branch も残す"
    exit 1
  fi
  if out=$(git push "$remote" --delete "$cl_branch" 2>&1); then
    echo "deleted: remote branch $remote/$cl_branch"
  elif printf '%s' "$out" | grep -q 'remote ref does not exist'; then
    echo "deleted: remote branch $remote/$cl_branch (merge で既に消えていた)"
  else
    keep "remote branch $remote/$cl_branch: 削除に失敗した。worktree とローカルの branch も残す"
    printf 'note: %s\n' "$out"
    exit 1
  fi
fi

# 3. worktree
if [ -d "$worktree" ]; then
  git worktree unlock "$worktree" >/dev/null 2>&1 || true # lock されていなくても失敗する
  if out=$(git worktree remove "$worktree" 2>&1); then
    echo "deleted: worktree $worktree"
  else
    keep "worktree $worktree: remove に失敗した"
    printf 'note: %s\n' "$out"
  fi
else
  echo "deleted: worktree $worktree (既に無かった)"
fi

# 4. branch
branches=("$cl_branch")
wt_branch="worktree-$(basename "$worktree")"
if [ "$wt_branch" != "$cl_branch" ] && git show-ref --verify --quiet "refs/heads/$wt_branch"; then
  branches+=("$wt_branch")
fi

refused=()
for b in "${branches[@]}"; do
  if ! git show-ref --verify --quiet "refs/heads/$b"; then
    echo "deleted: branch $b (既に無かった)"
  elif git branch -d "$b" >/dev/null 2>&1; then
    echo "deleted: branch $b"
  else
    refused+=("$b")
  fi
done

if [ "${#refused[@]}" -gt 0 ]; then
  if ! ensure_fetch; then
    for b in "${refused[@]}"; do
      keep "branch $b: git branch -d が拒み、git fetch $remote が失敗したので確かめていない"
    done
    printf 'note: git fetch %s: %s\n' "$remote" "$fetch_out"
  else
    for b in "${refused[@]}"; do
      if ! git merge-base --is-ancestor "$b" "$remote/$default_branch" 2>/dev/null; then
        keep "branch $b: $remote/$default_branch に含まれない (squash merge か未 merge)"
      elif out=$(git branch -D "$b" 2>&1); then
        echo "deleted: branch $b (-D。$remote/$default_branch に含まれる)"
      else
        keep "branch $b: git branch -D が拒まれた"
        printf 'note: %s\n' "$out"
      fi
    done
  fi
fi

exit "$kept"
