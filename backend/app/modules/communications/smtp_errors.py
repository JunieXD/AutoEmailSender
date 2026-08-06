from __future__ import annotations

import re


_THROTTLE_MARKERS = (
    "flow over limit",
    "flow control",
    "rate limit",
    "rate-limit",
    "rate exceeded",
    "frequency limited",
    "frequency limit",
    "ip frequency limited",
    "connection frequency limited",
    "sending limit",
    "send limit",
    "sending quota",
    "send quota",
    "daily sending limit",
    "daily send limit",
    "daily sending quota",
    "daily send quota",
    "too many requests",
    "too many connections",
    "too many messages",
    "temporarily throttled",
    "throttling",
    "hl:rep",
    "hl:icc",
    "hl:ifc",
    "hl:mep",
    "mi:cel",
    "mi:dmc",
    "mi:ccl",
    "rp:drc",
    "dt:stc",
    "dt:stf",
    "发送频率",
    "发信频率",
    "发送过快",
    "请求过于频繁",
    "操作过于频繁",
    "连接数过多",
    "超过频率限制",
    "流量超限",
    "发送额度",
    "发信额度",
)
_AUTH_MARKERS = (
    "authentication failed",
    "authentication unsuccessful",
    "authentication required",
    "authenticationfailed",
    "authorization failed",
    "invalid credentials",
    "bad credentials",
    "invalid login",
    "login denied",
    "username and password not accepted",
    "password not accepted",
    "授权失败",
    "认证失败",
    "登录失败",
    "用户名或密码",
    "授权码",
)
_SENDER_MARKERS = (
    "mail from must equal authorized user",
    "sender address rejected",
    "sender not authorized",
    "not authorized to send",
    "not owned by user",
    "send as denied",
    "envelope sender",
    "dt:sum",
    "发件人地址不匹配",
    "发件人不一致",
    "无权代发",
)
_RELAY_MARKERS = (
    "relay access denied",
    "relaying denied",
    "relay not permitted",
    "unable to relay",
    "relay denied",
    "中继被拒绝",
)
_ACCOUNT_MARKERS = (
    "account disabled",
    "account suspended",
    "account locked",
    "user disabled",
    "user suspended",
    "mailbox locked",
    "user has no permission",
    "permission denied",
    "账号被禁用",
    "账号已停用",
    "账号已冻结",
    "账号被锁定",
    "没有发信权限",
)
_RECIPIENT_MARKERS = (
    "user unknown",
    "unknown user",
    "no such user",
    "unknown recipient",
    "invalid recipient",
    "recipient not found",
    "recipient address rejected",
    "mailbox unavailable",
    "address does not exist",
    "recipient does not exist",
    "收件人不存在",
    "收件地址不存在",
    "无效收件人",
)
_MAILBOX_FULL_MARKERS = (
    "mailbox full",
    "mailbox is full",
    "mailbox quota exceeded",
    "recipient quota exceeded",
    "over quota",
    "insufficient storage",
    "收件箱已满",
    "邮箱已满",
    "存储空间不足",
)
_MESSAGE_SIZE_MARKERS = (
    "message too large",
    "message size exceeds",
    "message size limit",
    "maximum message size",
    "size limit exceeded",
    "邮件过大",
    "附件过大",
    "超过邮件大小",
)
_POLICY_MARKERS = (
    "dt:spm",
    "spam detected",
    "spam message",
    "spam content",
    "suspected spam",
    "junk mail",
    "blacklisted",
    "blocked for abuse",
    "policy rejection",
    "policy violation",
    "content rejected",
    "message rejected",
    "mi:spf",
    "mi:dma",
    "spf check",
    "dmarc",
    "dkim",
    "垃圾邮件",
    "反垃圾",
    "内容被拒绝",
    "安全策略",
)
_TLS_MARKERS = (
    "starttls",
    "ssl:",
    "sslerror",
    "tls",
    "certificate verify failed",
    "wrong version number",
    "handshake failure",
    "证书验证失败",
    "加密握手",
)
_NETWORK_MARKERS = (
    "timed out",
    "timeout",
    "connection refused",
    "connection reset",
    "connection aborted",
    "network is unreachable",
    "getaddrinfo failed",
    "name or service not known",
    "nodename nor servname provided",
    "temporary failure in name resolution",
    "连接超时",
    "连接被拒绝",
    "网络不可达",
    "无法解析",
)


def explain_smtp_error(raw_error: object) -> str:
    text = str(raw_error).strip() if raw_error is not None else ""
    if not text:
        return "系统没有收到可识别的失败原因。请重试；若持续失败，请查看诊断日志。"

    normalized = text.casefold()
    if "任务缺少可发送的主题或正文" in text:
        return "邮件主题或正文为空，系统无法提交发送。请补全邮件内容后重新发送。"

    if "smtp_username_non_ascii" in normalized:
        return (
            "发件邮箱（SMTP 用户名）包含 SMTP 登录不支持的中文、全角符号或不可见字符。"
            "请完全删除发件邮箱后重新输入。"
        )

    if "smtp_password_non_ascii" in normalized:
        return (
            "邮箱授权码包含 SMTP 登录不支持的中文、全角符号或不可见字符。"
            "请从邮箱设置页面重新复制客户端授权码。"
        )

    if "smtp_login_encoding_error" in normalized:
        return (
            "本地 SMTP 登录过程发生凭据编码错误，服务商尚未返回鉴权结果。"
            "请检查发件邮箱和授权码；若持续失败，请将下方脱敏后的原始报错反馈给开发者。"
        )

    if _contains_any(normalized, _THROTTLE_MARKERS):
        return (
            "邮箱服务商可能对发件账号进行了发送限流：短时间发送过快、连接过于频繁，"
            "或阶段性/当日额度已用完。请暂停发送，稍后再重试。"
        )

    if _contains_any(normalized, _TLS_MARKERS):
        return "SMTP 加密连接可能失败。请检查端口与 SSL/TLS 方式是否匹配，以及系统证书和网络代理设置。"

    if _contains_any(normalized, _AUTH_MARKERS) or _has_status_code(
        normalized,
        "530",
        "534",
        "535",
        "5.7.8",
    ):
        return (
            "SMTP 身份验证可能失败。请确认已开启 SMTP 服务，并使用客户端授权码，"
            "而不是邮箱登录密码。"
        )

    if _contains_any(normalized, _SENDER_MARKERS):
        return "发件人地址可能与 SMTP 登录账号不一致，或当前账号没有代发权限。"

    if _contains_any(normalized, _RELAY_MARKERS):
        return "SMTP 服务器拒绝代发。可能是尚未登录、发件域不允许中继，或服务器配置不匹配。"

    if _contains_any(normalized, _ACCOUNT_MARKERS):
        return "发件账号可能已停用、冻结、锁定，或没有 SMTP 发信权限。请登录邮箱网页检查账号状态。"

    if "too many recipients" in normalized or "recipient limit" in normalized or _has_status_code(
        normalized,
        "4.5.3",
        "5.5.3",
    ):
        return "单封邮件的收件人数可能超过服务商限制。请减少收件人数量后重试。"

    if _contains_any(normalized, _MAILBOX_FULL_MARKERS) or _has_status_code(normalized, "5.2.2"):
        return "收件人的邮箱可能已满或超过存储配额，需等待对方清理邮箱后再发送。"

    if _contains_any(normalized, _MESSAGE_SIZE_MARKERS) or _has_status_code(normalized, "5.3.4"):
        return "邮件正文或附件可能超过服务商允许的大小。请缩小附件或邮件内容后重试。"

    if _contains_any(normalized, _RECIPIENT_MARKERS) or _has_status_code(normalized, "5.1.1"):
        return "收件地址可能不存在、格式不正确，或已被对方邮件服务器停用或拒收。"

    if _contains_any(normalized, _POLICY_MARKERS) or _has_status_code(normalized, "5.7.1"):
        return (
            "邮件可能被邮箱服务商的反垃圾或安全策略拦截。"
            "请检查主题、正文、链接、附件、发件人一致性及近期发送行为。"
        )

    if _contains_any(normalized, _NETWORK_MARKERS):
        return "连接 SMTP 服务器可能超时或中断。请检查网络、服务器地址和端口，或稍后重试。"

    if _has_status_code(normalized, "421", "450", "451", "452") or re.search(
        r"(?<!\d)4\.[0-9]\.[0-9](?!\d)",
        normalized,
    ):
        return "邮箱服务商可能暂时无法处理发送请求。请稍后重试；若持续出现，请联系邮箱服务商。"

    if _has_status_code(normalized, "552"):
        return "邮件大小或收件箱容量可能超过服务商限制。请缩小邮件，或请收件人检查邮箱空间。"

    return "暂未能从服务商返回内容判断具体原因。请根据原始报错检查邮箱配置和收件地址；若持续失败，请联系邮箱服务商。"


def _contains_any(text: str, markers: tuple[str, ...]) -> bool:
    return any(marker in text for marker in markers)


def _has_status_code(text: str, *codes: str) -> bool:
    return any(re.search(rf"(?<![\d.]){re.escape(code)}(?![\d.])", text) for code in codes)
