#!/usr/bin/env python3
"""inject-skill-guide.py: SessionStart hook。思想系 skill (coding-principles /
test-strategy / engineering-judgment / modularity:balanced-coupling) の invoke
タイミングを 1 行規範として additionalContext に注入する。

設計方針 (issue #277 / #446 / #510):
- SKILL.md 全文は注入しない。全文注入は「必要時に reach する」構造を壊し、常時 context を
  占有する。注入するのは invoke 誘導文のみ。
- 誘導対象 skill はハードコード。skill ごとに invoke タイミングの誘導文が異なり
  (metadata では表現しづらい)、対象数も少ないため。思想系 skill が増えた場合に
  frontmatter marker からの導出へ移行することを検討する。
- trigger は skill ごとに一意でなければならない。同じ語で 2 skill を指すと読者 (LLM) が
  invoke 先を決められず、誘導そのものが機能しない。coding-principles / test-strategy が
  「行動」trigger (何かに着手する前)、engineering-judgment / balanced-coupling が
  「判断」trigger (選択肢から選ぶ前) という切り分けを保ち、テスト固有の語は
  test-strategy 側、境界・結合の語は balanced-coupling 側にのみ置く。判断 trigger が
  2 skill に分かれる分、engineering-judgment 行に優先関係の但し書きを置く
  (test-strategy で確立した形)。
- 誘導対象は本 plugin の skill に限らない (issue #510)。`modularity:balanced-coupling`
  は第三者 plugin (`vladikk/modularity`) の skill だが、誘導文は本 plugin の hook が
  持つ。未導入環境の検出はしない — plugin install の内部 state を読む条件分岐は、その
  形式が変わったときに誘導が黙って止まり、効果測定 (coverage) が原因不明の 0 を返す。
  未導入環境の損失は 1 行ぶんの context に留まるため、沈黙の失敗より軽い方を取る。
- **modularity は 4 skill 中 balanced-coupling だけを誘導する** (issue #510)。`review`
  の自然な trigger「レビュー」は coding-principles が既に占めており、同じ語で 2 skill を
  指すと上記の一意性が壊れる。`design` (新規システムのモジュール設計) / `document`
  (レビュー文書の出力) は日常作業と噛み合わず、誘導しても発火機会が無い。この 3 件は
  invocation 0 のまま許容する。
- LLM 遵守依存で 100% ではない。効果検証は inventory-skill-mcp の coverage 指標
  (issue #276) で行う。

効果検証の実測 (2026-07-28 の棚卸し): 導入 (2026-07-24) 前の窓では coding-principles の
coverage が 5/229 だったのに対し、導入後 4 日窓では 8/19 (42.1%)。注入は効いているが
LLM 遵守依存のため未達も残る。coverage を再評価するときは観測窓を導入日以降に合わせること
(30 日窓のままだと導入前を混ぜて過小評価する — 実際に一度誤読した)。
"""
from __future__ import annotations

import json
import sys

# 注入文全体は 500 字上限 (test_context_is_short_not_full_skill_md が固定する希釈ガード)。
# 4 行で 499 字とほぼ飽和しており、行の追加・加筆は上限の再交渉を伴う判断として行う。
GUIDE_LINES = (
    "コード新規作成・編集・リファクタリング・レビュー着手前に "
    "`swat-skills:coding-principles` を Skill tool で invoke する",
    "テストの新規作成・編集・削除・レビュー着手前に "
    "`swat-skills:test-strategy` を Skill tool で invoke する "
    "(テストレベル選択・テストダブル・カバレッジもここ)",
    "設計・実装方針・技術選定など複数選択肢からの判断前に "
    "`swat-skills:engineering-judgment` を Skill tool で invoke する "
    "(テストに閉じた判断は test-strategy、"
    "モジュール境界は balanced-coupling を優先)",
    "モジュール / サービス境界の分割・統合の判断前に "
    "`modularity:balanced-coupling` を Skill tool で invoke する "
    "(結合度の評価もここ)",
)

HEADER = (
    "以下は思想系 skill の適用ガイドです "
    "(invoke 誘導のみ。SKILL.md 本文は必要時に reach する):"
)


def main() -> int:
    body = "\n".join(f"- {line}" for line in GUIDE_LINES)
    ctx = f"{HEADER}\n{body}"
    out = {
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": ctx,
        }
    }
    print(json.dumps(out, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
