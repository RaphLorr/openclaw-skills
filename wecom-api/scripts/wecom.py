#!/usr/bin/env python3
"""企业微信 API 调用工具。

设计要点（改之前先读）：

- **凭据永远不经过模型。** 脚本自己从 600 权限的凭据文件里取 secret 换 token，
  secret 和 token 都不会出现在任何输出里。agent 只需要说"哪个项目、哪个环境"。
- **环境的身份是 (corpid, agentid) 二元组。** access_token 按应用发放，corpid 单独
  不足以确定该用哪个 token。两个环境填不填同一个 corpid，结构上没有区别。
- **可见范围决定结果。** 应用只能看到自己可见范围内的成员及其客户，所以空结果
  不等于对象不存在。凡是空结果都必须带上这句提示，否则会把人引向错误结论。
- **不做业务封装。** 这里只负责鉴权和转发，返回什么就原样打印什么。用途是临时排查，
  调用方自己从结果里找需要的字段——加一层"配方"只会在企微改返回结构时变成负债。
- 只依赖标准库：服务器上没有 npm，也别假设 pip 装得上东西。
"""
import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

API_BASE = "https://qyapi.weixin.qq.com"

# 跟 OpenClaw 自己的推导保持一致：认 OPENCLAW_HOME，没有就退回 ~/.openclaw。
# 不写死 /root——跑在别的账号下、或者本地测试时都不该改代码。
OPENCLAW_HOME = os.environ.get("OPENCLAW_HOME") or os.path.join(
    os.path.expanduser("~"), ".openclaw"
)


def _path(env_name, *parts):
    return os.environ.get(env_name) or os.path.join(OPENCLAW_HOME, *parts)


# 项目绑定：非密钥，agent 要能读，所以在 workspace 里。
CONTEXT_MAP = _path("WECOM_CONTEXT_MAP", "workspace", "data", "project_context_map.json")

# 密钥和 token：放 OpenClaw 的 credentials 目录（700），跟 feishu-* 那几个同一个惯例。
#
# 不放 skill 目录：`openclaw skills install --force` 会整个覆盖它，更新一次丢一次。
# 不放 plugin-data/wechat：那是 wechat 插件的私有命名空间，skill 不该往里塞东西。
CREDENTIALS = _path("WECOM_CREDENTIALS", "credentials", "wecom-credentials.json")
TOKEN_CACHE = _path("WECOM_TOKEN_CACHE", "credentials", "wecom-token-cache.json")

# 审计日志不是密钥，丢了也不影响功能，放 logs（700）。
AUDIT_LOG = _path("WECOM_AUDIT_LOG", "logs", "wecom-audit.log")

VISIBILITY_NOTE = (
    "注意：应用只能看到自己可见范围内的成员及其客户。空结果不代表对象不存在，"
    "可能只是当前应用看不见。换一个环境（--env）再试，或确认该应用的可见范围。"
)

# 路径里出现这些词就当成写操作，必须显式 --allow-write。
# 不按 HTTP 方法判断：企微大量只读接口也是 POST。
#
# 按「下划线切出的词」匹配，不按片段前缀——前缀匹配会漏掉 onjob_transfer 这类
# 把动词放在后面的命名。remark 这种本身就是动词的也必须单独列出。
MUTATING_WORDS = (
    "send", "add", "create", "update", "edit", "del", "set", "mark", "remark",
    "invite", "close", "cancel", "transfer", "import", "reset", "upload",
    "modify", "submit", "bind", "unbind", "join", "quit", "kick", "clear",
)

# 少数只读接口名里带上面的词，明确豁免，否则会被误拦。
# 加之前先用 specfusion 确认它真的不改数据。
READ_EXCEPTIONS = frozenset({
    "/cgi-bin/externalcontact/resigned/transfer_result",
    "/cgi-bin/externalcontact/groupchat/onjob_transfer_result",
})


class WecomError(Exception):
    """预期内的失败，打印成人话而不是堆栈。"""


# ---------------------------------------------------------------- 配置读取

def read_json(path, what):
    if not os.path.exists(path):
        raise WecomError("找不到%s：%s" % (what, path))
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    except ValueError as err:
        raise WecomError("%s 不是合法 JSON（%s）：%s" % (what, path, err))


def normalize_chat_id(value):
    """归一化会话 id：`openclaw-weixin:group:X@chatroom` 和裸 `X@chatroom` 视为同一个。

    agent 上下文里给的是全限定形式，人手敲的往往只有群号，两种都得认。
    """
    raw = (value or "").strip()
    if not raw:
        return ""
    return raw.rsplit(":", 1)[-1].lower()


def find_project(context_map, project_id=None, chat_id=None, unbound=False):
    """把会话解析成项目。默认 fail-closed：只认绑定关系。

    绝不按项目名直接取——那样 A 群里报一个别的项目名就能拿到别人的凭据。
    也绝不"只有一个项目就默认用它"——未绑定的会话必须一无所获。

    `unbound=True` 是给运维在 SSH 上手工排查用的后门，会被记进审计日志。
    群聊路径永远不该走到它。
    """
    projects = context_map.get("projects") or []
    by_id = {p.get("id"): p for p in projects if p.get("id")}

    if unbound:
        if not project_id:
            raise WecomError("--unbound 必须同时给 --project")
        project = by_id.get(project_id)
        if not project:
            raise WecomError("project_context_map 里没有项目 %r" % project_id)
        if not project.get("wecom"):
            raise WecomError("项目 %r 没有配 wecom" % project_id)
        return project, "unbound"

    if not chat_id:
        raise WecomError(
            "必须用 --chat-id 指定会话，凭据只按「会话 → 项目」的绑定关系解析。\n"
            "（运维手工排查可以用 --unbound --project <id> 绕过，会记进审计日志；"
            "群聊里不要用。）"
        )

    wanted = normalize_chat_id(chat_id)
    bound = []
    for context in context_map.get("contexts") or []:
        if normalize_chat_id(context.get("chat_id")) != wanted:
            continue
        for candidate in context.get("project_ids") or []:
            project = by_id.get(candidate)
            if project and project.get("wecom"):
                bound.append(project)

    if not bound:
        raise WecomError(
            "会话 %r 没有绑定任何配了 wecom 的项目，拒绝调用。\n"
            "要让这个会话能用企微接口，先在 project_context_map.json 里把它关联到项目。"
            % chat_id
        )
    if len({p.get("id") for p in bound}) > 1:
        raise WecomError(
            "会话 %r 绑定了多个配了 wecom 的项目（%s），拒绝猜测。请拆分绑定。"
            % (chat_id, ", ".join(sorted(p.get("id", "?") for p in bound)))
        )

    project = bound[0]
    # 两个都给时必须一致——否则就是拿甲会话的 id 去换乙项目的凭据。
    if project_id and project_id != project.get("id"):
        raise WecomError(
            "会话 %r 绑定的是项目 %r，不是 %r。拒绝调用。"
            % (chat_id, project.get("id"), project_id)
        )
    return project, "bound"


def resolve_env(project, env_name=None):
    """(env 名, corpid, agentid, label)。默认环境由配置决定，不由模型猜。"""
    wecom = project.get("wecom") or {}
    environments = wecom.get("environments") or {}
    if not environments:
        raise WecomError("项目 %r 没有配 wecom.environments" % project.get("id"))

    name = env_name or wecom.get("default")
    if not name:
        raise WecomError(
            "项目 %r 没有设 wecom.default，必须显式 --env。可选：%s"
            % (project.get("id"), ", ".join(sorted(environments)))
        )
    entry = environments.get(name)
    if not entry:
        raise WecomError(
            "项目 %r 没有环境 %r。可选：%s"
            % (project.get("id"), name, ", ".join(sorted(environments)))
        )

    corpid = (entry.get("corpid") or "").strip()
    agentid = str(entry.get("agentid") or "").strip()
    if not corpid or not agentid:
        raise WecomError("环境 %r 缺 corpid 或 agentid" % name)
    return name, corpid, agentid, entry.get("label") or name


def load_secret(corpid, agentid):
    creds = read_json(CREDENTIALS, "凭据文件")
    secret = ((creds.get(corpid) or {}).get("apps") or {}).get(agentid)
    if not secret:
        raise WecomError(
            "凭据文件里没有 %s / 应用 %s 的 secret。用下面这条命令补上（secret 从标准输入读，"
            "不会出现在命令行或日志里）：\n"
            "  echo -n '<secret>' | %s set-secret --corpid %s --agentid %s"
            % (corpid, agentid, sys.argv[0], corpid, agentid)
        )
    return secret


# ---------------------------------------------------------------- HTTP

def http_json(method, url, payload=None, timeout=30):
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(url, data=data, method=method)
    if data is not None:
        request.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as err:
        raise WecomError("HTTP %s：%s" % (err.code, err.reason))
    except urllib.error.URLError as err:
        raise WecomError("请求失败：%s" % err.reason)
    try:
        return json.loads(body)
    except ValueError:
        raise WecomError("返回不是 JSON：%s" % body[:400])


def ensure_private_dir(path):
    """目录也要 700——600 的文件放在可列出的目录里，等于告诉别人这儿有凭据。"""
    directory = os.path.dirname(path)
    if not directory:
        return
    existed = os.path.isdir(directory)
    os.makedirs(directory, exist_ok=True)
    if not existed:
        os.chmod(directory, 0o700)


def write_private(path, payload):
    ensure_private_dir(path)
    temp = path + ".tmp"
    with open(temp, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
    os.chmod(temp, 0o600)
    os.replace(temp, path)


def get_token(corpid, agentid):
    """带缓存的 access_token。缓存文件 600，提前 5 分钟过期以免边界失败。"""
    key = "%s:%s" % (corpid, agentid)
    cache = {}
    if os.path.exists(TOKEN_CACHE):
        try:
            cache = read_json(TOKEN_CACHE, "token 缓存")
        except WecomError:
            cache = {}  # 缓存坏了就重取，不是致命错误

    entry = cache.get(key) or {}
    if entry.get("token") and entry.get("expires_at", 0) > time.time() + 300:
        return entry["token"]

    secret = load_secret(corpid, agentid)
    url = "%s/cgi-bin/gettoken?%s" % (
        API_BASE,
        urllib.parse.urlencode({"corpid": corpid, "corpsecret": secret}),
    )
    result = http_json("GET", url)
    if result.get("errcode"):
        raise WecomError(
            "取 token 失败：errcode=%s errmsg=%s（检查 secret 是否属于该应用、"
            "以及服务器 IP 是否在企业可信 IP 白名单里）"
            % (result.get("errcode"), result.get("errmsg"))
        )
    token = result.get("access_token")
    if not token:
        raise WecomError("取 token 返回里没有 access_token：%s" % json.dumps(result)[:300])

    cache[key] = {
        "token": token,
        "expires_at": time.time() + int(result.get("expires_in") or 7200),
    }
    write_private(TOKEN_CACHE, cache)
    return token


def audit(line):
    try:
        ensure_private_dir(AUDIT_LOG)
        with open(AUDIT_LOG, "a", encoding="utf-8") as handle:
            handle.write("%s\t%s\n" % (time.strftime("%Y-%m-%dT%H:%M:%S%z"), line))
        os.chmod(AUDIT_LOG, 0o600)
    except OSError:
        pass  # 审计写不进去不该挡住调用


def is_mutating(path):
    """尽力而为的写操作识别。宁可误拦，不可漏放。

    误拦的代价是用户查一下文档再加 --allow-write；漏放的代价是真的给客户发了消息。
    所以词表只增不减，拿不准的一律留在里面。
    """
    clean = "/" + path.split("?")[0].strip("/")
    if clean in READ_EXCEPTIONS:
        return False
    for segment in clean.strip("/").split("/"):
        for token in segment.split("_"):
            if any(token.startswith(word) for word in MUTATING_WORDS):
                return True
    return False


def api_call(corpid, agentid, method, path, params=None, payload=None,
             allow_write=False, resolution="?", chat_id=""):
    if not path.startswith("/"):
        path = "/" + path
    if is_mutating(path) and not allow_write:
        raise WecomError(
            "%s 看起来是写操作，默认拒绝。确认要执行再加 --allow-write。\n"
            "（判断依据是路径里的词，不是 HTTP 方法——企微很多只读接口也是 POST。\n"
            "确属只读的话别硬加，先用 specfusion 确认再把它加进 READ_EXCEPTIONS。）" % path
        )

    # 先记再打——取 token 失败也是一次真实尝试，不能因为失败就不留痕。
    audit("attempt resolve=%s chat=%s corp=%s agent=%s %s %s write=%s"
          % (resolution, chat_id or "-", corpid, agentid, method, path, allow_write))

    token = get_token(corpid, agentid)
    query = dict(params or {})
    query["access_token"] = token
    url = "%s%s?%s" % (API_BASE, path, urllib.parse.urlencode(query))
    result = http_json(method, url, payload)
    if result.get("errcode"):
        raise WecomError(
            "接口返回错误：errcode=%s errmsg=%s\n路径：%s\n"
            "常见原因：该应用没有对应权限、对象不在可见范围内、或参数不对。"
            "用 specfusion 查这个 errcode 的官方说明。"
            % (result.get("errcode"), result.get("errmsg"), path)
        )
    return result


# ---------------------------------------------------------------- 子命令

def cmd_call(args):
    project, resolution = find_project(
        read_json(CONTEXT_MAP, "项目上下文"), args.project, args.chat_id, args.unbound
    )
    env_name, corpid, agentid, label = resolve_env(project, args.env)

    params = {}
    for item in args.param or []:
        if "=" not in item:
            raise WecomError("--param 要写成 key=value，收到 %r" % item)
        key, value = item.split("=", 1)
        params[key] = value

    payload = None
    if args.json_body:
        try:
            payload = json.loads(args.json_body)
        except ValueError as err:
            raise WecomError("--json-body 不是合法 JSON：%s" % err)

    result = api_call(
        corpid, agentid, args.method.upper(), args.path,
        params=params, payload=payload, allow_write=args.allow_write,
        resolution=resolution, chat_id=args.chat_id or "",
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))

    if not [k for k in result if k not in ("errcode", "errmsg")]:
        print()
        print(VISIBILITY_NOTE)

    # 排查时要能确认"到底发出去的是什么"。access_token 由 api_call 单独附加，
    # 不在 params 里，所以这行不会带出凭据。
    query = ("?" + urllib.parse.urlencode(params)) if params else ""
    print()
    print("（发出：%s %s%s ｜ 项目 %s（%s）｜ 环境 %s / %s，应用 %s）"
          % (args.method.upper(), args.path, query, project.get("id"),
             "会话绑定" if resolution == "bound" else "⚠️ 未经绑定",
             env_name, label, agentid))


def cmd_env(args):
    context_map = read_json(CONTEXT_MAP, "项目上下文")
    projects = [p for p in (context_map.get("projects") or []) if p.get("wecom")]
    if not projects:
        print("还没有任何项目配置 wecom。")
        return
    for project in projects:
        wecom = project["wecom"]
        print("【%s】%s" % (project.get("id"), project.get("name") or ""))
        print("  默认环境：%s" % (wecom.get("default") or "（未设，必须显式 --env）"))
        for name, entry in sorted((wecom.get("environments") or {}).items()):
            corpid = entry.get("corpid", "?")
            agentid = str(entry.get("agentid", "?"))
            has_secret = False
            try:
                load_secret(corpid, agentid)
                has_secret = True
            except WecomError:
                pass
            print("  - %-6s %s  应用 %s  %s  secret：%s"
                  % (name, corpid, agentid, entry.get("label") or "",
                     "已配置" if has_secret else "缺失"))
        chats = [c.get("chat_id") for c in (context_map.get("contexts") or [])
                 if project.get("id") in (c.get("project_ids") or [])]
        print("  关联会话：%s" % (", ".join(chats) if chats else "（无）"))


def cmd_set_secret(args):
    """secret 从标准输入读，不走命令行参数——argv 会进 ps 和 shell history。"""
    secret = sys.stdin.read().strip()
    if not secret:
        raise WecomError("标准输入是空的。用法：echo -n '<secret>' | wecom.py set-secret ...")

    creds = {}
    if os.path.exists(CREDENTIALS):
        creds = read_json(CREDENTIALS, "凭据文件")

    corp = creds.setdefault(args.corpid, {})
    apps = corp.setdefault("apps", {})
    replaced = args.agentid in apps
    apps[args.agentid] = secret
    write_private(CREDENTIALS, creds)

    print("%s %s / 应用 %s 的 secret（%d 字符，已写入 %s，权限 600）"
          % ("已覆盖" if replaced else "已写入", args.corpid, args.agentid,
             len(secret), CREDENTIALS))
    if replaced and os.path.exists(TOKEN_CACHE):
        os.remove(TOKEN_CACHE)
        print("secret 变了，已清空 token 缓存。")


def cmd_doctor(args):
    print("配置文件：%s  %s" % (CONTEXT_MAP, "存在" if os.path.exists(CONTEXT_MAP) else "缺失"))
    print("凭据文件：%s  %s" % (CREDENTIALS, "存在" if os.path.exists(CREDENTIALS) else "缺失"))
    if os.path.exists(CREDENTIALS):
        mode = oct(os.stat(CREDENTIALS).st_mode & 0o777)
        print("  权限：%s %s" % (mode, "✅" if mode == "0o600" else "⚠️ 应该是 600"))
    print("token 缓存：%s  %s" % (TOKEN_CACHE, "存在" if os.path.exists(TOKEN_CACHE) else "尚未生成"))
    print()
    cmd_env(args)
    print()
    print("以上只检查接线，没有调用任何企微接口。要验证凭据可用，随便 call 一个只读接口。")


# ---------------------------------------------------------------- 入口

def build_parser():
    parser = argparse.ArgumentParser(
        prog="wecom.py", description="企业微信 API 调用工具（凭据不经过模型）"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    def add_common(sub):
        sub.add_argument("--project", help="project_context_map 里的项目 id")
        sub.add_argument("--chat-id", help="会话 id（默认且唯一的解析依据）")
        sub.add_argument("--env", help="环境名（test / prod…）。不传则用配置里的 default")
        sub.add_argument(
            "--unbound", action="store_true",
            help="跳过会话绑定，直接按 --project 取凭据。仅限运维手工排查，会记进审计日志",
        )

    call = subparsers.add_parser("call", help="调任意接口（端点先用 specfusion 查）")
    call.add_argument("method", help="GET / POST")
    call.add_argument("path", help="如 /cgi-bin/externalcontact/get")
    call.add_argument("--param", action="append", help="查询参数 key=value，可重复")
    call.add_argument("--json-body", help="POST 的 JSON 请求体")
    call.add_argument("--allow-write", action="store_true", help="放行写操作")
    add_common(call)
    call.set_defaults(func=cmd_call)

    env = subparsers.add_parser("env", help="列出已配置的环境")
    env.set_defaults(func=cmd_env)

    doctor = subparsers.add_parser("doctor", help="检查配置接线，不调接口")
    doctor.set_defaults(func=cmd_doctor)

    set_secret = subparsers.add_parser("set-secret", help="从标准输入写入应用 secret")
    set_secret.add_argument("--corpid", required=True)
    set_secret.add_argument("--agentid", required=True)
    set_secret.set_defaults(func=cmd_set_secret)

    return parser


def main():
    args = build_parser().parse_args()
    try:
        args.func(args)
    except WecomError as err:
        # 拒绝本身就是安全事件——越权尝试被拦下时，日志里必须看得到。
        # 只记标识性参数，不记 --json-body（可能带业务数据）。
        audit("denied cmd=%s chat=%s project=%s unbound=%s path=%s :: %s"
              % (getattr(args, "command", "?"), getattr(args, "chat_id", "") or "-",
                 getattr(args, "project", "") or "-", getattr(args, "unbound", False),
                 getattr(args, "path", "") or "-", str(err).split("\n")[0]))
        print("错误：%s" % err, file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())
