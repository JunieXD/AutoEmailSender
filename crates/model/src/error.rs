use std::error::Error;

/// Why calling a model provider failed. Displays in English for the log; `message` is for the user.
#[derive(Debug)]
pub enum ModelError {
    /// No answer at all: the network, the proxy, the address or its certificate.
    Unreachable {
        address: String,
        source: reqwest::Error,
    },
    TimedOut {
        address: String,
    },
    /// The provider answered with an error status, with its words.
    Refused {
        status: u16,
        reply: String,
    },
    /// The provider failed partway through its reply, in its words.
    Failed(String),
    /// The model replied without an answer, such as when it spent everything on thinking.
    NoAnswer {
        finish_reason: Option<String>,
    },
    /// The provider answered in a way that makes no sense.
    Broken(String),
}

impl std::fmt::Display for ModelError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            ModelError::Unreachable { address, source } => {
                // reqwest's own words are only "error sending request"; the cause is deeper.
                write!(f, "reach {address}")?;
                let mut next: Option<&dyn Error> = Some(source);
                while let Some(e) = next {
                    write!(f, ": {e}")?;
                    next = e.source();
                }
                Ok(())
            }
            ModelError::TimedOut { address } => write!(f, "{address} timed out"),
            ModelError::Refused { status, reply } => write!(f, "refused with {status}: {reply}"),
            ModelError::Failed(reply) => write!(f, "failed partway: {reply}"),
            ModelError::NoAnswer { finish_reason } => {
                write!(f, "no answer, finish reason {finish_reason:?}")
            }
            ModelError::Broken(e) => write!(f, "{e}"),
        }
    }
}

impl Error for ModelError {}

impl ModelError {
    /// What happened and what to do about it.
    pub fn message(&self) -> String {
        match self {
            ModelError::Unreachable { address, source } if net::untrusted(source) => {
                format!("{address} 的证书不受信任，连接可能被拦截了。换个网络再试。")
            }
            ModelError::Unreachable { address, .. } => format!(
                "连不上 {address}。检查网络是否连通；开了系统代理的，确认代理软件在运行；国内用 OpenAI、Gemini 这类国外服务要开代理。"
            ),
            ModelError::TimedOut { address } => format!(
                "{address} 迟迟没有响应。稍后再试；国内用 OpenAI、Gemini 这类国外服务要开代理。"
            ),
            ModelError::Refused { status, reply } => match status {
                401 | 403 => format!(
                    "API Key 不正确或没有权限（服务商回复：{reply}）。检查 API Key 有没有填错、是否已停用。"
                ),
                402 => format!("账户余额不足（服务商回复：{reply}）。到服务商网站充值后再试。"),
                404 => format!(
                    "服务商找不到这个模型或接口（服务商回复：{reply}）。检查模型名有没有填错；自定义的接口地址一般以 /v1 结尾。"
                ),
                429 => format!(
                    "请求太频繁或额度已用完（服务商回复：{reply}）。稍后再试，或者到服务商网站查看额度。"
                ),
                500.. => format!("服务商出错了（{status}：{reply}）。稍后再试。"),
                _ => format!(
                    "服务商拒绝了请求（{status}：{reply}）。检查模型名有没有填错，这个模型是否支持对话。"
                ),
            },
            ModelError::Failed(reply) => {
                format!("服务商中途出错了（服务商回复：{reply}）。稍后再试。")
            }
            ModelError::NoAnswer { finish_reason } => format!(
                "模型没有给出回答（结束原因：{}）。换个模型再试。",
                finish_reason.as_deref().unwrap_or("未说明")
            ),
            ModelError::Broken(e) => format!(
                "服务商的回复看不懂（{e}）。检查接口地址是不是 OpenAI 兼容接口；一直出错时，导出日志发给开发者。"
            ),
        }
    }
}
