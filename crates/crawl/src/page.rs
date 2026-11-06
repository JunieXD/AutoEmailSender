use ego_tree::iter::Edge;
use regex::Regex;
use scraper::{ElementRef, Html, Node, Selector};
use serde::{Deserialize, Serialize};
use std::sync::{Arc, LazyLock};

use crate::{
    PageUrl,
    email::{EMAILS, Emails, cloudflare},
    pagination::{self, Control, input_button},
};

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum BrowserReason {
    Challenge,
    JavaScript,
    EncryptedContent,
    Pagination,
}

#[derive(Debug, Clone)]
pub struct Link {
    pub text: String,
    pub url: PageUrl,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub enum EmailLocation {
    /// UTF-8 byte offsets in this unit's text, preserving the original spelling.
    Text { start: usize, end: usize },
    /// Element ordinal in the original document, including the root element.
    Attribute {
        element: usize,
        attribute: String,
        raw: String,
        /// UTF-8 byte position of the element in this block's text.
        offset: usize,
    },
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct EmailEvidence {
    pub address: String,
    pub location: EmailLocation,
    /// Read from an image by text recognition, which can get a letter wrong.
    pub from_image: bool,
}

#[derive(Debug, Clone, Default)]
pub struct Unit {
    pub text: String,
    pub links: Vec<Link>,
    pub emails: Vec<EmailEvidence>,
    /// Original DOM element ordinals from the root through the owning block; none for a PDF's text.
    pub path: Vec<usize>,
    /// UTF-8 positions after rendered line breaks; the block itself starts at zero.
    pub line_starts: Vec<usize>,
    /// The footer or navigation the block is in, as the element ordinal of the innermost one: some
    /// sites wrap the whole page in an element whose class names a footer.
    pub footer_or_nav: Option<usize>,
}

#[derive(Debug, Clone)]
pub struct ButtonPage {
    /// None means the next page after the current replay sequence.
    pub page: Option<u32>,
    pub control: String,
}

#[derive(Debug, Clone, Default)]
pub struct Pagination {
    pub urls: Vec<PageUrl>,
    pub needs_browser: bool,
    pub buttons: Vec<ButtonPage>,
}

#[derive(Debug, Clone)]
pub struct Material {
    pub url: PageUrl,
    pub title: Option<String>,
    /// Text blocks in document order, including blocks with only link evidence.
    pub units: Vec<Unit>,
    pub pagination: Pagination,
    pub needs_browser: Option<BrowserReason>,
    /// None for a PDF, which shows none.
    pub(crate) images: Option<Images>,
    /// The PDFs the page embeds, in page order.
    pub(crate) documents: Vec<PageUrl>,
}

impl Material {
    pub fn text(&self) -> String {
        self.units
            .iter()
            .map(|u| u.text.as_str())
            .collect::<Vec<_>>()
            .join("\n\n")
    }

    /// A PDF read as a page: its text alone.
    pub(crate) fn document(url: PageUrl, text: &str) -> Self {
        let unit = document_unit(text);
        Self {
            url,
            title: None,
            units: if unit.text.is_empty() {
                vec![]
            } else {
                vec![unit]
            },
            pagination: Pagination::default(),
            needs_browser: None,
            images: None,
            documents: vec![],
        }
    }

    /// Whether the page gives this address only in text recognized in its images.
    pub(crate) fn only_from_image(&self, address: &str) -> bool {
        let mut found = self
            .units
            .iter()
            .flat_map(|u| &u.emails)
            .filter(|e| e.address == address)
            .peekable();
        found.peek().is_some() && found.all(|e| e.from_image)
    }
}

/// The images in a page's blocks, which may be text drawn as a picture, such as an address, and
/// the HTML to read the page again with what they say.
#[derive(Clone)]
pub(crate) struct Images {
    pub list: Vec<Image>,
    html: Arc<str>,
    rendered: bool,
}

impl std::fmt::Debug for Images {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_list().entries(&self.list).finish()
    }
}

impl Images {
    /// The page again, with the text recognized in images, by their element, in their places.
    pub fn read(&self, url: &PageUrl, recognized: &[(usize, String)]) -> Material {
        parse_page(url, self.html.clone(), self.rendered, recognized)
    }
}

#[derive(Debug, Clone)]
pub(crate) struct Image {
    /// Element ordinal in the original document.
    pub element: usize,
    pub source: ImageSource,
    /// Its `width` and `height` attributes, when it gives both.
    pub size: Option<(usize, usize)>,
    /// Its caption, its file name or the text just before it speaks of contacting someone.
    pub contact: bool,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) enum ImageSource {
    Url(PageUrl),
    /// The base64 contents of a `data:` URL.
    Data(String),
}

/// Words that tell an image may be an address, as in "邮箱：" before it or `email.png`.
const CONTACT: [&str; 5] = ["邮箱", "邮件", "mail", "联系", "contact"];

fn image(element: ElementRef<'_>, ordinal: usize, base: &PageUrl, before: &str) -> Option<Image> {
    // Pages that load images as they scroll show a placeholder in `src` until then.
    let value = ["data-src", "data-original", "src"]
        .iter()
        .filter_map(|a| element.value().attr(a))
        .map(str::trim)
        .find(|s| !s.is_empty())?;
    let source = match value.get(..5) {
        Some(scheme) if scheme.eq_ignore_ascii_case("data:") => {
            ImageSource::Data(value.split_once(";base64,")?.1.into())
        }
        _ => ImageSource::Url(base.join(value).ok()?),
    };
    let dimension = |name| {
        element
            .value()
            .attr(name)?
            .trim()
            .trim_end_matches("px")
            .parse()
            .ok()
    };
    let file = match &source {
        ImageSource::Url(url) => url.parsed().path().rsplit('/').next().unwrap_or_default(),
        ImageSource::Data(_) => "",
    };
    let caption = alternative_text(element).unwrap_or_default();
    Some(Image {
        element: ordinal,
        size: dimension("width").zip(dimension("height")),
        contact: [caption.as_str(), file, before].iter().any(|text| {
            let text = text.to_lowercase();
            CONTACT.iter().any(|cue| text.contains(cue))
        }),
        source,
    })
}

/// The PDF an embedding element shows: its own address, or the file a PDF viewer there opens.
fn embedded_pdf(address: &str, kind: Option<&str>, base: &PageUrl) -> Option<PageUrl> {
    let url = base.join(address.trim()).ok()?;
    if let Some((_, file)) = url.parsed().query_pairs().find(|(name, _)| name == "file") {
        // Some sites encode the address twice, as in `file=%252Fupload%252Fcv.pdf`.
        let file = if file.contains('/') {
            file
        } else {
            percent_encoding::percent_decode_str(&file)
                .decode_utf8()
                .ok()?
        };
        return url.join(&file).ok();
    }
    (kind.is_some_and(|k| k.trim().eq_ignore_ascii_case("application/pdf"))
        || url.parsed().path().to_ascii_lowercase().ends_with(".pdf"))
    .then_some(url)
}

/// A PDF's text as a block of its own, its lines as lines.
pub(crate) fn document_unit(text: &str) -> Unit {
    let (text, _) = normalize_text(text);
    Unit {
        emails: EMAILS.text(&text),
        line_starts: text.match_indices('\n').map(|(at, _)| at + 1).collect(),
        text,
        ..Unit::default()
    }
}

struct Selectors {
    base: Selector,
    scripts: Selector,
    title: Selector,
    mounts: Selector,
    noscript: Selector,
    teacher_regions: Selector,
    external_scripts: Selector,
}

static SELECTORS: LazyLock<Selectors> = LazyLock::new(|| {
    let compile = |value| {
        Selector::parse(value).unwrap_or_else(|_| unreachable!("fixed page selector is valid"))
    };
    Selectors {
        base: compile("base[href]"),
        scripts: compile("script"),
        title: compile("title"),
        mounts: compile("#root, #app, #__next, #__nuxt, [data-reactroot], [ng-app]"),
        noscript: compile("noscript"),
        teacher_regions: compile("[id], [class]"),
        external_scripts: compile("script[src]"),
    }
});

fn encrypted_field(element: ElementRef<'_>) -> bool {
    element.value().attr("_tsites_encrypt_field").is_some()
}

fn hidden(element: ElementRef<'_>) -> bool {
    element.value().attr("hidden").is_some()
        || element.value().attr("aria-hidden") == Some("true")
        || element.value().attr("style").is_some_and(|s| {
            let style: String = s
                .chars()
                .filter(|c| !c.is_whitespace())
                .flat_map(char::to_lowercase)
                .collect();
            style.contains("display:none") || style.contains("visibility:hidden")
        })
}

fn ignored(element: ElementRef<'_>) -> bool {
    matches!(
        element.value().name(),
        "script" | "style" | "noscript" | "template" | "svg" | "head"
    ) || hidden(element)
        || encrypted_field(element)
}

fn block(name: &str) -> bool {
    matches!(
        name,
        "p" | "div"
            | "li"
            | "tr"
            | "td"
            | "th"
            | "section"
            | "article"
            | "dd"
            | "dt"
            | "h1"
            | "h2"
            | "h3"
            | "h4"
            | "h5"
            | "h6"
            | "html"
            | "body"
            | "main"
            | "nav"
            | "header"
            | "footer"
            | "aside"
            | "ul"
            | "ol"
            | "dl"
            | "table"
            | "tbody"
            | "thead"
            | "tfoot"
            | "address"
            | "blockquote"
            | "figure"
            | "figcaption"
            | "fieldset"
            | "form"
            | "legend"
            | "pre"
            | "hr"
            | "details"
            | "summary"
    )
}

/// The page an `href` leads to; none when it is empty or a place within this page.
fn linked(href: &str, base: &PageUrl, page: &PageUrl) -> Option<PageUrl> {
    let href = href.trim();
    let target = base.join(href).ok()?;
    (!href.is_empty() && !(href.starts_with('#') && target == *page)).then_some(target)
}

fn navigation(script: &str, pattern: &Regex, base: &PageUrl, page: &PageUrl) -> Option<PageUrl> {
    let found = pattern.captures(script)?;
    let target = found.iter().skip(1).flatten().next()?.as_str();
    let target = base.join(target).ok()?;
    (target.website() == page.website()).then_some(target)
}

pub(crate) fn visible_text(element: ElementRef<'_>) -> String {
    if element
        .ancestors()
        .filter_map(ElementRef::wrap)
        .any(ignored)
    {
        return String::new();
    }
    let mut text = String::new();
    let mut suppressed = 0;
    for edge in element.traverse() {
        match edge {
            Edge::Open(node) => {
                if let Some(element) = ElementRef::wrap(node) {
                    if suppressed > 0 || ignored(element) {
                        suppressed += 1;
                    } else if block(element.value().name()) || element.value().name() == "br" {
                        text.push(' ');
                    }
                } else if suppressed == 0
                    && let Node::Text(value) = node.value()
                {
                    text.push_str(value);
                }
            }
            Edge::Close(node) => {
                if let Some(element) = ElementRef::wrap(node) {
                    if suppressed > 0 {
                        suppressed -= 1;
                    } else if block(element.value().name()) {
                        text.push(' ');
                    }
                }
            }
        }
    }
    text.split_whitespace().collect::<Vec<_>>().join(" ")
}

fn label(element: ElementRef<'_>) -> String {
    let text = visible_text(element);
    if !text.is_empty() {
        return text;
    }
    alternative_text(element)
        .or_else(|| {
            element
                .descendants()
                .filter_map(ElementRef::wrap)
                .filter(|e| e.value().name() == "img")
                .find_map(alternative_text)
        })
        .unwrap_or_else(|| "无文字链接".into())
}

fn alternative_text(element: ElementRef<'_>) -> Option<String> {
    let attributes = if element.value().name() == "img" {
        ["alt", "title", "aria-label"]
    } else {
        ["aria-label", "title", "alt"]
    };
    for attribute in attributes {
        if let Some(text) = element
            .value()
            .attr(attribute)
            .filter(|s| !s.trim().is_empty())
        {
            return Some(text.trim().into());
        }
    }
    None
}

static NAVIGATION: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(
        r#"(?ix)
        ^\s*(?:javascript:\s*)?(?:return\s+)?
        (?:
            (?:(?:window|top)\.)?location(?:\.href)?\s*=\s*
            (?:'([^'\\]*)'|"([^"\\]*)")
            |
            (?:opennews|(?:window\.)?open)\s*\(\s*
            (?:'([^'\\]*)'|"([^"\\]*)")
            (?:\s*,\s*(?:'[^'\\]*'|"[^"\\]*")){0,2}\s*\)
        )
        \s*;?\s*(?:return\s+false\s*;?\s*)?$
    "#,
    )
    .unwrap_or_else(|_| unreachable!("fixed navigation pattern is valid"))
});

static PUNCTUATION: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"[\p{P}\s]").unwrap_or_else(|_| unreachable!("fixed punctuation pattern is valid"))
});

/// Extracts data only; no scripts or event handlers are ever executed.
pub fn parse(url: &PageUrl, html: &str) -> Material {
    parse_page(url, html.into(), false, &[])
}

/// `recognized` is the text read from images, by their element.
fn parse_page(
    url: &PageUrl,
    html: Arc<str>,
    rendered: bool,
    recognized: &[(usize, String)],
) -> Material {
    let document = Html::parse_document(&html);
    // Relative links resolve against the first `<base href>`, as in a browser.
    let base = document
        .select(&SELECTORS.base)
        .next()
        .and_then(|e| e.value().attr("href"))
        .and_then(|href| url.join(href.trim()).ok());
    let base = base.as_ref().unwrap_or(url);
    let emails = &*EMAILS;
    let has_scripts = document.select(&SELECTORS.scripts).any(executable_script);
    let title = document.select(&SELECTORS.title).next().map(|e| {
        e.text()
            .collect::<String>()
            .split_whitespace()
            .collect::<Vec<_>>()
            .join(" ")
    });
    let mut units = Vec::new();
    let mut current = Unit::default();
    let mut path = Vec::new();
    let mut blocks: Vec<Vec<usize>> = Vec::new();
    let mut suppressed = 0;
    let mut ordinal = 0;
    let mut controls = Vec::new();
    let mut encrypted = false;
    let mut regions = Vec::new();
    let mut images = Vec::new();
    let mut documents = Vec::new();
    // Where in the current block's text recognized text was put.
    let mut drawn = Vec::new();
    for edge in document.root_element().traverse() {
        match edge {
            Edge::Open(node) => {
                let Some(element) = ElementRef::wrap(node) else {
                    if suppressed == 0
                        && let Node::Text(text) = node.value()
                    {
                        current.text.push_str(text);
                    }
                    continue;
                };
                let element_index = ordinal;
                ordinal += 1;
                path.push(element_index);
                // Many sites mark their footer only by its id or class.
                if matches!(element.value().name(), "footer" | "nav")
                    || matches!(
                        element.value().attr("role"),
                        Some("contentinfo" | "navigation")
                    )
                    || ["id", "class"]
                        .iter()
                        .filter_map(|a| element.value().attr(a))
                        .any(|s| s.to_lowercase().contains("footer"))
                {
                    regions.push(element_index);
                }
                let decrypted = rendered && encrypted_field(element) && !hidden(element);
                encrypted |= encrypted_field(element) && !decrypted;
                if suppressed > 0 || (ignored(element) && !decrypted) {
                    suppressed += 1;
                    continue;
                }
                if block(element.value().name()) {
                    finish(&mut current, &mut drawn, &mut units, emails);
                    blocks.push(path.clone());
                    current.path = path.clone();
                } else if element.value().name() == "br" {
                    current.text.push('\n');
                    current.line_starts.push(current.text.len());
                }
                let href = element.value().attr("href");
                if let Some(href) = href {
                    current
                        .emails
                        .extend(emails.mailto(href, element_index, current.text.len()));
                }
                if let Some(encoded) = element.value().attr("data-cfemail")
                    && let Some(address) = cloudflare(encoded)
                {
                    current.emails.push(EmailEvidence {
                        address: address.clone(),
                        location: EmailLocation::Attribute {
                            element: element_index,
                            attribute: "data-cfemail".into(),
                            raw: encoded.into(),
                            offset: current.text.len(),
                        },
                        from_image: false,
                    });
                    current.text.push_str(&format!(" {address} "));
                }
                if element.value().name() == "img"
                    && let Some(parent) = std::iter::once(element)
                        .chain(element.ancestors().filter_map(ElementRef::wrap))
                        .find(|e| e.value().name() == "a" || e.value().attr("onclick").is_some())
                    && !(parent.id() != element.id()
                        && visible_text(parent).is_empty()
                        && alternative_text(parent).is_some())
                    && let Some(text) = alternative_text(element)
                {
                    current.text.push_str(&text);
                }
                if element.value().name() == "img" {
                    let before = if current.text.trim().is_empty() {
                        units.last().map_or("", |u: &Unit| u.text.as_str())
                    } else {
                        current.text.as_str()
                    };
                    let before =
                        &before[before.char_indices().rev().nth(19).map_or(0, |(at, _)| at)..];
                    images.extend(image(element, element_index, base, before));
                    if let Some((_, text)) = recognized.iter().find(|(e, _)| *e == element_index) {
                        current.text.push(' ');
                        let start = current.text.len();
                        drawn.push((start, start + text.len()));
                        // Its lines are lines of the block, as with `<br>`.
                        current
                            .line_starts
                            .extend(text.match_indices('\n').map(|(at, _)| start + at + 1));
                        current.text.push_str(text);
                        current.text.push(' ');
                    }
                }
                let embedded = match element.value().name() {
                    "iframe" | "embed" => element.value().attr("src"),
                    "object" => element.value().attr("data"),
                    _ => None,
                };
                if let Some(document) =
                    embedded.and_then(|a| embedded_pdf(a, element.value().attr("type"), base))
                    && !documents.contains(&document)
                {
                    documents.push(document);
                }
                let direct = href.and_then(|h| linked(h, base, url));
                let link = direct
                    .or_else(|| {
                        element
                            .value()
                            .attr("onclick")
                            .and_then(|s| navigation(s, &NAVIGATION, base, url))
                    })
                    .or_else(|| {
                        href.filter(|h| h.trim().to_lowercase().starts_with("javascript:"))
                            .and_then(|s| navigation(s, &NAVIGATION, base, url))
                    });
                let control = if element.value().name() == "input" {
                    input_button(element)
                } else {
                    href.is_some()
                        || matches!(element.value().name(), "a" | "button")
                        || element.value().attr("role") == Some("button")
                        || element.value().attr("onclick").is_some()
                };
                if control {
                    if element.value().name() != "img"
                        && (link.is_some() || href.is_some())
                        && visible_text(element).is_empty()
                        && let Some(text) = alternative_text(element)
                    {
                        current.text.push_str(&text);
                    }
                    let text = if element.value().name() == "input" {
                        element
                            .value()
                            .attr("value")
                            .filter(|value| !value.trim().is_empty())
                            .map(str::to_owned)
                            .unwrap_or_else(|| label(element))
                    } else {
                        label(element)
                    };
                    controls.push(Control::new(element, &text, link.clone()));
                    if let Some(link) = link
                        && !current
                            .links
                            .iter()
                            .any(|l| l.url == link && l.text == text)
                    {
                        current.links.push(Link { text, url: link });
                    }
                }
            }
            Edge::Close(node) => {
                let Some(element) = ElementRef::wrap(node) else {
                    continue;
                };
                path.pop();
                if suppressed > 0 {
                    suppressed -= 1;
                    continue;
                }
                if block(element.value().name()) {
                    finish(&mut current, &mut drawn, &mut units, emails);
                    blocks.pop();
                    current.path = blocks.last().cloned().unwrap_or_default();
                }
            }
        }
    }
    finish(&mut current, &mut drawn, &mut units, emails);
    for unit in &mut units {
        unit.footer_or_nav = unit
            .path
            .iter()
            .rev()
            .copied()
            .find(|o| regions.contains(o));
    }
    let pagination = pagination::extract(&controls, url, has_scripts);
    let script_entry = document.select(&SELECTORS.scripts).any(|e| {
        executable_script(e)
            && (e.value().attr("type") == Some("module")
                || e.value().attr("src").is_some_and(|s| !s.trim().is_empty()))
    });
    let mounts: Vec<_> = document.select(&SELECTORS.mounts).collect();
    let mount_ordinals: Vec<_> = document
        .root_element()
        .descendants()
        .filter_map(ElementRef::wrap)
        .enumerate()
        .filter(|(_, e)| mounts.iter().any(|mount| mount.id() == e.id()))
        .map(|(ordinal, _)| ordinal)
        .collect();
    let javascript_hint = document.select(&SELECTORS.noscript).any(|e| {
        let text = e.text().collect::<String>().to_lowercase();
        text.contains("javascript")
            && ["enable", "need", "required", "启用", "开启", "需要"]
                .iter()
                .any(|cue| text.contains(cue))
    });
    let placeholder_only = units.iter().all(|unit| {
        unit.links.is_empty()
            && unit.emails.is_empty()
            && loading_placeholder(&unit.text, &PUNCTUATION)
            && mount_ordinals
                .iter()
                .any(|ordinal| unit.path.contains(ordinal))
    });
    let application_shell =
        placeholder_only && (javascript_hint || (script_entry && !mounts.is_empty()));
    let teacher_regions: Vec<_> = document
        .select(&SELECTORS.teacher_regions)
        .filter(|e| {
            !matches!(e.value().name(), "body" | "html")
                && ["id", "class"]
                    .iter()
                    .filter_map(|a| e.value().attr(a))
                    .any(|s| s.to_lowercase().contains("teacher"))
        })
        .collect();
    let has_profile_links = |e: &ElementRef<'_>| {
        e.descendants().filter_map(ElementRef::wrap).any(|child| {
            child.value().name() == "a"
                && child
                    .value()
                    .attr("href")
                    .is_some_and(|href| linked(href, base, url).is_some())
        })
    };
    let dynamic_list = !teacher_regions.iter().any(has_profile_links)
        && teacher_regions
            .iter()
            .any(|e| e.text().map(str::trim).map(str::len).sum::<usize>() < 300)
        && document.select(&SELECTORS.external_scripts).any(|e| {
            e.value()
                .attr("src")
                .is_some_and(|s| s.contains("search_teacher"))
        });
    let needs_browser = if encrypted {
        Some(BrowserReason::EncryptedContent)
    } else if dynamic_list || application_shell {
        Some(BrowserReason::JavaScript)
    } else if pagination.needs_browser {
        Some(BrowserReason::Pagination)
    } else {
        None
    };
    Material {
        url: url.clone(),
        title,
        units,
        pagination,
        needs_browser,
        images: Some(Images {
            list: images,
            html,
            rendered,
        }),
        documents,
    }
}

fn finish(
    current: &mut Unit,
    drawn: &mut Vec<(usize, usize)>,
    units: &mut Vec<Unit>,
    emails: &Emails,
) {
    let mut unit = std::mem::take(current);
    let (text, positions) = normalize_text(&unit.text);
    let drawn: Vec<_> = drawn
        .drain(..)
        .map(|(start, end)| (positions[start], positions[end]))
        .collect();
    for start in &mut unit.line_starts {
        *start = positions[*start];
    }
    unit.line_starts.dedup();
    unit.text = text;
    for evidence in &mut unit.emails {
        if let EmailLocation::Attribute { offset, .. } = &mut evidence.location {
            *offset = positions[*offset];
        }
    }
    unit.emails
        .extend(emails.text(&unit.text).into_iter().map(|mut evidence| {
            if let EmailLocation::Text { start, end } = evidence.location {
                evidence.from_image = drawn.iter().any(|&(from, to)| start < to && from < end);
            }
            evidence
        }));
    if !unit.text.is_empty() || !unit.links.is_empty() || !unit.emails.is_empty() {
        units.push(unit);
    }
}

fn normalize_text(raw: &str) -> (String, Vec<usize>) {
    let mut text = String::new();
    let mut positions = vec![0; raw.len() + 1];
    let mut whitespace: Option<(usize, bool)> = None;
    for (index, c) in raw.char_indices() {
        if c.is_whitespace() {
            let pending = whitespace.get_or_insert((index, false));
            pending.1 |= c == '\n';
        } else {
            if let Some((start, newline)) = whitespace.take() {
                if !text.is_empty() {
                    text.push(if newline { '\n' } else { ' ' });
                }
                positions[start..index].fill(text.len());
            }
            positions[index..index + c.len_utf8()].fill(text.len());
            text.push(c);
            positions[index + c.len_utf8()] = text.len();
        }
    }
    if let Some((start, _)) = whitespace {
        positions[start..].fill(text.len());
    }
    (text, positions)
}

fn executable_script(element: ElementRef<'_>) -> bool {
    let kind = element
        .value()
        .attr("type")
        .unwrap_or_default()
        .trim()
        .to_ascii_lowercase();
    let kind = kind.split(';').next().unwrap_or_default().trim();
    matches!(
        kind,
        "" | "module"
            | "text/javascript"
            | "application/javascript"
            | "text/ecmascript"
            | "application/ecmascript"
            | "application/x-javascript"
    )
}

fn loading_placeholder(text: &str, punctuation: &Regex) -> bool {
    let text: String = punctuation
        .replace_all(text, "")
        .chars()
        .flat_map(char::to_lowercase)
        .collect();
    matches!(
        text.as_str(),
        "" | "loading"
            | "loadingpleasewait"
            | "pleasewait"
            | "加载中"
            | "正在加载"
            | "请稍候"
            | "请稍等"
    )
}

/// Parse the DOM after browser scripts have run.
pub fn parse_rendered(url: &PageUrl, html: &str) -> Material {
    parse_page(url, html.into(), true, &[])
}
