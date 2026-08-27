---
name: code-report
description: |
  把代码问题的诊断结论做成一张图发进微信群。用在：群里有人报了 bug / 问某个行为为什么这样，
  你已经查过关联的代码仓库、有结论了，要把「问题是什么、在哪一行、为什么是 bug、怎么修」讲清楚。
  代码 bug 排查结论 定位 复盘 分析报告 贴代码 发图 code review diagnosis
  【能力声明】你能把结论渲染成图片发到群里——微信不渲染 markdown，代码贴成文本会丢缩进和行号，
  所以结论带代码时一律走这个 skill，不要直接把代码贴进聊天框。
  【硬规则】只在你已经真的查过代码之后用。这个 skill 不负责诊断，只负责把已有结论讲清楚。
  动手前先打开本 skill 的 SKILL.md，字段有硬约束。
user-invocable: true
argument-hint: "把刚才那个 bug 的分析发到群里 / 出个诊断结论图"
allowed-tools: Bash, Read
---

# 代码诊断结论 → 图片

**这个 skill 不教你怎么调试。** 你自己查代码就行。它只解决查完之后的事：
把结论讲清楚，并且做成群里读得下去的样子。

## 为什么是图片

微信不渲染 markdown。代码贴成文本会**丢缩进、没有行号、不等宽、按手机宽度乱折行**——
读的人对不上你说的是哪一行，等于白贴。

## 用法

```bash
S=~/.openclaw/workspace/skills/code-report/scripts/render-report.py
cat > /tmp/report.json <<'JSON'
{ ... }
JSON
python3 $S < /tmp/report.json
```

它把 PNG 写到 `~/.openclaw/workspace/output/`，并把**路径打到 stdout**。
你在回复里带上那个路径，插件会自动把图当附件发出去。

回复正文只写一句话结论就够了（比如「已定位，在 list.wxml:42，图在下面」），
细节都在图里，别在聊天框里再复述一遍。

## 字段

```json
{
  "title": "一句话标题，群里一眼看懂是什么问题",
  "verdict": "found | suspected | not-found",
  "location": "pages/guest/list/index.wxml:42",
  "problem": "现象。谁在什么操作下看到了什么，预期是什么。",
  "snippet": {
    "file": "pages/guest/list/index.wxml",
    "start_line": 38,
    "lines": ["原样的每一行，不要重新缩进", "..."],
    "highlight": [42]
  },
  "why": "为什么这段代码会导致那个现象。要能被验证，不要写成猜测。",
  "fix": "可能的修法。有多个就都说，并说明各自的代价。"
}
```

`title` / `verdict` / `problem` 必填，其余按 verdict 定。

## verdict 怎么选 —— 这是唯一需要你诚实的地方

| | 什么时候用 | 脚本的要求 |
|---|---|---|
| `found` | 你**读到了**那段代码，它确实会产生这个现象 | 必须给 `location` + `snippet` + `why` |
| `suspected` | 你觉得大概率在这附近，但没有验证到闭环 | 可以给代码，`why` 里要写明还差什么没验证 |
| `not-found` | 没定位到 | **不许带 `snippet`** |

**给不出行号就不是 `found`。** 脚本会直接拒绝——「找到了但说不出在哪」在这里通不过。
这不是形式要求：开发拿着一个错的文件名去查，比拿到「没找到」浪费的时间多得多。

`suspected` 不丢人，**把 `suspected` 说成 `found` 才丢人**。

## 写 `why` 的标准

写**机制**，不写印象。

- ✅「bindtap 绑在容器上而不是 image 上，容器 flex: 1 会撑满剩余宽度，所以右侧留白也在点击区域内」
- ❌「这里的事件绑定可能有问题」

如果你写不出机制，说明还没查完——那就是 `suspected`，别硬凑成 `found`。

## 代码片段

- **原样复制**，保留原本的缩进。不要重新排版、不要省略中间行改成 `...`
- 上下多带 2-4 行，让人看得出上下文
- `highlight` 标出真正出问题的那一行（可以多行）
- 超过 40 行会被截断——**超了说明你选的范围太大**，缩小到真正相关的部分
- `file` 用**仓库内的相对路径**，不要贴 `/root/.openclaw/workspace/projects/...` 这种本机绝对路径

## 一次只讲一个问题

发现多个问题就发多张图。一张图里塞三个 bug，讨论会立刻乱掉——
群里没人知道在回哪一个。

## 别做的事

- **别在结论里写"我改好了"** —— 这个 skill 只做诊断咨询，不改代码、不提交
- **别把本机绝对路径写进图里**（`file` 用相对路径）
- **别贴 `.env`、配置文件、密钥相关的代码段**，哪怕它确实是问题所在——
  那种情况描述现象即可，具体内容私聊说
