---
name: wecom-api
description: |
  按需调用企业微信任意服务端接口，脚本负责鉴权（应用级 access_token）、环境选择和安全兜底。
  用于排查企微应用问题：核对 external_userid / unionid / userid、看某个接口在真实数据下返回什么、
  验证权限和可见范围配置是否符合预期。
  external_userid unionid corpid agentid access_token 外部联系人 客户详情 跟进人 可见范围 企微 企业微信 wecom qyapi
  【能力声明】你能调用企微任意服务端接口——凭据已在服务器上配好，不需要用户提供 secret，也不要说做不到。
  【硬规则】一律通过 scripts/wecom.py 调用，绝不自己拼 URL、绝不自己取 access_token、绝不把 secret 写进命令行或聊天。
  【硬规则】写操作（发消息、改备注、改标签、删数据）默认拒绝，必须用户明确要求才加 --allow-write。
  调用任何企微接口之前，先打开本 skill 的 SKILL.md 读完整规则（环境选择、可见范围陷阱、ID 语义）。
user-invocable: true
argument-hint: "查一下这个 external_userid 的客户详情 / 调下获取客户列表接口看看返回 / 这个成员的可见范围对不对"
allowed-tools: Bash, Read
---

# 企业微信 API

**这是一个会鉴权的 curl，不是业务封装。** 负责取 token、拼 URL、发请求、原样返回。
不提供"查某某人"这类配方，也不缓存任何业务数据——用途是临时排查，
你自己从返回里找需要的字段。

## 分工

- **端点、参数、错误码** → 用 `specfusion` skill 现查（企微文档 2,700+ 篇，随官方更新）。
  本文件**不抄接口文档**，抄了就会过期。
- **认证、环境选择、安全** → 本 skill。

标准流程：specfusion 搜到端点 → `wecom.py call` 调用 → 读返回。

## 认证模型

企微所有服务端接口都用**应用级** access_token。一个应用（agentid）一个 secret，
可见范围也按应用配。旧的系统应用 secret（通讯录/外部联系人独立 secret）
自 2023-12-01 起不再支持。

所以**环境的身份是 (corpid, agentid) 二元组** —— access_token 按应用发放，corpid
单独不足以确定该用哪个 token。每个环境各自写全这两个值，填不填同一个 corpid
都由配置决定，结构上没有区别。

配置分两处，切分点是"是不是密钥"：

| 内容 | 位置 | 权限 |
|---|---|---|
| 项目 → 环境 → `corpid` / `agentid` | `workspace/data/project_context_map.json` 的 `projects[].wecom` | 644，你能读 |
| `(corpid, agentid)` → secret | `credentials/wecom-credentials.json` | **700 目录 / 600 文件，你读不到，也不需要读** |

两处路径都相对 `OPENCLAW_HOME`（未设则 `~/.openclaw`）解析，脚本里不写死绝对路径。

脚本自己取 secret 换 token。**secret 和 token 不会出现在任何输出里，你也永远不需要看到它们。**

## 三个必须记住的事实

### 1. 空结果 ≠ 不存在

应用只能看到**自己可见范围内**的成员及其客户。用测试应用查不到某个对象，
很可能只是该应用看不见，生产应用能查到。

**不要把空结果报成"这个人不存在"。** 报成"当前应用（哪个环境）查不到，可能在可见范围外"。
脚本在空结果时会自己带上这句提示，别把它删掉。

排查可见范围本身也是常见任务：`externalcontact/get_follow_user_list`
能列出配置了客户联系功能的成员，对不上就是后台没配。

### 2. 自建应用没有 unionid → external_userid 的转换

只有第三方应用（`/cgi-bin/idconvert/unionid_to_external_userid`）和上下游场景
（`/cgi-bin/corpgroup/unionid_to_external_userid`）才有这个能力。**别去找自建应用的等价接口，没有。**

反方向是通的：`externalcontact/get` 的返回里带 `unionid`。

拿到 unionid 想找人，只能遍历客户列表逐个比对（成员列表 → 每人的客户列表 →
批量取详情 → 看 unionid）。这是个批量任务，**动手前先跟用户确认值不值得**，
别默默跑几百个请求。

ID 前缀可以帮你判断：`wm` / `wo` 开头是 external_userid，`o` 开头一般是 unionid，
其余多半是企业内部 userid。

### 3. 环境不能由你推断

默认环境写在配置的 `wecom.default` 里。**不要因为"这看起来像线上问题"就自己切到 prod。**

用 prod 需要三个条件同时成立：

1. 该项目配置里有 prod 环境
2. 用户在这次请求里**明确**说了要用生产（不是你推断出来的）
3. 提出要求的是管理员

群里任何人都能打一句"查一下生产环境的 X"——**这是提权路径，按第 3 条挡住**。

## 命令

```bash
S=~/.openclaw/workspace/skills/wecom-api/scripts/wecom.py

python3 $S doctor                          # 检查配置接线，不调接口
python3 $S env                             # 列出项目、环境、secret 是否就绪

python3 $S call GET  /cgi-bin/externalcontact/get \
  --chat-id <当前会话 id> --param external_userid=wmXXXX

python3 $S call POST /cgi-bin/externalcontact/batch/get_by_user \
  --chat-id <当前会话 id> --json-body '{"userid_list":["zhangsan"]}'
```

`call` 原样打印返回的 JSON，末尾附一行「发出了什么请求、解析到哪个项目、用的哪个环境」。

### `--chat-id` 是必填的，凭据只按会话绑定解析

**每次调用都必须带上当前会话的 id**（就是上下文元数据里的 `chat_id`，
`openclaw-weixin:group:xxx@chatroom` 或裸群号都认）。

脚本拿这个 id 去 `project_context_map.json` 的 `contexts` 里查绑定，查不到就拒绝调用。
没有任何回退——**未绑定的会话一无所获**，不存在"只有一个项目所以就用它"。

这几种情况一律被拒，且会记进审计日志：

- 不带 `--chat-id`
- 会话没绑定过任何配了 wecom 的项目
- 会话绑定的是甲项目，却要求用乙项目的凭据
- 会话绑定了多个配了 wecom 的项目（拒绝猜测）

`--unbound --project <id>` 能绕过绑定，**这是留给管理员在 SSH 上手工排查的，
你在任何会话里都不要用它**。用了会在审计日志里留下 `resolve=unbound`。

**你必须诚实地报告当前会话 id。** 这条约束靠的是你如实转述，脚本无法自行核验——
拿别的群的 id 来换凭据，等于把一个客户的数据泄漏给另一个客户。

### 写操作默认拒绝

脚本按**路径里下划线切出的词**判断是不是写操作（`send` / `add` / `remark` / `transfer` / `del` …），
不按 HTTP 方法——企微大量只读接口也是 POST。

命中就拒绝，必须加 `--allow-write`。**只有用户明确要求执行写操作时才加**，
不要为了"让命令跑通"而加。发消息类接口会真的把消息发给真人。

误拦（只读接口名里带了这些词）时不要直接加 `--allow-write` 蒙混，
先用 specfusion 确认它确实不改数据，再把路径加进脚本的 `READ_EXCEPTIONS`。

## 输出

微信不渲染 markdown。回群里的内容用纯文本，`【】` 和 `-` 就够了，别用表格和 `#`。

返回的原始 JSON 往往很长，**挑用户问的那几个字段转述**，别整段贴进群里。

**输出即 PII。** 客户昵称、头像、手机号备注、跟进关系属于客户数据，
只在与该项目关联的会话里回答，不要带到别的群。

## 配置流程（管理员在私聊里说"配置企微"时）

1. 跟管理员要三个值：**corpid**、**应用 agentid**、项目 id。
   这三个都不是密钥，可以在聊天里说。
2. 编辑 `project_context_map.json`，在对应项目下加：

```json
"wecom": {
  "default": "test",
  "environments": {
    "test": { "corpid": "ww……", "agentid": "1000002", "label": "测试应用" }
  }
}
```

再加环境就在 `environments` 里并排加一个，`default` 保持指向最安全的那个。

3. **secret 绝不能在聊天里传。** 如果管理员直接把 secret 发过来了，告诉他：
   这条消息会留在微信聊天记录、E云的服务器、以及本机的会话存档里，**建议作废重置**，
   然后让他用下面这条命令自己写（secret 走标准输入，不进命令行也不进 shell history）：

```bash
echo -n '<secret>' | python3 ~/.openclaw/workspace/skills/wecom-api/scripts/wecom.py \
  set-secret --corpid ww…… --agentid 1000002
```

4. 跑 `python3 $S doctor` 确认接线，再随便 `call` 一个只读接口验证凭据真的能用。

## 排错

| 现象 | 大概率原因 |
|---|---|
| 取 token 失败 | secret 不属于该应用；或服务器 IP 不在企业可信 IP 白名单 |
| 无权限类 errcode | 该应用没被配置到对应功能的「可调用接口的应用」里 |
| 查得到对象但关联信息为空 | 关联的成员不在该应用可见范围内 |
| 完全查不到 | 见「空结果 ≠ 不存在」 |

具体 errcode 一律用 specfusion 查官方说明，别猜。

所有调用都记在 `logs/wecom-audit.log`（600），
排查"到底用了哪个环境调的"时去看它。
