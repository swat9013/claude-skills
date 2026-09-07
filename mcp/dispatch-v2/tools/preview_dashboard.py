"""dashboard.html を browser で見るための複製を作る (light / dark)。

    python3 mcp/dispatch-v2/tools/preview_dashboard.py \
        mcp/dispatch-v2/dispatch_v2/dashboard.html \
        mcp/dispatch-v2/tools/preview-overview.json "$TMPDIR/dashboard-preview"
    open "$TMPDIR/dashboard-preview/preview-light.html"

**動いている daemon の URL を開いても、見えるのは daemon 自身の clone の画面**
(`dashboard.py` は request のたびに自分の `PAGE_PATH` を読む)。作業ツリーの変更を確かめるには
手元の file を開く必要があるが、page は `/api/overview` を fetch するので file:// では
失敗経路しか踏めない。

そこで**差し替えるのは transport だけ**にする — `fetch` を canned payload を返す関数へ置き換え、
markup / CSS / 描画 script は 1 文字も変えない。dark は `prefers-color-scheme` を CLI から
指定できないので、dark の宣言ブロックを常時適用へ書き換えた複製を別に作る。

payload は `/api/overview` の応答そのもの。同梱の `preview-overview.json` は、目視で確かめたい
状態 (打ち切り済み / 再送中の escalation・conflict の CL・数えられない未解決 thread・alive
なのに activity が未観測の Session・投影に失敗した project・長い note) を 1 枚に集めてある。
"""

import sys
from pathlib import Path

DARK_MEDIA_QUERY = "@media (prefers-color-scheme: dark) {\n    :root {"
ALWAYS_APPLIED = "@media all {\n    :root {"
NEVER_APPLIED = "@media not all {\n    :root {"


def previewed(html, payload):
    """page の script の手前に、canned payload を返す `fetch` を差し込んだ複製。"""
    stub = (
        "<script>\n"
        f"const PREVIEW_OVERVIEW = {payload};\n"
        "const fetch = async () => ({ ok: true, json: async () => PREVIEW_OVERVIEW });\n"
    )
    written = html.replace("<script>\n", stub, 1)
    if "PREVIEW_OVERVIEW" not in written:
        raise SystemExit("script の差し込みに失敗した (page の <script> 開始行が変わった?)")
    return written


def themed(html, *, media_query, color_scheme):
    """dark の宣言ブロックの効き方を固定した複製。

    **light 側も固定する** — 素の page を書き出すと、OS が dark の環境では
    `preview-light.html` も dark で開き、light を見たつもりで dark を見ることになる。
    """
    written = html.replace(DARK_MEDIA_QUERY, media_query).replace(
        "color-scheme: light dark;", f"color-scheme: {color_scheme};"
    )
    if media_query not in written:
        raise SystemExit("dark ブロックの書き換えに失敗した (media query の綴りが変わった?)")
    return written


def main(page_path, payload_path, out_dir):
    html = previewed(
        Path(page_path).read_text(encoding="utf-8"),
        Path(payload_path).read_text(encoding="utf-8"),
    )
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "preview-light.html").write_text(
        themed(html, media_query=NEVER_APPLIED, color_scheme="light"), encoding="utf-8"
    )
    (out / "preview-dark.html").write_text(
        themed(html, media_query=ALWAYS_APPLIED, color_scheme="dark"), encoding="utf-8"
    )
    print(f"{out}/preview-light.html\n{out}/preview-dark.html")


if __name__ == "__main__":
    main(*sys.argv[1:4])
