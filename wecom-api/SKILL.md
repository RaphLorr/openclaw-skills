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

#### 群和私聊的规则不一样

| | 绑定 | 选项目 |
|---|---|---|
| **群** | 一个群只能绑一个配了 wecom 的项目 | 不能选，也不接受 `--project` 覆盖 |
| **私聊** | 可以绑多个（一个人对接多个客户） | 绑了多个时**必须**用 `--project` 点名，且只能从已绑定的里选 |

群里不让选项目，是因为**在群里选项目等于把选择权交给群成员**——有人说一句
「用另一个客户的 corp 查一下」，模型就照做了。一个群对应一个项目，没有例外。

私聊可以绑多个，是因为对面是你自己，你知道自己在问哪个客户。

**私聊不是默认授权的。** 能私聊 bot（`allowFrom` 放行）只说明这个人能跟 bot 说话，
不代表能查企微。私聊要能用，它的 chat id 必须也在 `contexts` 里显式绑定过：

```json
{ "chat_id": "openclaw-weixin:direct:<wxid>", "project_ids": ["client-a", "client-b"] }
```

群和私聊是**两条独立的授权链路**，互不继承：`group:X` 和 `direct:X` 在脚本里
是不同的会话，拿群号当私聊 id 传会被拒。

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

## 出口线路（IP 白名单）

企微的可信 IP 是**按应用**配的，所以「从哪个 IP 发出去」是应用的属性。
环境里的 `egress` 字段指定走哪条线路，脚本按环境自动选，**你不需要做任何事**。

```bash
python3 $S egress-status     # 看各条线路现在连着没
```

几件你需要知道的事：

- **拨号是自动的。** 声明了按需拨号的线路，第一次调用时自动连，后续调用复用，
  静默几分钟后由定时器自动挂断。你不用也不该手动拨号。
- **首次调用会慢几秒**（IKE 握手）。同一轮里的后续调用就快了。
- **拨号失败时脚本会报错而不是改走直连。** 这是刻意的：用错误的源 IP 发请求会
  撞上别的应用的白名单，比失败更难查。看到「拨号失败」就如实转达，
  别自己去试直连。
- **VPN 账号是共享单会话的**——同一账号第二个连接会把第一个踢掉。所以偶尔会遇到
  「拨号失败，可能有人正占着」，重试一下通常就好。别为此反复重试超过两三次。

报 `errcode 60020 not allow to access from your ip` 说明这个应用需要走特定出口，
但环境没配 `egress`（或配错了）。这不是你能在会话里解决的，告诉管理员，
让他用 `bind --egress <线路名>` 配上。

## 输出

微信不渲染 markdown。回群里的内容用纯文本，`【】` 和 `-` 就够了，别用表格和 `#`。

返回的原始 JSON 往往很长，**挑用户问的那几个字段转述**，别整段贴进群里。

**输出即 PII。** 客户昵称、头像、手机号备注、跟进关系属于客户数据，
只在与该项目关联的会话里回答，不要带到别的群。

## 配置流程（管理员在私聊里说"配置企微"时）

**绝对不要手改 `project_context_map.json` 里的 wecom 结构。** 用 `bind` 命令。

手拼 JSON 出过事：曾经把配置写成了 `wecom_app` 这个键名、而且是扁平结构，
看起来配好了、实际脚本一个字都读不到——**静默失效比报错难查得多**。
结构该由命令保证，不该由你照着模板拼。

### 1. 跟管理员要三个值

**corpid**、**应用 agentid**、**项目 id**。三个都不是密钥，可以在聊天里说。

### 2. 写配置

```bash
python3 $S bind --project <项目id> --env test \
  --corpid ww…… --agentid 1000002 --label "测试应用"
```

写前自动备份，写后自动体检并把结果打给你。再加环境就再跑一次，换个 `--env` 名字。
`--make-default` 改默认环境；`--drop-legacy` 顺手清掉历史遗留的错键名。

### 3. 绑定会话

`bind` 只管项目的企微配置，**不管哪个会话能用它**。会话绑定在
`project_context_map.json` 的 `contexts` 里：

```json
{ "chat_id": "openclaw-weixin:group:xxx@chatroom", "project_ids": ["<项目id>"] }
{ "chat_id": "openclaw-weixin:direct:<wxid>",      "project_ids": ["<项目id>"] }
```

没有这条，配了 corpid 也调不通——这是有意的，见上面的授权规则。

### 4. secret 绝不能在聊天里传

如果管理员直接把 secret 发过来了，告诉他：这条消息会留在微信聊天记录、
E云的服务器、以及本机的会话存档里，**建议作废重置**，然后让他自己跑
（secret 走标准输入，不进命令行也不进 shell history）：

```bash
echo -n '<secret>' | python3 ~/.openclaw/workspace/skills/wecom-api/scripts/wecom.py \
  set-secret --corpid ww…… --agentid 1000002
```

### 5. 验收

跑 `python3 $S doctor`，必须满足两条：

- **结构体检**没有 ⚠️
- 对应环境的 **secret 显示「已配置」**

然后随便 `call` 一个只读接口，确认凭据真的能用。

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
