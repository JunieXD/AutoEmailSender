use std::{collections::BTreeMap, sync::Arc, time::Duration};

use encoding_rs::{Encoding, GBK};
use reqwest::{Client, StatusCode, header};
use scraper::{ElementRef, Html};
use tokio::{
    sync::{Mutex, Semaphore, SemaphorePermit},
    time::{Instant, sleep_until, timeout},
};

use crate::{BrowserReason, CrawlError, Material, PageTarget, PageUrl};

pub const MAX_PAGE_BYTES: usize = 4 * 1024 * 1024;
const REQUEST_TIMEOUT: Duration = Duration::from_secs(20);
const PAGE_TIMEOUT: Duration = Duration::from_secs(40);
const REQUEST_INTERVAL: Duration = Duration::from_millis(300);
const MAX_REDIRECTS: usize = 5;

struct SiteLimit {
    slots: Semaphore,
    next: Mutex<Instant>,
}

impl SiteLimit {
    async fn start(&self) -> Result<SemaphorePermit<'_>, CrawlError> {
        let slot = self
            .slots
            .acquire()
            .await
            .map_err(|e| CrawlError::Parse(e.to_string()))?;
        let mut next = self.next.lock().await;
        sleep_until(*next).await;
        *next = Instant::now() + REQUEST_INTERVAL;
        Ok(slot)
    }
}

/// Share one instance across crawl jobs so the limit applies to all requests to a website.
pub struct Fetcher {
    trusted: Client,
    untrusted: Client,
    sites: Mutex<BTreeMap<String, Arc<SiteLimit>>>,
}

#[derive(Debug)]
pub struct FetchedPage {
    pub material: Material,
    pub status: u16,
    pub relaxed_certificate: bool,
}

impl Fetcher {
    pub fn new(net: &net::Net) -> Result<Self, CrawlError> {
        Ok(Self {
            trusted: net.page_http_builder().build().map_err(CrawlError::Http)?,
            untrusted: net
                .untrusted_page_http_builder()
                .build()
                .map_err(CrawlError::Http)?,
            sites: Mutex::new(BTreeMap::new()),
        })
    }

    async fn site(&self, url: &PageUrl) -> Arc<SiteLimit> {
        let mut sites = self.sites.lock().await;
        let now = Instant::now();
        // Idle sites expire after the spacing window; visiting many profile domains does not
        // turn this limiter into an ever-growing history of visited pages.
        sites.retain(|_, limit| {
            Arc::strong_count(limit) > 1 || limit.next.try_lock().map_or(true, |next| *next > now)
        });
        let site = sites.entry(url.website()).or_insert_with(|| {
            Arc::new(SiteLimit {
                slots: Semaphore::new(2),
                next: Mutex::new(now),
            })
        });
        site.clone()
    }

    pub async fn fetch(&self, target: &PageTarget) -> Result<FetchedPage, CrawlError> {
        timeout(PAGE_TIMEOUT, self.read(target))
            .await
            .map_err(|_| CrawlError::TimedOut)?
    }

    async fn read(&self, target: &PageTarget) -> Result<FetchedPage, CrawlError> {
        let site = self.site(target.url()).await;
        let mut url = target.url.clone();
        let mut relaxed_certificate = false;
        for redirects in 0..=MAX_REDIRECTS {
            let (response, slot) = match self.send(&self.trusted, &url, &site).await {
                Err(CrawlError::Http(error)) if net::untrusted(&error) => {
                    tracing::debug!(website = %url.website(), "retry public page with relaxed certificate checks");
                    relaxed_certificate = true;
                    self.send(&self.untrusted, &url, &site).await?
                }
                result => result?,
            };
            let status = response.status();
            if matches!(status.as_u16(), 301 | 302 | 303 | 307 | 308) {
                if redirects == MAX_REDIRECTS {
                    return Err(CrawlError::Redirect);
                }
                let location = response
                    .headers()
                    .get(header::LOCATION)
                    .and_then(|h| h.to_str().ok())
                    .ok_or(CrawlError::Redirect)?;
                url = target.redirect(&url, location)?;
                continue;
            }
            if response
                .content_length()
                .is_some_and(|n| n > MAX_PAGE_BYTES as u64)
            {
                return Err(CrawlError::TooLarge);
            }
            let content_type = response
                .headers()
                .get(header::CONTENT_TYPE)
                .and_then(|h| h.to_str().ok())
                .map(str::to_owned);
            let mut response = response;
            let mut bytes = Vec::new();
            while let Some(chunk) = response.chunk().await.map_err(CrawlError::Http)? {
                if bytes.len() + chunk.len() > MAX_PAGE_BYTES {
                    return Err(CrawlError::TooLarge);
                }
                bytes.extend_from_slice(&chunk);
            }
            drop(slot);
            let url = url.clone();
            let material = tokio::task::spawn_blocking(move || {
                response_material(&url, status.as_u16(), content_type.as_deref(), &bytes)
            })
            .await
            .map_err(CrawlError::Worker)??;
            return Ok(FetchedPage {
                material,
                status: status.as_u16(),
                relaxed_certificate,
            });
        }
        Err(CrawlError::Redirect)
    }

    async fn send<'a>(
        &self,
        client: &Client,
        url: &PageUrl,
        site: &'a SiteLimit,
    ) -> Result<(reqwest::Response, SemaphorePermit<'a>), CrawlError> {
        let slot = site.start().await?;
        let response = client
            .get(url.as_str())
            .header(
                header::ACCEPT,
                "text/html, application/xhtml+xml, text/plain",
            )
            .timeout(REQUEST_TIMEOUT)
            .send()
            .await
            .map_err(|e| {
                if e.is_timeout() {
                    CrawlError::TimedOut
                } else {
                    CrawlError::Http(e)
                }
            })?;
        Ok((response, slot))
    }
}

#[cfg(test)]
mod tests;

fn charset(value: &str) -> Option<&'static Encoding> {
    let value = value.to_ascii_lowercase();
    let (_, tail) = value.split_once("charset")?;
    let tail = tail
        .trim_start()
        .strip_prefix('=')?
        .trim_start()
        .trim_start_matches(['\'', '"']);
    let label = tail
        .split(['\'', '"', ';', '>', ' ', '\t', '\r', '\n'])
        .next()?;
    Encoding::for_label(label.as_bytes())
}

/// BOM, HTTP header, HTML declaration, then UTF-8 or the common undeclared Chinese encoding.
pub fn decode(bytes: &[u8], content_type: Option<&str>) -> Result<String, CrawlError> {
    if bytes.len() > MAX_PAGE_BYTES {
        return Err(CrawlError::TooLarge);
    }
    if let Some((encoding, _)) = Encoding::for_bom(bytes) {
        return Ok(encoding.decode(bytes).0.into_owned());
    }
    let declared = content_type.and_then(charset).or_else(|| {
        let prefix = String::from_utf8_lossy(&bytes[..bytes.len().min(8192)]);
        let document = Html::parse_document(&prefix);
        document
            .root_element()
            .descendants()
            .filter_map(ElementRef::wrap)
            .filter(|e| e.value().name() == "meta")
            .find_map(|meta| {
                meta.value()
                    .attr("charset")
                    .and_then(|s| Encoding::for_label(s.as_bytes()))
                    .or_else(|| meta.value().attr("content").and_then(charset))
            })
    });
    if let Some(encoding) = declared {
        return Ok(encoding.decode(bytes).0.into_owned());
    }
    match std::str::from_utf8(bytes) {
        Ok(text) => Ok(text.into()),
        Err(_) => Ok(GBK.decode(bytes).0.into_owned()),
    }
}

/// The same boundary is used for fixtures and network responses.
pub fn response_material(
    url: &PageUrl,
    status: u16,
    content_type: Option<&str>,
    bytes: &[u8],
) -> Result<Material, CrawlError> {
    let html = decode(bytes, content_type)?;
    let lower = html.to_ascii_lowercase();
    let challenge = status == 412
        || (status == 202 && lower.contains("<script"))
        || ([403, 429].contains(&status)
            && ["challenge", "captcha", "javascript", "验证"]
                .iter()
                .any(|s| lower.contains(s)));
    if !challenge && !StatusCode::from_u16(status).is_ok_and(|s| s.is_success()) {
        return Err(CrawlError::Status(status));
    }
    let mime = content_type
        .and_then(|s| s.split(';').next())
        .map(|s| s.trim().to_ascii_lowercase());
    let plain = mime.as_deref() == Some("text/plain");
    if !challenge
        && mime
            .as_deref()
            .is_some_and(|m| !matches!(m, "text/html" | "application/xhtml+xml" | "text/plain"))
    {
        return Err(CrawlError::ContentType);
    }
    if mime.is_none()
        && !challenge
        && !["<html", "<!doctype", "<head", "<body", "<meta"]
            .iter()
            .any(|s| lower.contains(s))
    {
        return Err(CrawlError::ContentType);
    }
    let html = if plain {
        html.replace('&', "&amp;")
            .replace('<', "&lt;")
            .replace('>', "&gt;")
    } else {
        html
    };
    let mut material = crate::parse(url, &html)?;
    if challenge {
        material.needs_browser = Some(BrowserReason::Challenge);
    }
    Ok(material)
}
