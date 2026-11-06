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
/// A PDF read as a person's subpage or embedded in their page.
pub(crate) const MAX_DOCUMENT_BYTES: usize = 10 * 1024 * 1024;
const REQUEST_TIMEOUT: Duration = Duration::from_secs(20);
const PAGE_TIMEOUT: Duration = Duration::from_secs(40);
const REQUEST_INTERVAL: Duration = Duration::from_millis(300);
/// A website asking to slow down without saying for how long waits this long, and a stated wait
/// is kept within bounds.
const SLOW_DOWN: Duration = Duration::from_secs(60);
const SLOW_DOWN_LIMITS: (Duration, Duration) = (Duration::from_secs(5), Duration::from_secs(600));
pub const MAX_REDIRECTS: usize = 5;

struct SiteLimit {
    slots: Semaphore,
    next: Mutex<Instant>,
}

impl SiteLimit {
    async fn start(&self) -> SemaphorePermit<'_> {
        let slot = self
            .slots
            .acquire()
            .await
            .unwrap_or_else(|_| unreachable!("site capacity is never closed"));
        let mut next = self.next.lock().await;
        sleep_until(*next).await;
        *next = Instant::now() + REQUEST_INTERVAL;
        slot
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

    /// `documents` also reads a PDF, as its text alone.
    pub async fn fetch(
        &self,
        target: &PageTarget,
        documents: bool,
    ) -> Result<FetchedPage, CrawlError> {
        let started = Instant::now();
        let result = timeout(PAGE_TIMEOUT, self.read(target, documents))
            .await
            .unwrap_or_else(|_| Err(CrawlError::TimedOut));
        match &result {
            Ok(page) => tracing::info!(
                url = target.url().as_str(),
                method = "http",
                status = page.status,
                elapsed_ms = started.elapsed().as_millis(),
                result = "success",
                "page fetched"
            ),
            Err(error) => tracing::info!(
                url = target.url().as_str(),
                method = "http",
                %error,
                elapsed_ms = started.elapsed().as_millis(),
                result = "failed",
                "page fetched"
            ),
        }
        result
    }

    /// A file a page shows, such as an image, within `limit` bytes.
    pub(crate) async fn file(
        &self,
        url: &PageUrl,
        limit: usize,
        accept: &'static str,
    ) -> Result<Vec<u8>, CrawlError> {
        let response = timeout(
            PAGE_TIMEOUT,
            self.get(url, accept, limit, |current, location| {
                current.join(location)
            }),
        )
        .await
        .unwrap_or_else(|_| Err(CrawlError::TimedOut))?;
        if let Some(wait) = slow_down(response.status, response.retry_after) {
            return Err(CrawlError::SlowDown(wait));
        }
        if !response.status.is_success() {
            return Err(CrawlError::Status(response.status.as_u16()));
        }
        response.body
    }

    async fn read(&self, target: &PageTarget, documents: bool) -> Result<FetchedPage, CrawlError> {
        let (accept, limit) = if documents {
            (
                "text/html, application/xhtml+xml, text/plain, application/pdf",
                MAX_DOCUMENT_BYTES,
            )
        } else {
            (
                "text/html, application/xhtml+xml, text/plain",
                MAX_PAGE_BYTES,
            )
        };
        let Response {
            url,
            status,
            content_type,
            retry_after,
            body,
            relaxed_certificate,
        } = self
            .get(target.url(), accept, limit, |url, location| {
                // A location without a fragment keeps the requested one (RFC 9110), so a
                // front-end route survives the redirect.
                match url.parsed().fragment() {
                    Some(route) if !location.contains('#') => {
                        target.redirect(url, &format!("{location}#{route}"))
                    }
                    _ => target.redirect(url, location),
                }
            })
            .await?;
        let material = match body {
            Ok(bytes) => {
                let span = tracing::Span::current();
                tokio::task::spawn_blocking(move || {
                    span.in_scope(|| {
                        if documents && status.is_success() && crate::pdf::is_pdf(&bytes) {
                            crate::pdf::text(&bytes)
                                .map(|text| Material::document(url, &text))
                                .map_err(CrawlError::Document)
                        } else {
                            response_material(
                                &url,
                                status.as_u16(),
                                content_type.as_deref(),
                                &bytes,
                            )
                        }
                    })
                })
                .await
                .map_err(CrawlError::Worker)?
            }
            Err(error) => Err(error),
        };
        // Asked to slow down, rather than to pass a challenge, also when the refusal itself
        // cannot be read.
        let material = match material {
            Err(_) if let Some(wait) = slow_down(status, retry_after) => {
                Err(CrawlError::SlowDown(wait))
            }
            material => material,
        }?;
        Ok(FetchedPage {
            material,
            status: status.as_u16(),
            relaxed_certificate,
        })
    }

    /// Follows redirects through `redirect`, which checks where each leads.
    async fn get(
        &self,
        target: &PageUrl,
        accept: &'static str,
        limit: usize,
        redirect: impl Fn(&PageUrl, &str) -> Result<PageUrl, CrawlError>,
    ) -> Result<Response, CrawlError> {
        let site = self.site(target).await;
        let mut url = target.clone();
        let mut relaxed_certificate = false;
        for redirects in 0..=MAX_REDIRECTS {
            let (response, slot) = match self.send(&self.trusted, &url, &site, accept).await {
                Err(CrawlError::Http(error)) if net::untrusted(&error) => {
                    tracing::info!(
                        url = url.as_str(),
                        "retry public page with relaxed certificate checks"
                    );
                    relaxed_certificate = true;
                    self.send(&self.untrusted, &url, &site, accept).await?
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
                let next = redirect(&url, location);
                let destination = match &next {
                    Ok(url) => Some(url.as_str()),
                    Err(_) => None,
                };
                tracing::info!(
                    url = url.as_str(),
                    status = status.as_u16(),
                    location = destination,
                    allowed = next.is_ok(),
                    "HTTP redirect"
                );
                url = next?;
                continue;
            }
            let content_type = response
                .headers()
                .get(header::CONTENT_TYPE)
                .and_then(|h| h.to_str().ok())
                .map(str::to_owned);
            let retry_after = net::retry_after(&response);
            let body = body(response, limit).await;
            drop(slot);
            return Ok(Response {
                url,
                status,
                content_type,
                retry_after,
                body,
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
        accept: &'static str,
    ) -> Result<(reqwest::Response, SemaphorePermit<'a>), CrawlError> {
        let slot = site.start().await;
        let response = client
            .get(url.as_str())
            .header(header::ACCEPT, accept)
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

struct Response {
    url: PageUrl,
    status: StatusCode,
    content_type: Option<String>,
    retry_after: Option<Duration>,
    body: Result<Vec<u8>, CrawlError>,
    relaxed_certificate: bool,
}

/// How long a website that asked to be left alone is.
fn slow_down(status: StatusCode, retry_after: Option<Duration>) -> Option<Duration> {
    // 503 alone is a broken server.
    (status == StatusCode::TOO_MANY_REQUESTS
        || (status == StatusCode::SERVICE_UNAVAILABLE && retry_after.is_some()))
    .then(|| {
        let (least, most) = SLOW_DOWN_LIMITS;
        retry_after.unwrap_or(SLOW_DOWN).clamp(least, most)
    })
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

async fn body(mut response: reqwest::Response, limit: usize) -> Result<Vec<u8>, CrawlError> {
    if response.content_length().is_some_and(|n| n > limit as u64) {
        return Err(CrawlError::TooLarge);
    }
    let mut bytes = Vec::new();
    while let Some(chunk) = response.chunk().await.map_err(CrawlError::Http)? {
        if bytes.len() + chunk.len() > limit {
            return Err(CrawlError::TooLarge);
        }
        bytes.extend_from_slice(&chunk);
    }
    Ok(bytes)
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
    let mut material = crate::parse(url, &html);
    if challenge {
        material.needs_browser = Some(BrowserReason::Challenge);
    }
    Ok(material)
}

#[cfg(test)]
impl Fetcher {
    pub(crate) fn local(port: u16) -> Self {
        let net = net::Net::new(vec![]).unwrap();
        let client = |builder: reqwest::ClientBuilder| {
            builder
                .no_proxy()
                .resolve("school.example.edu", ([127, 0, 0, 1], port).into())
                .resolve("www.school.example.edu", ([127, 0, 0, 1], port).into())
                .resolve("outside.example.org", ([127, 0, 0, 1], port).into())
                .build()
                .unwrap()
        };
        // Reject fixture certificates without depending on the OS trust service under load.
        let tls = net::rustls::ClientConfig::builder_with_provider(Arc::new(
            net::rustls::crypto::ring::default_provider(),
        ))
        .with_safe_default_protocol_versions()
        .unwrap()
        .with_root_certificates(net::rustls::RootCertStore::empty())
        .with_no_client_auth();
        Self {
            trusted: client(net.page_http_builder().tls_backend_preconfigured(tls)),
            untrusted: client(net.untrusted_page_http_builder()),
            sites: Mutex::new(BTreeMap::new()),
        }
    }
}
