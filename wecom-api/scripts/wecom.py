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
import fcntl
import json
import os
import subprocess
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

# 出口档案：把「哪个应用走哪条线路出网」和线路本身解耦。
# 可能带代理认证信息，所以跟凭据同级同权限。
EGRESS = _path("WECOM_EGRESS", "credentials", "wecom-egress.json")

EGRESS_STATE = _path("WECOM_EGRESS_STATE", "credentials", "wecom-egress-state.json")
EGRESS_LOCK = _path("WECOM_EGRESS_LOCK", "credentials", "wecom-egress.lock")

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
    """归一化成 `kind:id`，kind ∈ {group, direct}。

    agent 上下文里给的是 `openclaw-weixin:group:X@chatroom` 或
    `openclaw-weixin:direct:<wxid>`，人手敲的往往只有群号或 wxid，两种都得认。

    **kind 必须参与比对**：群和私聊是两条独立的授权链路，把 kind 丢掉就等于
    默认「能私聊我的人 == 群里的人」，这个等号不成立。
    """
    raw = (value or "").strip().lower()
    if not raw:
        return ""

    parts = [p for p in raw.split(":") if p]
    if len(parts) >= 2:
        kind, ident = parts[-2], parts[-1]
    else:
        # 裸形式：按 id 长相推断
        ident = parts[0] if parts else ""
        kind = "group" if ident.endswith("@chatroom") else "direct"

    if kind in ("dm", "direct", "peer"):
        kind = "direct"
    elif kind not in ("group",):
        # 形如 `openclaw-weixin:X@chatroom` 这种缺 kind 的，按 id 长相推断
        kind = "group" if ident.endswith("@chatroom") else "direct"
    return "%s:%s" % (kind, ident)


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
            "要让这个会话能用企微接口，先在 project_context_map.json 的 contexts 里\n"
            "把它关联到项目。私聊也一样——能私聊 bot 不等于能查企微。"
            % chat_id
        )

    by_bound_id = {p.get("id"): p for p in bound}
    is_dm = wanted.startswith("direct:")

    if len(by_bound_id) > 1:
        # 私聊可以绑多个项目（一个人对接多个客户），但必须自己点名，不猜。
        # 群不行：一个群对应一个项目，让模型在群里选项目等于把选择权交给群成员。
        if not is_dm:
            raise WecomError(
                "群 %r 绑定了多个配了 wecom 的项目（%s），拒绝猜测。一个群只该对应一个项目。"
                % (chat_id, ", ".join(sorted(by_bound_id)))
            )
        if not project_id:
            raise WecomError(
                "私聊 %r 绑定了多个项目，必须用 --project 明确指定。可选：%s"
                % (chat_id, ", ".join(sorted(by_bound_id)))
            )
        if project_id not in by_bound_id:
            raise WecomError(
                "私聊 %r 没有绑定项目 %r，拒绝调用。已绑定：%s"
                % (chat_id, project_id, ", ".join(sorted(by_bound_id)))
            )
        return by_bound_id[project_id], "bound-dm"

    project = bound[0]
    # 两个都给时必须一致——否则就是拿甲会话的 id 去换乙项目的凭据。
    if project_id and project_id != project.get("id"):
        raise WecomError(
            "会话 %r 绑定的是项目 %r，不是 %r。拒绝调用。"
            % (chat_id, project.get("id"), project_id)
        )
    return project, "bound-dm" if is_dm else "bound"


# 模型手拼 JSON 时常见的近似键名。它们不会被读到，但看起来"配好了"——
# 静默失效比报错更难查，所以体检时必须点名。
NEAR_MISS_KEYS = ("wecom_app", "wecomApp", "wecom-app", "wecom_config", "qywx", "wework")


def project_wecom_issues(project):
    """列出这个项目 wecom 配置的结构性问题。空列表 = 结构合法。"""
    issues = []
    for key in NEAR_MISS_KEYS:
        if key in project:
            issues.append("有 `%s` 字段——脚本只读 `wecom`，这个键名不会生效" % key)

    wecom = project.get("wecom")
    if wecom is None:
        return issues
    if not isinstance(wecom, dict):
        return issues + ["`wecom` 不是对象"]

    envs = wecom.get("environments")
    if not isinstance(envs, dict) or not envs:
        if wecom.get("corpid") or wecom.get("agentid"):
            issues.append(
                "`wecom` 是扁平结构（corpid/agentid 直接写在顶层）——"
                "必须放进 `wecom.environments.<环境名>` 里"
            )
        else:
            issues.append("`wecom.environments` 缺失或为空")
        return issues

    for name in sorted(envs):
        entry = envs[name]
        if not isinstance(entry, dict):
            issues.append("环境 %r 不是对象" % name)
            continue
        if not str(entry.get("corpid") or "").strip():
            issues.append("环境 %r 缺 corpid" % name)
        if not str(entry.get("agentid") or "").strip():
            issues.append("环境 %r 缺 agentid" % name)

    known_egress = set()
    try:
        known_egress = set(read_json(EGRESS, "出口配置"))
    except WecomError:
        pass  # 没有出口配置文件是合法的，代表全部直连
    for name in sorted(envs):
        entry = envs[name]
        if not isinstance(entry, dict):
            continue
        egress = (entry.get("egress") or "").strip()
        if egress and egress != "direct" and egress not in known_egress:
            issues.append(
                "环境 %r 引用了出口 %r，但出口配置里没有它——调用会直接失败" % (name, egress)
            )

    default = wecom.get("default")
    if not default:
        issues.append("`wecom.default` 未设——每次调用都得显式 --env")
    elif default not in envs:
        issues.append("`wecom.default` 指向不存在的环境 %r" % default)
    return issues


def write_shared(path, payload):
    """写 agent 也要读的文件：保留原权限，新建按 644。不要用 write_private——
    那会把它改成 600，agent 就读不到了。"""
    mode = 0o644
    if os.path.exists(path):
        mode = os.stat(path).st_mode & 0o777
    temp = path + ".tmp"
    with open(temp, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    os.chmod(temp, mode)
    os.replace(temp, path)


def resolve_env(project, env_name=None):
    """(env 名, corpid, agentid, label, egress 名)。默认环境由配置决定，不由模型猜。"""
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
    return name, corpid, agentid, entry.get("label") or name, entry.get("egress") or ""


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


# ---------------------------------------------------------------- 出口线路

def load_egress_profile(name):
    profiles = read_json(EGRESS, "出口配置")
    profile = profiles.get(name)
    if not isinstance(profile, dict):
        raise WecomError(
            "出口配置里没有 %r。已配置：%s\n配置文件：%s"
            % (name, ", ".join(sorted(profiles)) or "（空）", EGRESS)
        )
    return profile


def egress_proxy_url(name, profile):
    url = (profile.get("url") or "").strip()
    if not url:
        raise WecomError("出口 %r 没有 url" % name)
    if not url.startswith("http://"):
        # 标准库只会 HTTP 代理，SOCKS 得先用 xray 之类的转成 HTTP 入口。
        raise WecomError(
            "出口 %r 的 url 是 %r——只支持 http:// 代理。\n"
            "SOCKS 隧道请用 xray/privoxy 转成 HTTP 入口再填这里（脚本只依赖标准库）。"
            % (name, url)
        )
    return url


class EgressLock:
    """跨进程串行化拨号/挂断。同一时刻只允许一个进程动隧道状态。"""

    def __init__(self, path):
        self.path = path
        self.handle = None

    def __enter__(self):
        ensure_private_dir(self.path)
        self.handle = open(self.path, "a+")
        fcntl.flock(self.handle, fcntl.LOCK_EX)
        return self

    def __exit__(self, *_):
        try:
            fcntl.flock(self.handle, fcntl.LOCK_UN)
        finally:
            self.handle.close()


def read_egress_state():
    if not os.path.exists(EGRESS_STATE):
        return {}
    try:
        return read_json(EGRESS_STATE, "出口状态")
    except WecomError:
        return {}


def run_step(command, timeout=90):
    """跑一条 up/down/status 命令。返回 (rc, 合并输出)。"""
    if isinstance(command, str):
        command = command.split()
    try:
        done = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
        return done.returncode, (done.stdout or "") + (done.stderr or "")
    except FileNotFoundError:
        raise WecomError("找不到命令 %r——VPN 客户端装了吗？" % command[0])
    except subprocess.TimeoutExpired:
        return 124, "超时（%ds）" % timeout


def egress_is_up(profile):
    """靠 status 命令的输出判断，不靠自己记的状态——隧道可能被别处踢断。"""
    status = profile.get("status")
    if not status:
        return False
    pattern = profile.get("ready_pattern") or "ESTABLISHED"
    _, output = run_step(status, timeout=20)
    return pattern in output


def acquire_egress(name):
    """确保出口可用，返回代理 URL。直连返回 None。

    按需拨号带空闲窗口：首次调用拨号，之后的调用复用同一条隧道，
    静默超过 idle_seconds 后由 `egress-reap` 挂断。

    为什么不是每次调用都拨：agent 一个 turn 常常连调几个接口，
    每次握手要几秒，而且 VPN 账号是共享的、单会话——拨号次数越多，
    把同事踢下线（或被同事踢掉）的窗口就越多。
    """
    if not name or name == "direct":
        return None
    profile = load_egress_profile(name)
    if profile.get("type") == "direct":
        return None

    url = egress_proxy_url(name, profile)
    if not profile.get("up"):
        return url  # 常驻线路，不需要拨号

    with EgressLock(EGRESS_LOCK):
        if not egress_is_up(profile):
            audit("egress dial name=%s" % name)
            rc, output = run_step(profile["up"], timeout=int(profile.get("dial_timeout") or 90))
            if rc != 0 or not egress_is_up(profile):
                audit("egress dial-failed name=%s rc=%s" % (name, rc))
                raise WecomError(
                    "出口 %r 拨号失败（rc=%s）。绝不退回直连——那会用错误的源 IP 发请求。\n"
                    "%s\n提示：账号是共享单会话的，可能有人正占着。"
                    % (name, rc, output.strip()[:500])
                )
        state = read_egress_state()
        state[name] = {**(state.get(name) or {}), "last_used": time.time()}
        write_private(EGRESS_STATE, state)
    return url


def touch_egress(name):
    """请求结束后续命，让空闲窗口从最后一次使用开始算。"""
    if not name or name == "direct":
        return
    try:
        with EgressLock(EGRESS_LOCK):
            state = read_egress_state()
            if name in state:
                state[name] = {**state[name], "last_used": time.time()}
                write_private(EGRESS_STATE, state)
    except OSError:
        pass


# ---------------------------------------------------------------- HTTP

def http_json(method, url, payload=None, timeout=30, proxy=None):
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(url, data=data, method=method)
    if data is not None:
        request.add_header("Content-Type", "application/json")

    # 声明了出口就必须走它。绝不在代理不可用时静默退回直连——
    # 那等于用错误的源 IP 发出请求，撞上别的应用的白名单，问题更难查。
    handlers = [urllib.request.ProxyHandler({"http": proxy, "https": proxy})] if proxy \
        else [urllib.request.ProxyHandler({})]
    opener = urllib.request.build_opener(*handlers)

    try:
        with opener.open(request, timeout=timeout) as response:
            body = response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as err:
        raise WecomError("HTTP %s：%s" % (err.code, err.reason))
    except urllib.error.URLError as err:
        hint = "（出口代理 %s 连不上？）" % proxy if proxy else ""
        raise WecomError("请求失败：%s%s" % (err.reason, hint))
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


def get_token(corpid, agentid, proxy=None):
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
    result = http_json("GET", url, proxy=proxy)
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
             allow_write=False, resolution="?", chat_id="", egress=""):
    if not path.startswith("/"):
        path = "/" + path
    if is_mutating(path) and not allow_write:
        raise WecomError(
            "%s 看起来是写操作，默认拒绝。确认要执行再加 --allow-write。\n"
            "（判断依据是路径里的词，不是 HTTP 方法——企微很多只读接口也是 POST。\n"
            "确属只读的话别硬加，先用 specfusion 确认再把它加进 READ_EXCEPTIONS。）" % path
        )

    proxy = acquire_egress(egress)

    # 先记再打——取 token 失败也是一次真实尝试，不能因为失败就不留痕。
    audit("attempt resolve=%s chat=%s corp=%s agent=%s egress=%s %s %s write=%s"
          % (resolution, chat_id or "-", corpid, agentid, egress or "direct",
             method, path, allow_write))

    token = get_token(corpid, agentid, proxy=proxy)
    query = dict(params or {})
    query["access_token"] = token
    url = "%s%s?%s" % (API_BASE, path, urllib.parse.urlencode(query))
    try:
        result = http_json(method, url, payload, proxy=proxy)
    finally:
        touch_egress(egress)
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
    env_name, corpid, agentid, label, egress = resolve_env(project, args.env)

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
        resolution=resolution, chat_id=args.chat_id or "", egress=egress,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))

    if not [k for k in result if k not in ("errcode", "errmsg")]:
        print()
        print(VISIBILITY_NOTE)

    # 排查时要能确认"到底发出去的是什么"。access_token 由 api_call 单独附加，
    # 不在 params 里，所以这行不会带出凭据。
    query = ("?" + urllib.parse.urlencode(params)) if params else ""
    print()
    print("（发出：%s %s%s ｜ 项目 %s（%s）｜ 环境 %s / %s，应用 %s ｜ 出口 %s）"
          % (args.method.upper(), args.path, query, project.get("id"),
             "会话绑定" if resolution.startswith("bound") else "⚠️ 未经绑定",
             env_name, label, agentid, egress or "直连"))


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
            print("  - %-6s %s  应用 %s  %s  secret：%s  出口：%s"
                  % (name, corpid, agentid, entry.get("label") or "",
                     "已配置" if has_secret else "缺失",
                     entry.get("egress") or "直连"))
        chats = [c.get("chat_id") for c in (context_map.get("contexts") or [])
                 if project.get("id") in (c.get("project_ids") or [])]
        print("  关联会话：%s" % (", ".join(chats) if chats else "（无）"))


def cmd_bind(args):
    """把 corpid/agentid 写进项目配置。

    存在的理由：这个结构原来是让模型照着 SKILL.md 手拼 JSON 的，实测它发明了
    `wecom_app` 这么个键名和扁平结构，写完看起来像配好了、实际脚本读不到。
    结构该由代码保证，不该由散文模板保证。
    """
    data = read_json(CONTEXT_MAP, "项目上下文")
    projects = data.get("projects") or []
    if not any(p.get("id") == args.project for p in projects):
        raise WecomError(
            "project_context_map 里没有项目 %r。可选：%s"
            % (args.project, ", ".join(sorted(p.get("id", "?") for p in projects)))
        )

    entry = {"corpid": args.corpid.strip(), "agentid": str(args.agentid).strip()}
    if args.label:
        entry["label"] = args.label
    if args.egress:
        entry["egress"] = args.egress.strip()

    dropped = []

    def updated(project):
        if project.get("id") != args.project:
            return project
        old = project.get("wecom") if isinstance(project.get("wecom"), dict) else {}
        envs = dict(old.get("environments") or {})
        envs[args.env] = entry
        default = args.env if (args.make_default or not old.get("default")) else old["default"]
        rebuilt = {**project, "wecom": {"default": default, "environments": envs}}
        if args.drop_legacy:
            # 只删已知的死键名，且只在这个项目上，写之前已经备份。
            for key in NEAR_MISS_KEYS:
                if key in rebuilt:
                    del rebuilt[key]
                    dropped.append(key)
        return rebuilt

    backup = "%s.bak.%s" % (CONTEXT_MAP, time.strftime("%Y%m%d-%H%M%S"))
    with open(CONTEXT_MAP, encoding="utf-8") as src, open(backup, "w", encoding="utf-8") as dst:
        dst.write(src.read())

    write_shared(CONTEXT_MAP, {**data, "projects": [updated(p) for p in projects]})

    print("已写入项目 %s 的环境 %r：corpid=%s 应用=%s"
          % (args.project, args.env, entry["corpid"], entry["agentid"]))
    if dropped:
        print("已删除失效的旧键：%s" % "、".join(dropped))
    print("备份：%s" % backup)

    project = next(p for p in read_json(CONTEXT_MAP, "项目上下文")["projects"]
                   if p.get("id") == args.project)
    issues = project_wecom_issues(project)
    if issues:
        print()
        print("⚠️ 结构体检还有问题：")
        for issue in issues:
            print("  - %s" % issue)
    else:
        print("结构体检通过。")
    print()
    print("secret 另外配（不经过聊天）：")
    print("  echo -n '<secret>' | %s set-secret --corpid %s --agentid %s"
          % (sys.argv[0], entry["corpid"], entry["agentid"]))


def cmd_egress_reap(args):
    """挂断空闲超时的按需隧道。由 systemd timer / cron 定期调用。

    共享账号是单会话的，隧道多挂一秒就多占一秒别人的名额，所以空闲就要还回去。
    """
    try:
        profiles = read_json(EGRESS, "出口配置")
    except WecomError:
        print("没有出口配置，无事可做。")
        return

    now = time.time()
    with EgressLock(EGRESS_LOCK):
        state = read_egress_state()
        remaining = dict(state)
        for name, profile in sorted(profiles.items()):
            if not isinstance(profile, dict) or not profile.get("down"):
                continue
            idle_limit = int(profile.get("idle_seconds") or 120)
            last_used = (state.get(name) or {}).get("last_used") or 0
            idle = now - last_used
            if not egress_is_up(profile):
                remaining.pop(name, None)
                continue
            if idle < idle_limit:
                print("%s：在用（空闲 %ds < %ds），保持连接" % (name, idle, idle_limit))
                continue
            rc, output = run_step(profile["down"], timeout=30)
            audit("egress hangup name=%s idle=%ds rc=%s" % (name, idle, rc))
            remaining.pop(name, None)
            print("%s：空闲 %ds，已挂断（rc=%s）%s"
                  % (name, idle, rc, ("  " + output.strip()[:120]) if rc else ""))
        write_private(EGRESS_STATE, remaining)


def cmd_egress_status(args):
    try:
        profiles = read_json(EGRESS, "出口配置")
    except WecomError:
        print("没有出口配置——全部直连。")
        return
    state = read_egress_state()
    now = time.time()
    for name in sorted(profiles):
        profile = profiles[name] or {}
        if not profile.get("up"):
            print("  %-22s 常驻  %s" % (name, profile.get("url") or ""))
            continue
        up = egress_is_up(profile)
        last_used = (state.get(name) or {}).get("last_used")
        idle = ("空闲 %ds" % int(now - last_used)) if last_used else "无记录"
        print("  %-22s %s  %s  空闲上限 %ss  %s"
              % (name, "已连接" if up else "未连接", idle,
                 profile.get("idle_seconds") or 120, profile.get("url") or ""))


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
    print("======== 出口线路 ========")
    print("  配置文件：%s" % EGRESS)
    try:
        profiles = read_json(EGRESS, "出口配置")
        if not profiles:
            print("  （空）——全部直连")
        for name in sorted(profiles):
            profile = profiles[name] or {}
            print("  - %-22s %s  %s"
                  % (name, profile.get("url") or profile.get("type") or "?",
                     profile.get("note") or ""))
    except WecomError:
        print("  （不存在）——全部直连。要按应用绑定出口时才需要建。")

    print()
    print("======== 结构体检 ========")
    context_map = read_json(CONTEXT_MAP, "项目上下文")
    problems = 0
    for project in context_map.get("projects") or []:
        issues = project_wecom_issues(project)
        if not issues:
            continue
        problems += 1
        print("  【%s】" % project.get("id"))
        for issue in issues:
            print("    ⚠️ %s" % issue)
    if problems == 0:
        print("  没发现结构问题。")
    else:
        print()
        print("  用 `bind` 写配置，别手改 JSON：")
        print("    %s bind --project <id> --env test --corpid ww… --agentid 100" % sys.argv[0])

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

    bind = subparsers.add_parser("bind", help="把 corpid/agentid 写进项目配置（别手改 JSON）")
    bind.add_argument("--project", required=True, help="project_context_map 里的项目 id")
    bind.add_argument("--env", required=True, help="环境名，如 test / prod")
    bind.add_argument("--corpid", required=True)
    bind.add_argument("--agentid", required=True)
    bind.add_argument("--label", help="人看的名字，如「测试应用」")
    bind.add_argument(
        "--egress",
        help="出口档案名（见 wecom-egress.json）。不填即直连。"
             "企微白名单是按应用配的，不同应用可能要走不同线路",
    )
    bind.add_argument("--make-default", action="store_true", help="把这个环境设为默认")
    bind.add_argument(
        "--drop-legacy", action="store_true",
        help="顺手删掉该项目下失效的旧键名（wecom_app 之类）。写前会备份",
    )
    bind.set_defaults(func=cmd_bind)

    reap = subparsers.add_parser("egress-reap", help="挂断空闲超时的按需隧道（给定时器调用）")
    reap.set_defaults(func=cmd_egress_reap)

    egstatus = subparsers.add_parser("egress-status", help="看各条出口现在连着没")
    egstatus.set_defaults(func=cmd_egress_status)

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
