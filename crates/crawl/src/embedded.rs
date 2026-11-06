//! What the images and PDFs on a page read for one person say: pages often draw an address as a
//! picture, or give a CV as a PDF.
use base64::{Engine, engine::general_purpose::STANDARD};
use model::background::Cooldown;

use crate::{
    CrawlError, Material, PageUrl,
    fetch::{Fetcher, MAX_DOCUMENT_BYTES},
    page::{self, Image, ImageSource},
    pdf,
};

/// Reads the text in an encoded image: `ocr::recognize`, or a fake in tests.
pub(crate) type Recognize = fn(&[u8]) -> Result<String, String>;

/// Images read to see their size, of which this many are recognized.
const IMAGES_CHECKED: usize = 20;
const IMAGES_RECOGNIZED: usize = 6;
const MAX_IMAGE_BYTES: usize = 1024 * 1024;
const DOCUMENTS: usize = 2;

/// The size of a line of text, such as an address: not a photo or a banner.
fn line_sized(width: usize, height: usize) -> bool {
    (32..=1600).contains(&width) && (8..=240).contains(&height) && width * 5 >= height * 7
}

pub(crate) struct Reader<'a> {
    pub fetcher: &'a Fetcher,
    pub recognize: Recognize,
    pub cooldown: &'a Cooldown<i64>,
    pub crawl: i64,
}

impl Reader<'_> {
    /// The page with the text recognized in its images, when `images`, and its PDFs' text after
    /// its own, when `documents`. A file that cannot be had is skipped; so are the rest once a
    /// limit holds the crawl back.
    pub async fn read(
        &self,
        mut m: Material,
        images: bool,
        documents: bool,
    ) -> Result<Material, CrawlError> {
        let mut recognized = 0;
        if images && let Some(found) = m.images.clone() {
            let (texts, count) = self.read_images(&found.list).await;
            recognized = count;
            if !texts.is_empty() {
                let url = m.url.clone();
                let span = tracing::Span::current();
                m = tokio::task::spawn_blocking(move || span.in_scope(|| found.read(&url, &texts)))
                    .await
                    .map_err(CrawlError::Worker)?;
            }
        }
        let mut read = 0;
        let urls: Vec<_> = if documents {
            m.documents.iter().take(DOCUMENTS).cloned().collect()
        } else {
            vec![]
        };
        for url in urls {
            if self.cooldown.until().is_some() {
                break;
            }
            let Some(bytes) = self.file(&url, MAX_DOCUMENT_BYTES, "application/pdf").await else {
                continue;
            };
            if !pdf::is_pdf(&bytes) {
                skipped(url.as_str(), "not a PDF");
                continue;
            }
            match blocking(move || pdf::text(&bytes)).await {
                Ok(text) => {
                    read += 1;
                    let unit = page::document_unit(&text);
                    if !unit.text.is_empty() {
                        m.units.push(unit);
                    }
                }
                Err(error) => skipped(url.as_str(), error),
            }
        }
        tracing::info!(
            images = m.images.as_ref().map_or(0, |found| found.list.len()),
            images_recognized = recognized,
            pdfs = m.documents.len(),
            pdfs_read = read,
            "embedded files read"
        );
        Ok(m)
    }

    /// The text in the images most likely to give an address, by their element, and how many
    /// were recognized.
    async fn read_images(&self, images: &[Image]) -> (Vec<(usize, String)>, usize) {
        let mut ranked: Vec<_> = images.iter().collect();
        ranked.sort_by_key(|image| !image.contact);
        // An image shown more than once, such as an icon, is read once.
        let mut sources: Vec<(&ImageSource, Vec<usize>)> = Vec::new();
        for image in ranked {
            if let Some((_, elements)) = sources.iter_mut().find(|(s, _)| **s == image.source) {
                elements.push(image.element);
            } else if sources.len() < IMAGES_CHECKED
                && image
                    .size
                    .is_none_or(|(width, height)| line_sized(width, height))
            {
                sources.push((&image.source, vec![image.element]));
            }
        }
        let (mut texts, mut recognized) = (Vec::new(), 0);
        for (source, elements) in sources {
            if recognized == IMAGES_RECOGNIZED || self.cooldown.until().is_some() {
                break;
            }
            let (name, bytes) = match source {
                ImageSource::Url(url) => (
                    url.as_str(),
                    self.file(url, MAX_IMAGE_BYTES, "image/*").await,
                ),
                ImageSource::Data(data) => (
                    "data URL",
                    STANDARD
                        .decode(data)
                        .inspect_err(|error| skipped("data URL", error))
                        .ok(),
                ),
            };
            let Some(bytes) = bytes.filter(|bytes| bytes.len() <= MAX_IMAGE_BYTES) else {
                continue;
            };
            if !imagesize::blob_size(&bytes).is_ok_and(|size| line_sized(size.width, size.height)) {
                continue;
            }
            recognized += 1;
            let recognize = self.recognize;
            match blocking(move || recognize(&bytes)).await {
                Ok(text) => {
                    let text = text
                        .lines()
                        .map(str::trim)
                        .filter(|line| !line.is_empty())
                        .collect::<Vec<_>>()
                        .join("\n");
                    if !text.is_empty() {
                        texts.extend(elements.into_iter().map(|e| (e, text.clone())));
                    }
                }
                Err(error) => skipped(name, error),
            }
        }
        (texts, recognized)
    }

    /// A file the page shows, or None when it cannot be had.
    async fn file(&self, url: &PageUrl, limit: usize, accept: &'static str) -> Option<Vec<u8>> {
        self.fetcher
            .file(url, limit, accept)
            .await
            .inspect_err(|error| {
                if let CrawlError::SlowDown(wait) = error {
                    // As for a page: nothing more until then, and this file is skipped.
                    let round = self.cooldown.round(&self.crawl);
                    self.cooldown.defer(self.crawl, round, *wait);
                }
                skipped(url.as_str(), error);
            })
            .ok()
    }
}

fn skipped(file: &str, error: impl std::fmt::Display) {
    tracing::info!(file, %error, "embedded file skipped");
}

/// Slow work, such as reading a PDF, off the async threads.
async fn blocking<T: Send + 'static>(
    work: impl FnOnce() -> Result<T, String> + Send + 'static,
) -> Result<T, String> {
    let span = tracing::Span::current();
    tokio::task::spawn_blocking(move || span.in_scope(work))
        .await
        .unwrap_or_else(|e| Err(e.to_string()))
}
