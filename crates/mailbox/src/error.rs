use std::io;

/// Why talking to a mail server failed. Displays in English for the log; `message` is for the user.
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
    Login(String),
    /// The server turned down some other request, with its reply.
    Refused(String),
    /// The connection broke off, or the server answered in a way that makes no sense.
    Broken(String),
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
            MailError::Login(reply) => write!(f, "login refused: {reply}"),
            MailError::Refused(reply) => write!(f, "refused: {reply}"),
            MailError::Broken(e) => write!(f, "{e}"),
            MailError::Recipient(address) => write!(f, "unusable recipient {address}"),
        }
    }
}

impl std::error::Error for MailError {}

impl MailError {
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
                "系统代理 {proxy} 没能连上 {host} 的 {port} 端口（{reason}）。检查服务器地址和端口，或者换个代理节点。"
            ),
            MailError::TimedOut { host, port } => format!(
                "{host} 的 {port} 端口迟迟没有响应。检查服务器地址和端口；国内连 Gmail 这类国外邮箱要开代理。"
            ),
            MailError::Tls { host, source } if net::untrusted(source) => {
                format!("{host} 的证书不受信任，连接可能被拦截了。换个网络再试。")
            }
            MailError::Tls { host, source } => format!(
                "和 {host} 建立加密连接失败（{source}）。检查加密方式和端口是否对应：SSL/TLS 一般用 465、993 端口，STARTTLS 一般用 587、143 端口。"
            ),
            MailError::Login(reply) => format!(
                "登录被拒绝（服务器回复：{reply}）。检查授权码或密码有没有填错，邮箱设置里有没有开启 IMAP/SMTP 服务；学校和公司的邮箱还可能被管理员关闭了客户端登录，需要问管理员。"
            ),
            MailError::Refused(reply) => format!("服务器拒绝了请求（{reply}）。"),
            MailError::Broken(e) => {
                format!("和服务器通信出错（{e}）。稍后再试；一直出错时，导出日志发给开发者。")
            }
            MailError::Recipient(address) => {
                format!("收件地址 {address} 无法使用。检查导师的邮箱有没有填错。")
            }
        }
    }
}
