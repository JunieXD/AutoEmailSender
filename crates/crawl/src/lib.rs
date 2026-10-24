//! Public school pages, isolated evidence units, and the persistent page ledger.

mod email;
pub mod fetch;
mod page;
pub mod store;
mod url;

pub use page::{
    BrowserReason, EmailEvidence, EmailLocation, Link, Material, Pagination, Unit, parse,
};
pub use url::{PageTarget, PageUrl, Scope};

#[derive(Debug)]
pub enum CrawlError {
    Url,
    OutsideSite,
    Redirect,
    TooLarge,
    TimedOut,
    Status(u16),
    ContentType,
    Http(reqwest::Error),
    Parse(String),
    Worker(tokio::task::JoinError),
}

impl std::fmt::Display for CrawlError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::Url => f.write_str("invalid or non-public page URL"),
            Self::OutsideSite => f.write_str("page is outside the allowed website"),
            Self::Redirect => f.write_str("invalid or excessive redirects"),
            Self::TooLarge => f.write_str("page exceeds the size limit"),
            Self::TimedOut => f.write_str("page request timed out"),
            Self::Status(code) => write!(f, "page returned HTTP {code}"),
            Self::ContentType => f.write_str("response is not an HTML or text page"),
            Self::Http(error) => write!(f, "fetch page: {error}"),
            Self::Parse(error) => write!(f, "parse page: {error}"),
            Self::Worker(error) => write!(f, "page worker: {error}"),
        }
    }
}

impl std::error::Error for CrawlError {
    fn source(&self) -> Option<&(dyn std::error::Error + 'static)> {
        match self {
            Self::Http(e) => Some(e),
            Self::Worker(e) => Some(e),
            _ => None,
        }
    }
}

impl CrawlError {
    pub fn message(&self) -> String {
        match self {
            Self::Url => "网址无法使用。请填写公开的 http 或 https 网页地址。".into(),
            Self::OutsideSite => "页面超出了这次抓取允许访问的网站。".into(),
            Self::Redirect => "网页的跳转地址无效，或跳转次数过多。请检查原网址。".into(),
            Self::TooLarge => "网页过大，无法读取。请改用具体的名单页或导师个人页。".into(),
            Self::TimedOut => "读取网页超时。请检查网络，稍后重试。".into(),
            Self::Status(code) => {
                format!("网站返回了 HTTP {code}，请稍后重试或在浏览器中检查网址。")
            }
            Self::ContentType => "这个地址返回的不是网页，请改用名单页或导师个人页。".into(),
            Self::Http(_) => "连不上网页。请检查网络和系统代理，稍后重试。".into(),
            Self::Parse(_) | Self::Worker(_) => {
                "读取网页内容失败，请重试；一直失败时请导出日志。".into()
            }
        }
    }
}
