"""adapter の登録表 — tracker の中立語彙 → 観測 port の実装。

**adapter を足す作業をここ 1 箇所に閉じる**のが本 module の存在理由。tracker 名を持つ表が
「観測に使う class 表」と「宣言で許す tracker」に割れていると、片方だけ足した状態が生まれる:
宣言は通るのに adapter が無い (tick が KeyError で落ちる) か、adapter はあるのに宣言が名指しで
拒まれる。**どちらも表を突き合わせないと気付けない**ので、宣言側は本 module から導出する
(`declaration.SUPPORTED_TRACKERS` / `declaration.CL_TRACKERS`)。

**adapter そのものは知らない** — 持つのは名前と class の対応だけで、CLI の綴りも語彙の写像も
各 adapter に閉じる。ここに条件分岐が生えたら、それは adapter に置くべきものが漏れた合図。

**役割ごとの表を手書きしない。** adapter は担える port が違う (gh / glab は 3 port すべて、
jira は Tracker だけ) が、その差は class の部分型関係が既に持っているので、役割別の tracker
集合は**表 1 つから導出する**。第 2 の表を手書きすると、`ADAPTERS` へ足したのに役割表へ
足し忘れた adapter が黙って役割から漏れる。
"""

from collections import namedtuple

from dispatch_v2 import gh_adapter, glab_adapter, jira_adapter, ports

#: tracker の中立語彙 (`refs.ISSUE_PATTERNS` の key) → adapter class。
#: adapter を足すのは entry 1 行と adapter 実装だけ
ADAPTERS = {
    gh_adapter.TRACKER: gh_adapter.GhAdapter,
    glab_adapter.TRACKER: glab_adapter.GlabAdapter,
    jira_adapter.TRACKER: jira_adapter.JiraAdapter,
}


def _trackers_implementing(registry, port):
    """その port を実装する adapter の tracker 名 (辞書順)。

    並びを固定するのは、未対応 tracker の error 文面に候補が並ぶため (実行ごとに順が変わると
    「同じ不備が同じ文面で出る」前提が崩れる)。
    """
    return tuple(sorted(name for name, cls in registry.items() if issubclass(cls, port)))


#: 役割別の tracker 集合。**位置ではなく名前で受け取る** — 3 つとも同じ型 (tracker 名の tuple)
#: なので、位置で渡し違えても型では気付けない
RoleSets = namedtuple("RoleSets", "issue_places cl_places cl_link_sources")


def derive_role_sets(registry):
    """登録表 1 つから役割別の tracker 集合を導く。

    - `issue_places`: **issue 置き場**として宣言できる tracker (`registry` 全件 — issue を
      観測できない adapter は登録表に居ない)
    - `cl_places`: **CL 置き場**として宣言できる tracker (ChangeHost port を実装するものだけ。
      jira は入らない — Jira は変更置き場ではなく `refs.CL_PATTERNS` にも無い)
    - `cl_link_sources`: issue 置き場側から**紐づき (issue → CL) を引ける** tracker。引けない
      置き場では台帳に記録した紐づきが源になる (`cl_record.LedgerCLLinks` / ADR 0059)

    **登録表を引数に取る**のは、「adapter を足せば役割別の集合が付いてくる」を test が
    probe adapter で実際に確かめられるようにするため。module の定数を突き合わせるだけの検査は、
    同じ値を手書きした第 2 の表でも通ってしまう (それがこの module が塞いでいる不備そのもの)。
    """
    return RoleSets(
        issue_places=tuple(sorted(registry)),
        cl_places=_trackers_implementing(registry, ports.ChangeHostPort),
        cl_link_sources=_trackers_implementing(registry, ports.CLLinkPort),
    )


#: 宣言の検査が引く名前 (中身は `derive_role_sets` の docstring が持つ)
SUPPORTED_TRACKERS, CL_TRACKERS, CL_LINK_TRACKERS = derive_role_sets(ADAPTERS)
