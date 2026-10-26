use ego_tree::iter::Edge;
use regex::Regex;
use scraper::{ElementRef, Html, Node, Selector};
use serde::{Deserialize, Serialize};
use std::{ops::Range, sync::LazyLock};

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
}

#[derive(Debug, Clone, Default)]
pub struct Unit {
    pub text: String,
    pub links: Vec<Link>,
    pub emails: Vec<EmailEvidence>,
    /// Original DOM element ordinals from the root through the owning block.
    pub path: Vec<usize>,
    /// UTF-8 positions after rendered line breaks; the block itself starts at zero.
    pub line_starts: Vec<usize>,
    /// UTF-8 ranges of link labels inserted from alternative text attributes.
    pub alternative_text: Vec<Range<usize>>,
    pub in_footer_or_nav: bool,
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
}

impl Material {
    pub fn text(&self) -> String {
        self.units
            .iter()
            .map(|u| u.text.as_str())
            .collect::<Vec<_>>()
            .join("\n\n")
    }
}

struct Selectors {
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

fn navigation(script: &str, pattern: &Regex, url: &PageUrl) -> Option<PageUrl> {
    let found = pattern.captures(script)?;
    let target = found.iter().skip(1).flatten().next()?.as_str();
    let target = url.join(target).ok()?;
    (target.website() == url.website()).then_some(target)
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

fn add_alternative(unit: &mut Unit, text: &str) {
    let start = unit.text.len();
    unit.text.push_str(text);
    unit.alternative_text.push(start..unit.text.len());
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
    parse_page(url, html, false)
}

fn parse_page(url: &PageUrl, html: &str, rendered: bool) -> Material {
    let document = Html::parse_document(html);
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
                if matches!(element.value().name(), "footer" | "nav") {
                    regions.push(element_index);
                }
                let decrypted = rendered && encrypted_field(element) && !hidden(element);
                encrypted |= encrypted_field(element) && !decrypted;
                if suppressed > 0 || (ignored(element) && !decrypted) {
                    suppressed += 1;
                    continue;
                }
                if block(element.value().name()) {
                    finish(&mut current, &mut units, emails);
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
                    add_alternative(&mut current, &text);
                }
                let direct = href
                    .filter(|h| !h.trim().is_empty() && !h.trim().starts_with('#'))
                    .and_then(|h| url.join(h).ok());
                let link = direct
                    .or_else(|| {
                        element
                            .value()
                            .attr("onclick")
                            .and_then(|s| navigation(s, &NAVIGATION, url))
                    })
                    .or_else(|| {
                        href.filter(|h| h.trim().to_lowercase().starts_with("javascript:"))
                            .and_then(|s| navigation(s, &NAVIGATION, url))
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
                        add_alternative(&mut current, &text);
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
                    finish(&mut current, &mut units, emails);
                    blocks.pop();
                    current.path = blocks.last().cloned().unwrap_or_default();
                }
            }
        }
    }
    finish(&mut current, &mut units, emails);
    for unit in &mut units {
        unit.in_footer_or_nav = regions.iter().any(|ordinal| unit.path.contains(ordinal));
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
                && child.value().attr("href").is_some_and(|href| {
                    let href = href.trim();
                    !href.is_empty() && !href.starts_with('#') && url.join(href).is_ok()
                })
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
    }
}

fn finish(current: &mut Unit, units: &mut Vec<Unit>, emails: &Emails) {
    let mut unit = std::mem::take(current);
    let (text, positions) = normalize_text(&unit.text);
    for start in &mut unit.line_starts {
        *start = positions[*start];
    }
    unit.line_starts.dedup();
    for range in &mut unit.alternative_text {
        range.start = positions[range.start];
        range.end = text[..positions[range.end]].trim_end().len();
    }
    unit.text = text;
    for evidence in &mut unit.emails {
        if let EmailLocation::Attribute { offset, .. } = &mut evidence.location {
            *offset = positions[*offset];
        }
    }
    unit.emails.extend(emails.text(&unit.text));
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
    parse_page(url, html, true)
}
