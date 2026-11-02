use std::io;

use crate::pause::{Failure, PauseKind, classify};

/// Why talking to a mail server failed. Displays in English for the log, without the server's
/// words, which may quote the recipient or the letter; `message` is for the user.
#[derive(Debug)]
pub enum MailError {
    /// The server's name could not be looked up.
    Resolve {
        host: String,
        source: io::Error,
    },
    /// The server did not take the connection.
    Connect {
        host: String,
        port: u16,
        source: io::Error,
    },
    /// The system proxy did not take the connection.
    ProxyDown {
        proxy: String,
        source: io::Error,
    },
    /// The proxy took the connection but could not reach the server.
    ProxyFailed {
        proxy: String,
        host: String,
        port: u16,
        reason: String,
    },
    TimedOut {
        host: String,
        port: u16,
    },
    /// The encrypted connection could not be set up.
    Tls {
        host: String,
        source: io::Error,
    },
    /// The server turned down the login, with its reply.
    Login {
        reply: String,
        smtp_code: Option<u16>,
    },
    /// The server turned down some other request, with its reply.
    Refused {
        reply: String,
        smtp_code: Option<u16>,
    },
    /// The connection broke off, or the server answered in a way that makes no sense.
    Broken(String),
    /// The server answered in a way the mail library cannot read. Its own wording quotes the reply,
    /// so none is kept.
    Unreadable,
    /// An address mail cannot be sent to.
    Recipient(String),
}

impl std::fmt::Display for MailError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            MailError::Resolve { host, source } => write!(f, "look up {host}: {source}"),
            MailError::Connect { host, port, source } => {
                write!(f, "connect to {host}:{port}: {source}")
            }
            MailError::ProxyDown { proxy, source } => {
                write!(f, "connect to proxy {proxy}: {source}")
            }
            MailError::ProxyFailed {
                proxy,
                host,
                port,
                reason,
            } => write!(f, "proxy {proxy} to {host}:{port}: {reason}"),
            MailError::TimedOut { host, port } => write!(f, "{host}:{port} timed out"),
            MailError::Tls { host, source } => write!(f, "tls with {host}: {source}"),
            MailError::Login { reply, smtp_code } => {
                write!(f, "login refused")?;
                codes(f, reply, *smtp_code)
            }
            MailError::Refused { reply, smtp_code } => {
                write!(f, "refused")?;
                codes(f, reply, *smtp_code)
            }
            MailError::Broken(e) => write!(f, "{e}"),
            MailError::Unreadable => write!(f, "unreadable reply"),
            MailError::Recipient(_) => write!(f, "unusable recipient"),
        }
    }
}

/// An SMTP refusal's codes, such as ` (550 5.1.1)`; an IMAP one has none.
fn codes(f: &mut std::fmt::Formatter<'_>, reply: &str, smtp_code: Option<u16>) -> std::fmt::Result {
    let Some(code) = smtp_code else {
        return Ok(());
    };
    // Worded by lettre as "permanent error (550): 5.1.1 ...", the enhanced code first if any.
    let first = reply
        .split_once("): ")
        .and_then(|(_, words)| words.split_whitespace().next());
    let number =
        |part: &str| (1..=3).contains(&part.len()) && part.bytes().all(|b| b.is_ascii_digit());
    match first.filter(|c| c.split('.').count() == 3 && c.split('.').all(number)) {
        Some(enhanced) => write!(f, " ({code} {enhanced})"),
        None => write!(f, " ({code})"),
    }
}

impl std::error::Error for MailError {}

impl MailError {
    /// A refusal taken as one of the login.
    pub(crate) fn login(self) -> MailError {
        match self {
            MailError::Refused { reply, smtp_code } => MailError::Login { reply, smtp_code },
            e => e,
        }
    }

    /// Without the mailbox's secret, should the server quote it back: what the server says reaches
    /// the user, Agents and the mailbox's saved error.
    pub(crate) fn without(self, secret: &str) -> MailError {
        let hide = |words: String| words.replace(secret, "***");
        match self {
            MailError::Login { reply, smtp_code } => MailError::Login {
                reply: hide(reply),
                smtp_code,
            },
            MailError::Refused { reply, smtp_code } => MailError::Refused {
                reply: hide(reply),
                smtp_code,
            },
            e => e,
        }
    }

    /// What happened and what to do about it.
    pub fn message(&self) -> String {
        match self {
            MailError::Resolve { host, .. } => {
                format!("找不到服务器 {host}。检查服务器地址有没有填错，网络是否连通。")
            }
            MailError::Connect { host, port, .. } => format!(
                "连不上 {host} 的 {port} 端口。检查服务器地址和端口有没有填错，网络是否连通。"
            ),
            MailError::ProxyDown { proxy, .. } => {
                format!("连不上系统代理 {proxy}。确认代理软件在运行，或者关掉系统代理。")
            }
            MailError::ProxyFailed {
                proxy,
                host,
                port,
                reason,
            } => format!(
                "系统代理 {proxy} 没能连上 {host} 的 {port} 端口（{reason}）。\
                检查服务器地址和端口，或者换个代理节点。"
            ),
            MailError::TimedOut { host, port } => format!(
                "{host} 的 {port} 端口迟迟没有响应。检查服务器地址和端口；国内连 Gmail \
                这类国外邮箱要开代理。"
            ),
            MailError::Tls { host, source } if net::untrusted(source) => {
                format!("{host} 的证书不受信任，连接可能被拦截了。换个网络再试。")
            }
            MailError::Tls { host, source } => format!(
                "和 {host} 建立加密连接失败（{source}）。检查加密方式和端口是否对应：SSL/TLS \
                一般用 465、993 端口，STARTTLS 一般用 587、143 端口。"
            ),
            // Checking the code or trying again at once would only keep the limit on.
            MailError::Login { reply, .. } | MailError::Refused { reply, .. }
                if matches!(classify(self), Failure::Pause(PauseKind::Throttled(_))) =>
            {
                format!(
                    "服务商限制了登录或发信的频率（服务器回复：{reply}）。过一段时间再试，\
                    授权码不用改；短时间里反复尝试会让限制更久。"
                )
            }
            MailError::Login { reply, .. } => format!(
                "登录被拒绝（服务器回复：{reply}）。检查授权码或密码有没有填错，\
                邮箱设置里有没有开启 IMAP/SMTP 服务；\
                学校和公司的邮箱还可能被管理员关闭了客户端登录，需要问管理员。"
            ),
            MailError::Refused { reply, .. } => format!("服务器拒绝了请求（{reply}）。"),
            MailError::Broken(e) => {
                format!("和服务器通信出错（{e}）。稍后再试；一直出错时，导出日志发给开发者。")
            }
            MailError::Unreadable => {
                "服务器的回复看不懂。稍后再试；一直出错时，导出日志发给开发者。".into()
            }
            MailError::Recipient(address) => {
                format!("收件地址 {address} 无法使用。检查导师的邮箱有没有填错。")
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::MailError;

    #[test]
    fn a_refusal_quoting_the_recipient_logs_only_its_codes() {
        let refused = MailError::Refused {
            reply: "permanent error (550): 5.1.1 <wang@example.edu.cn>: Recipient address rejected"
                .into(),
            smtp_code: Some(550),
        };
        assert_eq!(refused.to_string(), "refused (550 5.1.1)");
        assert!(refused.message().contains("wang@example.edu.cn"));
        let unusable = MailError::Recipient("wang@example.edu.cn".into());
        assert!(!unusable.to_string().contains("wang"), "{unusable}");
    }

    #[test]
    fn a_refused_login_quoting_the_secret_does_not_pass_it_on() {
        let refused = MailError::Refused {
            reply: "permanent error (535): 5.7.8 bad secret abcd-1234".into(),
            smtp_code: Some(535),
        }
        .login()
        .without("abcd-1234");
        assert!(matches!(refused, MailError::Login { .. }), "{refused:?}");
        assert!(
            !refused.message().contains("abcd-1234"),
            "{}",
            refused.message()
        );
    }
}
