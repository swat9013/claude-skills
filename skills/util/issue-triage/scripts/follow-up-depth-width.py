#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""follow-up issue の深さ (issue ごと) と、幅・流入率 (週ごと) を GitHub の sub-issue の木と検索から計算する。

usage:
  follow-up-depth-width.py [共通の引数] depth <issue 番号>   # 深さ
  follow-up-depth-width.py [共通の引数]                      # 週ごとの幅と流入率 (直近 4 週)

共通の引数:
  --repo OWNER/NAME              省くと cwd の repo (`gh repo view`) を使う
  --planned-split-label LABEL    計画的な分解の親を表す label (くり返し可)。省くと、すべての親子の辺を
                                 follow-up issue の辺として数える — 深さが大きめに出て、着手可を付けにくい側に倒れる
  --limit N                      深さの上限 (既定 2)

gh の認証が要る。

## 深さ

issue から親 (sub-issue の親) を根まで辿り、follow-up issue の辺の数を深さとする。親を持たない
issue の深さは 0。親が計画的な分解の親 (`--planned-split-label` の label を持つ issue) である辺は
follow-up issue の辺ではないので数えない — その親の先の祖先へは辿り続ける。

親は REST の issue の `parent_issue_url` から取り、親の issue を取得してその label を読む。親の
取得が失敗すれば exit 2 になる。ただし token から読めない repo にある親を API が `parent_issue_url`
ごと隠すなら、親なし (深さ 0) と区別できない (この振る舞いは確かめていない)。sub-issue の親は同じ
owner の repo に限られるので、owner の repo を読めない token で実行しない。

stdout の 1 行目は `depth=<n> limit=<上限> result=<within-limit|over-limit>`、2 行目は辿った鎖。

## 終了コード

深さ (`depth`) の呼び出し側が着手可を付けてよいのは、`depth` の exit 0 のときだけ。

- 0: 深さが上限以内
- 3: 深さが上限 (`--limit`) を超えた
- それ以外: 失敗。本 script が検知した失敗と引数の誤り (issue 番号の欠け等) は 2 で返し、捕まえ損ねた
  例外 (1) や起動の失敗 (uv / interpreter が無い等) もここに入る — 失敗を専用の code に寄せず「0 と 3
  以外」で受けるので、想定外の落ち方も着手可を付けない側 (fail-closed) に倒れる

幅 (引数なし) は出力し終えたら 0、失敗は 2。幅は `depth` を付けない起動でしか出ないので、深さを測る
つもりの起動が幅の exit 0 を受け取ることはない。

## 不変条件 (1・2 は exit != 0 で落とすもの、3 は落としたときの message の形)

1. 深さが上限を超えたら exit 3
2. 深さ・幅・流入率を確かめられないときは exit 0 にしない (exit 2): gh が無い・gh が非 0 で終わるか
   `GH_TIMEOUT_SECONDS` を超える・応答が JSON でないか sub-issue に対応した issue の形でない・GraphQL の
   `errors` がある・issue (祖先を含む) を取得できない・親の鎖が循環するか `WALK_CAP` を超える・1 週の
   検索結果が検索 API の上限 (`SEARCH_CAP`) を超える。応答の内側の形の崩れ (label や検索結果の field の
   欠け) は個別に検査せず、捕まえ損ねた例外 (exit 1) として落とす — どちらも 0 と 3 以外なので着手可は付かない
3. 親の鎖の循環と `WALK_CAP` 超過は別の message で報告する: 循環なら sub-issue のデータを直し、段数超過なら
   暴走した連鎖か上限を見直す — 対処が逆向きなので、stderr からどちらが起きたかを読めるようにする

## 幅と流入率

ISO 週 (月〜日。日付は UTC — GitHub の検索の日付と同じ) ごとに、その週に作成された follow-up issue
の数 ÷ その週に merge した pull request (CL) の数。follow-up issue は「親を持ち、親が計画的な分解の
親でない issue」。幅は人が週 1 回見る改善の指標で、着手可の判定には使わない。今週は途中までの値。

同じ行に、その週に作成された issue 全件 (親を持たない issue を含む) の数と、それを merge した CL の数で
割った流入率 (inflow) を並べる。幅は連鎖の広がりを診る値で、親を持たない issue を数えないので、open が
減るかどうかは流入率で見る。
"""

import argparse
import datetime
import json
import re
import subprocess
import sys
from dataclasses import dataclass

DEFAULT_DEPTH_LIMIT = 2
EXIT_OK = 0
EXIT_FAILURE = 2
EXIT_OVER_LIMIT = 3

WEEKS_SHOWN = 4
WALK_CAP = 50
SEARCH_CAP = 1000  # GitHub の検索 API が返す結果の上限
GH_TIMEOUT_SECONDS = 60

ISSUE_API_URL = re.compile(r"/repos/([^/]+/[^/]+)/issues/(\d+)$")

CREATED_ISSUES_QUERY = """
query($q: String!, $cursor: String) {
  search(query: $q, type: ISSUE, first: 100, after: $cursor) {
    issueCount
    pageInfo { hasNextPage endCursor }
    nodes {
      ... on Issue {
        number
        parent { number labels(first: 100) { nodes { name } } }
      }
    }
  }
}
"""

COUNT_QUERY = """
query($q: String!) {
  search(query: $q, type: ISSUE, first: 0) { issueCount }
}
"""


class Failure(Exception):
    """深さ・幅を確かめられなかった。exit 2 で返す。"""


@dataclass(frozen=True)
class IssueRef:
    repo: str
    number: int

    def display(self, home_repo: str) -> str:
        return f"#{self.number}" if self.repo == home_repo else f"{self.repo}#{self.number}"


def run_gh(argv: list[str]) -> str:
    try:
        proc = subprocess.run(["gh", *argv], capture_output=True, text=True, timeout=GH_TIMEOUT_SECONDS)
    except FileNotFoundError as e:
        raise Failure("gh が見つからない") from e
    except subprocess.TimeoutExpired as e:
        raise Failure(f"gh {argv[0]} {argv[1]} が {GH_TIMEOUT_SECONDS} 秒で終わらない") from e
    if proc.returncode != 0:
        raise Failure(f"gh {argv[0]} {argv[1]} が exit {proc.returncode}: {proc.stderr.strip()}")
    return proc.stdout


def parse_json(stdout: str, source: str) -> dict:
    try:
        body = json.loads(stdout)
    except json.JSONDecodeError as e:
        raise Failure(f"{source} の応答が JSON でない: {stdout[:200]!r}") from e
    if not isinstance(body, dict):
        raise Failure(f"{source} の応答が object でない: {stdout[:200]!r}")
    return body


def graphql(query: str, variables: dict[str, str | None]) -> dict:
    argv = ["api", "graphql", "-f", f"query={query}"]
    for key, value in variables.items():
        if value is not None:
            argv += ["-f", f"{key}={value}"]
    body = parse_json(run_gh(argv), "gh api graphql")
    if body.get("errors"):
        raise Failure(f"GraphQL がエラーを返した: {json.dumps(body['errors'], ensure_ascii=False)}")
    data = body.get("data")
    if not isinstance(data, dict):
        raise Failure(f"GraphQL の応答に data が無い: {json.dumps(body, ensure_ascii=False)[:200]}")
    return data


def fetch_issue(ref: IssueRef) -> dict:
    path = f"repos/{ref.repo}/issues/{ref.number}"
    issue = parse_json(run_gh(["api", path]), f"gh api {path}")
    # 親の無い issue は parent_issue_url を key ごと持たない。sub-issue に対応しない API (古い GHES 等) の
    # 応答も同じ形になるので、sub_issues_summary で対応を確かめてから「親なし」と読む — 確かめずに読むと
    # どの issue も深さ 0 になって fail-open する
    if "sub_issues_summary" not in issue or not isinstance(issue.get("labels"), list):
        raise Failure(f"gh api {path} の応答に sub_issues_summary か labels が無い (sub-issue 非対応の API か)")
    return issue


def parent_ref(issue: dict) -> IssueRef | None:
    url = issue.get("parent_issue_url")
    if url is None:
        return None
    m = ISSUE_API_URL.search(url)
    if m is None:
        raise Failure(f"parent_issue_url を issue として読めない: {url}")
    return IssueRef(m.group(1), int(m.group(2)))


def is_follow_up_edge(parent_label_names: set[str], planned_split: frozenset[str]) -> bool:
    """親から子への辺が follow-up issue の辺か。深さと幅はこの 1 つの判定で数える。
    計画的な分解の親から子への辺は、follow-up issue の辺ではない。"""
    return not (parent_label_names & planned_split)


def measure_depth(start: IssueRef, planned_split: frozenset[str]) -> tuple[int, list[str]]:
    """start の深さと、根まで辿った鎖の表示 (数えなかった辺の親には注記を添える) を返す。"""
    depth = 0
    chain = [start.display(start.repo)]
    seen = {start}
    issue = fetch_issue(start)
    while (parent := parent_ref(issue)) is not None:
        # 循環と段数超過は分けて報告する (不変条件 3)
        if parent in seen:
            revisited = parent.display(start.repo)
            raise Failure(f"親の鎖が循環した ({revisited} に戻った): {' <- '.join([*chain, revisited])}")
        if len(seen) > WALK_CAP:
            raise Failure(f"親の鎖が上限の {WALK_CAP} 段を超えた: {' <- '.join(chain)}")
        seen.add(parent)
        issue = fetch_issue(parent)
        if is_follow_up_edge({label["name"] for label in issue["labels"]}, planned_split):
            depth += 1
            chain.append(parent.display(start.repo))
        else:
            chain.append(f"{parent.display(start.repo)}[計画的な分解: 数えない]")
    return depth, chain


def created_issues(q: str) -> list[dict]:
    """検索に当たった issue の node を全件返す。検索 API の上限を超えたら全件を数えられないので失敗。"""
    nodes: list[dict] = []
    cursor = None
    while True:
        result = graphql(CREATED_ISSUES_QUERY, {"q": q, "cursor": cursor})["search"]
        if result["issueCount"] > SEARCH_CAP:
            raise Failure(f"検索結果 {result['issueCount']} 件が上限 {SEARCH_CAP} を超えた: {q}")
        nodes += result["nodes"]
        if not result["pageInfo"]["hasNextPage"]:
            return nodes
        cursor = result["pageInfo"]["endCursor"]


def is_follow_up(node: dict, planned_split: frozenset[str]) -> bool:
    parent = node.get("parent")
    if not parent:
        return False
    return is_follow_up_edge({label["name"] for label in parent["labels"]["nodes"]}, planned_split)


def report_width(repo: str, planned_split: frozenset[str]) -> None:
    today = datetime.datetime.now(datetime.timezone.utc).date()
    this_monday = today - datetime.timedelta(days=today.weekday())
    print("# ISO 週 (月〜日、UTC)。width = follow-up issue 数 / merge した CL 数。"
          "inflow = 作成した issue 全件 / merge した CL 数。着手可の判定には使わない")
    for back in range(WEEKS_SHOWN - 1, -1, -1):
        start = this_monday - datetime.timedelta(weeks=back)
        span = f"{start.isoformat()}..{(start + datetime.timedelta(days=6)).isoformat()}"
        created = created_issues(f"repo:{repo} is:issue created:{span}")
        follow_ups = sum(1 for node in created if is_follow_up(node, planned_split))
        # is:merged は pull request にしか当たらないので、種別の qualifier は付けない
        merged = graphql(COUNT_QUERY, {"q": f"repo:{repo} is:merged merged:{span}"})["search"]["issueCount"]
        width = f"{follow_ups / merged:.2f}" if merged else "-"
        inflow = f"{len(created) / merged:.2f}" if merged else "-"
        year, week, _ = start.isocalendar()
        partial = " (今週・途中)" if back == 0 else ""
        print(f"{year}-W{week:02d} {span} follow-up={follow_ups} merged-cl={merged} width={width} created={len(created)} inflow={inflow}{partial}")


def current_repo() -> str:
    repo = run_gh(["repo", "view", "--json", "nameWithOwner", "--jq", ".nameWithOwner"]).strip()
    if not repo:
        raise Failure("cwd の repo を gh repo view で決められない")
    return repo


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--repo", help="OWNER/NAME。省くと cwd の repo")
    p.add_argument(
        "--planned-split-label",
        action="append",
        default=[],
        metavar="LABEL",
        help="計画的な分解の親を表す label (くり返し可)。この label の親から子への辺は数えない",
    )
    p.add_argument("--limit", type=int, default=DEFAULT_DEPTH_LIMIT, help=f"深さの上限 (既定 {DEFAULT_DEPTH_LIMIT})")
    sub = p.add_subparsers(dest="mode", metavar="depth")
    depth = sub.add_parser("depth", help="issue の follow-up の深さを測る (省くと週ごとの幅と流入率を出す)")
    depth.add_argument("issue", type=int, help="深さを測る issue の番号")
    return p.parse_args(argv)


def main(args: argparse.Namespace) -> int:
    repo = args.repo or "<cwd の repo>"
    context = f"depth {repo}#{args.issue}" if args.mode == "depth" else f"width {repo}"
    planned_split = frozenset(args.planned_split_label)
    try:
        repo = args.repo or current_repo()
        if args.mode != "depth":
            report_width(repo, planned_split)
            return EXIT_OK
        depth, chain = measure_depth(IssueRef(repo, args.issue), planned_split)
    except Failure as e:
        print(f"[error] {context}: {e}", file=sys.stderr)
        return EXIT_FAILURE
    over = depth > args.limit
    print(f"depth={depth} limit={args.limit} result={'over-limit' if over else 'within-limit'}")
    print("chain: " + " <- ".join(chain))
    return EXIT_OVER_LIMIT if over else EXIT_OK


if __name__ == "__main__":
    try:
        sys.exit(main(parse_args()))
    except KeyboardInterrupt:
        sys.exit(130)
