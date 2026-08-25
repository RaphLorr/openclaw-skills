# OpenClaw Agent Skills

我给自己的 [OpenClaw](https://github.com/openclaw/openclaw) agent 写的 skill。这个 agent 接在一个微信 bot 上，日常处理建票、提醒、身份解析这类事。

只收录自己写的 skill，从 [clawhub](https://clawhub.ai) 安装的通用 skill 和第三方 skill 不在这里。

## 结构

每个目录是一个 skill，遵循 SKILL.md 开放格式：YAML frontmatter（`name` + `description`）加 Markdown 正文。`description` 会被注入 agent 的系统提示当作目录，正文由 agent 按需读取（progressive disclosure）。

| Skill | 用途 |
| --- | --- |
| `tapd` | TAPD 缺陷 / 需求管理 —— 建票、查重、关联迭代、处理人解析、图片附件、描述排版 |
| `contact-map-manager` | 维护本地身份映射（微信昵称 ↔ 企微姓名 ↔ 邮箱 ↔ 项目归属） |
| `reminder` | 定时提醒 |
| `wecom-api` | 企业微信服务端接口 —— 查外部联系人身份与跟进关系，按需调用任意原生接口 |

## 踩过的两个坑

**1. `description` 放触发条件和能力声明，正文放怎么做。**

一度以为"正文不会被加载，硬规则必须塞进 `description`"，于是往 `description` 里堆流程。实测下来是错的：真正调用过 TAPD 写工具的会话 9/9 都读了正文，progressive disclosure 工作得很好。真正起作用的是 `description` 末尾那句「动手前先读 SKILL.md」。

但有个例外值得留意 —— 如果模型可能**以为自己做不到**某件事，那条能力声明必须进 `description`，否则它压根不会去读正文。「能不能做」进目录，「怎么做」进正文。

**2. skill 里写的工具名，一定要跟 `tools/list` 对过。**

`tapd` skill 曾指示 agent 调用 COS 的 `upload_file` 上传截图 —— 而这个工具名在该 MCP server 的任何版本里都不存在（真名是 `putObject`）。模型找不到就回一句"做不到"，不报错、不重试，排查起来很费劲。写错工具名是静默失败。
