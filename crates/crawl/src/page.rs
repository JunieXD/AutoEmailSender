use std::collections::BTreeMap;

use ego_tree::{NodeId, iter::Edge};
use regex::Regex;
use scraper::{ElementRef, Html, Node, Selector};
use serde::{Deserialize, Serialize};

use crate::{
    CrawlError, PageUrl,
    email::{Emails, cloudflare},
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
    pub is_card: bool,
}

#[derive(Debug, Clone, Default)]
pub struct Pagination {
    pub urls: Vec<PageUrl>,
    pub needs_browser: bool,
}

#[derive(Debug, Clone)]
pub struct Material {
    pub url: PageUrl,
    pub title: Option<String>,
    /// Document order. Context outside the cards stays in separate units.
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

fn selector(value: &str) -> Result<Selector, CrawlError> {
    Selector::parse(value).map_err(|e| CrawlError::Parse(format!("selector: {e}")))
}

fn encrypted_field(element: ElementRef<'_>) -> bool {
    element.value().attr("_tsites_encrypt_field").is_some()
}

fn ignored(element: ElementRef<'_>) -> bool {
    matches!(
        element.value().name(),
        "script" | "style" | "noscript" | "template" | "svg" | "head"
    ) || element.value().attr("hidden").is_some()
        || element.value().attr("aria-hidden") == Some("true")
        || element.value().attr("style").is_some_and(|s| {
            let style: String = s
                .chars()
                .filter(|c| !c.is_whitespace())
                .flat_map(char::to_lowercase)
                .collect();
            style.contains("display:none") || style.contains("visibility:hidden")
        })
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
            | "br"
            | "h1"
            | "h2"
            | "h3"
            | "h4"
    )
}

fn shape(element: ElementRef<'_>) -> String {
    let name = element.value().name();
    if matches!(name, "li" | "tr" | "dd" | "article") {
        return name.into();
    }
    let mut classes: Vec<_> = element.value().classes().collect();
    classes.sort();
    format!(
        "{name}:{classes:?}:{:?}",
        element
            .child_elements()
            .map(|e| e.value().name())
            .collect::<Vec<_>>()
    )
}

fn repeated_cards(document: &Html) -> Result<Vec<NodeId>, CrawlError> {
    let candidates = selector("li, tr, div, article, dd, section")?;
    let links = selector("a[href], [onclick], [data-cfemail]")?;
    let mut repeated = Vec::new();
    for parent in document
        .root_element()
        .descendants()
        .filter_map(ElementRef::wrap)
    {
        if ignored(parent) {
            continue;
        }
        let mut groups: BTreeMap<String, Vec<ElementRef<'_>>> = BTreeMap::new();
        for child in parent
            .child_elements()
            .filter(|e| candidates.matches(e) && !ignored(*e))
        {
            groups.entry(shape(child)).or_default().push(child);
        }
        for siblings in groups.values().filter(|siblings| siblings.len() >= 2) {
            for &child in siblings {
                if !ignored(child)
                    && (child.select(&links).next().is_some()
                        || child.value().attr("onclick").is_some()
                        || child
                            .text()
                            .any(|s| s.contains('@') || s.contains("(at)") || s.contains('＠')))
                {
                    repeated.push(child);
                }
            }
        }
    }
    // A repeated column/group may contain several cards. Keep the smaller evidence units.
    let mut cards = Vec::new();
    for element in &repeated {
        if element
            .descendants()
            .skip(1)
            .filter(|n| repeated.iter().any(|e| e.id() == n.id()))
            .take(2)
            .count()
            < 2
        {
            cards.push(element.id());
        }
    }
    Ok(cards)
}

fn navigation(script: &str, pattern: &Regex, url: &PageUrl) -> Option<PageUrl> {
    let found = pattern.captures(script)?;
    let target = found.get(1).or_else(|| found.get(2))?.as_str();
    let target = url.join(target).ok()?;
    (target.website() == url.website()).then_some(target)
}

fn label(element: ElementRef<'_>) -> String {
    let text = element
        .descendants()
        .filter_map(|node| {
            let Node::Text(text) = node.value() else {
                return None;
            };
            node.ancestors()
                .take_while(|n| n.id() != element.id())
                .filter_map(ElementRef::wrap)
                .all(|e| !ignored(e))
                .then_some(text.as_ref())
        })
        .collect::<Vec<_>>()
        .join(" ");
    let text = text.split_whitespace().collect::<Vec<_>>().join(" ");
    if !text.is_empty() {
        return text;
    }
    for attribute in ["aria-label", "title", "alt"] {
        if let Some(text) = element
            .value()
            .attr(attribute)
            .filter(|s| !s.trim().is_empty())
        {
            return text.trim().into();
        }
    }
    for image in element
        .descendants()
        .filter_map(ElementRef::wrap)
        .filter(|e| e.value().name() == "img")
    {
        if let Some(text) = image
            .value()
            .attr("alt")
            .or_else(|| image.value().attr("title"))
        {
            return text.into();
        }
    }
    "无文字链接".into()
}

fn pagination_control(element: ElementRef<'_>, text: &str) -> bool {
    let text = text.trim().to_lowercase();
    let label = text.trim_matches(['<', '>', '«', '»', '‹', '›', '→', '←', ' ']);
    let explicit = [
        "下一页",
        "下页",
        "下翻",
        "后页",
        "末页",
        "尾页",
        "上一页",
        "上页",
        "首页",
        "next",
        "next page",
        "previous",
        "last",
        "first",
        "»",
        "›",
        ">",
        "跳转",
    ];
    if explicit.contains(&text.as_str())
        || explicit.contains(&label)
        || element
            .value()
            .attr("rel")
            .is_some_and(|r| r.split_whitespace().any(|r| r.eq_ignore_ascii_case("next")))
    {
        return true;
    }
    let numeric = !text.is_empty() && text.chars().all(|c| c.is_ascii_digit());
    numeric
        && element
            .ancestors()
            .filter_map(ElementRef::wrap)
            .take(4)
            .any(|e| {
                ["class", "id", "aria-label"]
                    .iter()
                    .filter_map(|a| e.value().attr(a))
                    .any(|s| {
                        let s = s.to_lowercase();
                        s.contains("pag") || s.contains("翻页") || s.contains("分页")
                    })
            })
}

fn backward_control(text: &str) -> bool {
    let text = text.trim().to_lowercase();
    let text = text.trim_matches(['<', '>', '«', '»', '‹', '›', '→', '←', ' ']);
    matches!(
        text,
        "上一页" | "上页" | "首页" | "第一页" | "previous" | "previous page" | "first"
    )
}

/// Extracts data only; no scripts or event handlers are ever executed.
pub fn parse(url: &PageUrl, html: &str) -> Result<Material, CrawlError> {
    let document = Html::parse_document(html);
    let emails = Emails::new()?;
    let navigation_pattern = Regex::new(r#"(?i)(?:\b(?:opennews|(?:window\.)?open)\s*\(\s*|\b(?:(?:window|top)\.)?location(?:\.href)?\s*=\s*)(?:'([^'\\]*)'|"([^"\\]*)")"#)
        .map_err(|e| CrawlError::Parse(e.to_string()))?;
    let title = document.select(&selector("title")?).next().map(|e| {
        e.text()
            .collect::<String>()
            .split_whitespace()
            .collect::<Vec<_>>()
            .join(" ")
    });
    let cards = repeated_cards(&document)?;
    let mut units = Vec::new();
    let mut current = Unit::default();
    let mut active_card = None;
    let mut suppressed = 0;
    let mut ordinal = 0;
    let mut pagination = Pagination::default();
    let mut encrypted = false;
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
                encrypted |= encrypted_field(element);
                if suppressed > 0 || ignored(element) {
                    suppressed += 1;
                    continue;
                }
                if active_card.is_none() && cards.contains(&node.id()) {
                    finish(&mut current, &mut units, &emails);
                    current.is_card = true;
                    active_card = Some(node.id());
                }
                if block(element.value().name()) {
                    current.text.push('\n');
                } else {
                    current.text.push(' ');
                }
                let href = element.value().attr("href");
                if let Some(href) = href {
                    current.emails.extend(emails.mailto(href, element_index));
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
                        },
                    });
                    current.text.push_str(&format!(" {address} "));
                }
                let direct = href
                    .filter(|h| !h.trim().is_empty() && !h.trim().starts_with('#'))
                    .and_then(|h| url.join(h).ok());
                let link = direct
                    .or_else(|| {
                        element
                            .value()
                            .attr("onclick")
                            .and_then(|s| navigation(s, &navigation_pattern, url))
                    })
                    .or_else(|| {
                        href.filter(|h| h.trim().to_lowercase().starts_with("javascript:"))
                            .and_then(|s| navigation(s, &navigation_pattern, url))
                    });
                let control = href.is_some()
                    || matches!(element.value().name(), "a" | "button" | "input")
                    || element.value().attr("role") == Some("button")
                    || element.value().attr("onclick").is_some();
                if control {
                    let text = if element.value().name() == "input" {
                        element.value().attr("value").unwrap_or_default().into()
                    } else {
                        label(element)
                    };
                    let query_page = link.as_ref().is_some_and(|u| {
                        u.parsed().query_pairs().any(|(key, value)| {
                            matches!(
                                key.to_lowercase().as_str(),
                                "page" | "pageno" | "pagenum" | "pageindex" | "page_no"
                            ) && value.parse::<u32>().is_ok()
                        })
                    });
                    if pagination_control(element, &text) || query_page {
                        if let Some(link) = &link {
                            if link.website() == url.website()
                                && link != url
                                && !pagination.urls.contains(link)
                            {
                                pagination.urls.push(link.clone());
                            }
                        } else if !backward_control(&text)
                            && element.value().attr("disabled").is_none()
                            && element.value().attr("aria-disabled") != Some("true")
                            && (element.value().attr("onclick").is_some()
                                || href
                                    .is_some_and(|h| h.to_lowercase().starts_with("javascript:"))
                                || element.value().name() == "button"
                                || element.value().attr("role") == Some("button"))
                        {
                            pagination.needs_browser = true;
                        }
                    }
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
                if suppressed > 0 {
                    suppressed -= 1;
                    continue;
                }
                if block(element.value().name()) {
                    current.text.push('\n');
                } else {
                    current.text.push(' ');
                }
                if active_card == Some(node.id()) {
                    finish(&mut current, &mut units, &emails);
                    active_card = None;
                }
            }
        }
    }
    finish(&mut current, &mut units, &emails);
    let scripts = document.select(&selector("script")?).count();
    let text_len: usize = units
        .iter()
        .map(|u| u.text.chars().filter(|c| !c.is_whitespace()).count())
        .sum();
    let dynamic_list = document.select(&selector("[id], [class]")?).any(|e| {
        ["id", "class"]
            .iter()
            .filter_map(|a| e.value().attr(a))
            .any(|s| {
                let s = s.to_lowercase();
                s.contains("teacher")
                    && e.text().map(str::trim).map(str::len).sum::<usize>() < 300
                    && e.descendants().filter_map(ElementRef::wrap).all(|child| {
                        child.value().name() != "a" || child.value().attr("href").is_none()
                    })
            })
    }) && document.select(&selector("script[src]")?).any(|e| {
        e.value()
            .attr("src")
            .is_some_and(|s| s.contains("search_teacher"))
    });
    let needs_browser = if encrypted {
        Some(BrowserReason::EncryptedContent)
    } else if dynamic_list || (text_len < 100 && scripts >= 3) {
        Some(BrowserReason::JavaScript)
    } else if pagination.needs_browser {
        Some(BrowserReason::Pagination)
    } else {
        None
    };
    Ok(Material {
        url: url.clone(),
        title,
        units,
        pagination,
        needs_browser,
    })
}

fn finish(current: &mut Unit, units: &mut Vec<Unit>, emails: &Emails) {
    let mut unit = std::mem::take(current);
    unit.text = unit
        .text
        .lines()
        .map(|line| line.split_whitespace().collect::<Vec<_>>().join(" "))
        .filter(|line| !line.is_empty())
        .collect::<Vec<_>>()
        .join("\n");
    unit.emails.extend(emails.text(&unit.text));
    if !unit.text.is_empty() || !unit.links.is_empty() || !unit.emails.is_empty() {
        units.push(unit);
    }
}
