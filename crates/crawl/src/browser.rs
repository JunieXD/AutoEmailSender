//! A desktop browser supplies HTML; parsing and claim ownership stay in crawl.
use std::{future::Future, pin::Pin};

use crate::{PageTarget, store::PageStep};

pub struct RenderedPage {
    pub url: String,
    pub html: String,
}

pub trait Browser: Send + Sync {
    /// Dropping the future must close its window, including while waiting for a slot.
    fn render<'a>(
        &'a self,
        target: &'a PageTarget,
        steps: &'a [PageStep],
    ) -> Pin<Box<dyn Future<Output = Result<RenderedPage, BrowserError>> + Send + 'a>>;
}

#[derive(Debug)]
pub enum BrowserError {
    TimedOut,
    Failed(String),
}

impl BrowserError {
    pub fn message(&self) -> String {
        match self {
            Self::TimedOut => "网页在浏览器里 20 秒内没有加载完".into(),
            Self::Failed(reason) => reason.clone(),
        }
    }
}
