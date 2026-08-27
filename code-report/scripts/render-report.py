#!/usr/bin/env python3
"""把诊断结论渲染成一张图，发进微信群。

为什么是图片：微信不渲染 markdown。代码贴成文本会丢缩进、没有行号、
不等宽、还会按手机宽度乱折行——读的人根本对不上是哪一行。

为什么结构是固定字段而不是自由文本：字段本身就是那份纪律。
verdict=found 却给不出 location 和代码片段时，这里会直接报错——
「找到了但说不出在哪」在代码里就通不过，不依赖谁记得住规矩。

输入：stdin 读 JSON。输出：PNG 路径打到 stdout。
只依赖标准库 + PIL + 系统里的 google-chrome。
"""
import html
import json
import os
import subprocess
import sys
import tempfile
import time

# 只有这个目录下的图片，插件才会作为附件发出去（restricted 访问级别）。
OUT_DIR = os.environ.get("CODE_REPORT_OUT") or os.path.join(
    os.environ.get("OPENCLAW_HOME") or os.path.join(os.path.expanduser("~"), ".openclaw"),
    "workspace", "output",
)
CHROME = os.environ.get("CODE_REPORT_CHROME") or "google-chrome"

WIDTH = 880
MAX_HEIGHT = 6000
MAX_SNIPPET_LINES = 40

VERDICTS = {
    "found":     ("已定位", "#0f7b3f", "#e7f6ed"),
    "suspected": ("疑似",   "#9a6400", "#fdf3e0"),
    "not-found": ("未定位", "#8a2f2f", "#fbeaea"),
}


class ReportError(Exception):
    """预期内的输入错误，打成人话而不是堆栈。"""


def require(data, key):
    value = (data.get(key) or "").strip() if isinstance(data.get(key), str) else data.get(key)
    if not value:
        raise ReportError("缺少必填字段 %r" % key)
    return value


def validate(data):
    verdict = require(data, "verdict")
    if verdict not in VERDICTS:
        raise ReportError("verdict 只能是 %s，收到 %r" % ("/".join(VERDICTS), verdict))
    require(data, "title")
    require(data, "problem")

    snippet = data.get("snippet")
    if verdict == "found":
        # 这条是整个 skill 的核心约束：说「找到了」就必须指得出是哪一行。
        if not (data.get("location") or "").strip():
            raise ReportError("verdict=found 必须给 location（形如 pages/x/y.js:42）")
        if not snippet or not snippet.get("lines"):
            raise ReportError("verdict=found 必须给 snippet，把那段代码贴出来")
        require(data, "why")
    if verdict == "not-found" and snippet:
        raise ReportError("verdict=not-found 不该带 snippet——没定位到就没有代码可展示")
    if snippet and not str(snippet.get("file") or "").strip():
        raise ReportError("snippet 必须带 file")
    return verdict


def render_snippet(snippet):
    if not snippet:
        return ""
    lines = list(snippet.get("lines") or [])
    start = int(snippet.get("start_line") or 1)
    highlight = {int(n) for n in (snippet.get("highlight") or [])}

    truncated = len(lines) > MAX_SNIPPET_LINES
    if truncated:
        # 截断而不是缩小字号：一屏读不完的代码块，读的人只会划过去。
        lines = lines[:MAX_SNIPPET_LINES]

    rows = []
    for offset, text in enumerate(lines):
        num = start + offset
        cls = "row hl" if num in highlight else "row"
        rows.append(
            '<div class="%s"><span class="ln">%d</span><span class="code">%s</span></div>'
            % (cls, num, html.escape(text.rstrip("\n")) or "&nbsp;")
        )
    more = '<div class="more">… 还有 %d 行未展示</div>' % (
        len(snippet.get("lines") or []) - MAX_SNIPPET_LINES
    ) if truncated else ""
    return (
        '<div class="snippet"><div class="file">%s</div><div class="lines">%s</div>%s</div>'
        % (html.escape(str(snippet.get("file"))), "".join(rows), more)
    )


def section(label, body):
    if not body:
        return ""
    return '<div class="sec"><div class="label">%s</div><div class="body">%s</div></div>' % (
        html.escape(label),
        html.escape(str(body)).replace("\n", "<br>"),
    )


def build_html(data, verdict):
    word, fg, bg = VERDICTS[verdict]
    location = (data.get("location") or "").strip()
    return """<!doctype html><html lang="zh"><meta charset="utf-8"><body><div class="card">
<div class="head">
  <div class="title">%(title)s</div>
  <div class="badge">%(word)s</div>
</div>
%(loc)s
%(problem)s
%(snippet)s
%(why)s
%(fix)s
<div class="foot">%(foot)s</div>
</div>
<style>
  * { box-sizing: border-box; }
  body { margin: 0; padding: 24px; background: #eef0f3;
         font-family: "Noto Sans CJK SC", "Source Han Sans SC", sans-serif; }
  .card { width: %(w)dpx; margin: 0 auto; background: #fff; border-radius: 14px;
          padding: 26px 28px 20px; box-shadow: 0 2px 14px rgba(0,0,0,.08); }
  .head { display: flex; align-items: flex-start; gap: 12px; margin-bottom: 4px; }
  .title { font-size: 25px; font-weight: 700; color: #1a1d21; line-height: 1.35; flex: 1; }
  .badge { flex: none; font-size: 14px; font-weight: 700; padding: 5px 12px;
           border-radius: 999px; color: %(fg)s; background: %(bg)s; margin-top: 4px; }
  .loc { font-family: "DejaVu Sans Mono", monospace; font-size: 15px; color: #0b5cad;
         background: #f2f6fb; border-radius: 6px; padding: 7px 11px; margin: 14px 0 4px;
         word-break: break-all; }
  .sec { margin-top: 18px; }
  .label { font-size: 13px; font-weight: 700; color: #8b9099; letter-spacing: .06em; margin-bottom: 6px; }
  .body { font-size: 17px; line-height: 1.72; color: #24272b; }
  .snippet { margin-top: 18px; border: 1px solid #e3e6ea; border-radius: 9px; overflow: hidden; }
  .file { font-family: "DejaVu Sans Mono", monospace; font-size: 13px; color: #5c626b;
          background: #f7f8fa; padding: 8px 12px; border-bottom: 1px solid #e3e6ea; word-break: break-all; }
  .lines { background: #fbfcfd; padding: 8px 0; }
  .row { display: flex; font-family: "DejaVu Sans Mono", monospace; font-size: 14.5px; line-height: 1.62; }
  .row.hl { background: #fff4d6; }
  .row.hl .ln { color: #a06a00; font-weight: 700; }
  .ln { flex: none; width: 56px; text-align: right; padding-right: 14px; color: #a9aeb6;
        user-select: none; border-right: 1px solid #ebedf0; }
  .code { padding-left: 14px; white-space: pre-wrap; word-break: break-word; color: #1f2328; }
  .more { font-size: 13px; color: #8b9099; padding: 7px 12px; background: #f7f8fa;
          border-top: 1px solid #e3e6ea; }
  .foot { margin-top: 20px; padding-top: 12px; border-top: 1px solid #eef0f2;
          font-size: 12.5px; color: #a4a9b1; }
</style></body></html>""" % {
        "title": html.escape(str(data["title"])),
        "word": word, "fg": fg, "bg": bg, "w": WIDTH,
        "loc": ('<div class="loc">%s</div>' % html.escape(location)) if location else "",
        "problem": section("问题", data.get("problem")),
        "snippet": render_snippet(data.get("snippet")),
        "why": section("为什么是 bug", data.get("why")),
        "fix": section("可能的修法", data.get("fix")),
        "foot": html.escape(
            (data.get("footer") or "").strip()
            or "由 AI 分析生成，结论仅供参考，改动前请自行复核"
        ),
    }


def screenshot(html_text, out_path):
    with tempfile.TemporaryDirectory() as work:
        page = os.path.join(work, "report.html")
        with open(page, "w", encoding="utf-8") as fh:
            fh.write(html_text)
        cmd = [
            CHROME, "--headless", "--disable-gpu", "--no-sandbox", "--hide-scrollbars",
            "--force-device-scale-factor=2",  # 2x：手机上放大看代码不糊
            "--window-size=%d,%d" % (WIDTH + 48, MAX_HEIGHT),
            "--screenshot=%s" % out_path,
            "--user-data-dir=%s" % os.path.join(work, "profile"),
            "file://" + page,
        ]
        done = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if not os.path.exists(out_path):
            raise ReportError("Chrome 没有生成图片：%s" % (done.stderr or done.stdout)[-500:])
    trim(out_path)


def trim(path):
    """裁掉底部多余的背景。窗口高度是固定的，不裁就是一大片空白。"""
    try:
        from PIL import Image
    except ImportError:
        return  # 没有 PIL 就留着空白，不值得为此失败
    with Image.open(path) as img:
        rgb = img.convert("RGB")
        bg = rgb.getpixel((2, rgb.height - 2))
        bottom = rgb.height
        for y in range(rgb.height - 1, 0, -1):
            row = [rgb.getpixel((x, y)) for x in range(0, rgb.width, 24)]
            if any(p != bg for p in row):
                bottom = min(rgb.height, y + 26)
                break
        if bottom < rgb.height:
            rgb.crop((0, 0, rgb.width, bottom)).save(path)


def main():
    try:
        raw = sys.stdin.read()
        if not raw.strip():
            raise ReportError("标准输入是空的。用法：echo '<json>' | render-report.py")
        try:
            data = json.loads(raw)
        except ValueError as err:
            raise ReportError("输入不是合法 JSON：%s" % err)

        verdict = validate(data)
        os.makedirs(OUT_DIR, exist_ok=True)
        out = os.path.join(OUT_DIR, "code-report-%s.png" % time.strftime("%Y%m%d-%H%M%S"))
        screenshot(build_html(data, verdict), out)
        size = os.path.getsize(out)
        print(out)
        print("（%.0f KB）" % (size / 1024), file=sys.stderr)
    except ReportError as err:
        print("错误：%s" % err, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
