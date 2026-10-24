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

fn contact_attribute(element: ElementRef<'_>, emails: &Emails) -> bool {
    element
        .value()
        .attr("href")
        .is_some_and(|href| !emails.mailto(href, 0).is_empty())
        || element
            .value()
            .attr("data-cfemail")
            .and_then(cloudflare)
            .is_some()
}

fn repeated_cards(document: &Html, emails: &Emails) -> Result<Vec<NodeId>, CrawlError> {
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
                        || !emails.text(&visible_text(child)).is_empty())
                {
                    repeated.push(child);
                }
            }
        }
    }
    let evidence: Vec<_> = repeated
        .iter()
        .map(|&element| {
            let has_contact = !emails.text(&visible_text(element)).is_empty()
                || element.descendants().filter_map(ElementRef::wrap).any(|e| {
                    e.ancestors()
                        .take_while(|n| n.id() != element.id())
                        .filter_map(ElementRef::wrap)
                        .all(|e| !ignored(e))
                        && contact_attribute(e, emails)
                });
            let owned_text = element
                .descendants()
                .filter_map(|node| {
                    let Node::Text(text) = node.value() else {
                        return None;
                    };
                    node.ancestors()
                        .take_while(|n| n.id() != element.id())
                        .filter_map(ElementRef::wrap)
                        .all(|e| {
                            !ignored(e)
                                && !repeated.iter().any(|r| r.id() == e.id())
                                && !contact_attribute(e, emails)
                        })
                        .then_some(text.as_ref())
                })
                .collect::<Vec<_>>()
                .join(" ");
            let mut identity = owned_text.clone();
            for evidence in emails.text(&owned_text).iter().rev() {
                if let EmailLocation::Text { start, end } = evidence.location {
                    identity.replace_range(start..end, "");
                }
            }
            (element, identity.trim().to_owned(), has_contact)
        })
        .collect();
    let records: Vec<_> = evidence
        .iter()
        .filter(|(element, identity, has_contact)| {
            // Repeated contact labels do not establish separate identities.
            *has_contact
                && identity.chars().any(char::is_alphanumeric)
                && evidence.iter().any(|(sibling, other_identity, _)| {
                    sibling.parent().map(|n| n.id()) == element.parent().map(|n| n.id())
                        && !other_identity.is_empty()
                        && other_identity != identity
                })
        })
        .map(|(element, _, _)| element.id())
        .collect();
    // Prefer a person's identity plus contacts over repeated fields inside that record.
    // Columns containing multiple complete records still split at the person boundaries.
    let mut cards = Vec::new();
    for element in &repeated {
        let descendants: Vec<_> = element
            .descendants()
            .skip(1)
            .filter(|n| repeated.iter().any(|e| e.id() == n.id()))
            .map(|n| n.id())
            .collect();
        if descendants.iter().filter(|id| records.contains(id)).count() < 2
            && (records.contains(&element.id()) || descendants.len() < 2)
        {
            cards.push(element.id());
        }
    }
    Ok(cards)
}

fn navigation(script: &str, pattern: &Regex, url: &PageUrl) -> Option<PageUrl> {
    let found = pattern.captures(script)?;
    let target = found.iter().skip(1).flatten().next()?.as_str();
    let target = url.join(target).ok()?;
    (target.website() == url.website()).then_some(target)
}

fn visible_text(element: ElementRef<'_>) -> String {
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
    text.split_whitespace().collect::<Vec<_>>().join(" ")
}

fn label(element: ElementRef<'_>) -> String {
    let text = visible_text(element);
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

fn pagination_container(element: ElementRef<'_>) -> bool {
    ["class", "id", "aria-label"]
        .iter()
        .filter_map(|a| element.value().attr(a))
        .any(|s| {
            let s = s.to_lowercase();
            [
                "pagination",
                "paging",
                "pager",
                "pagenav",
                "page-nav",
                "page_nav",
                "pagebar",
                "翻页",
                "分页",
            ]
            .iter()
            .any(|cue| s.contains(cue))
                || s.split_whitespace().any(|s| s == "pages")
        })
}

fn pagination_context(element: ElementRef<'_>) -> Option<ElementRef<'_>> {
    std::iter::once(element)
        .chain(element.ancestors().filter_map(ElementRef::wrap))
        .take(5)
        .find(|e| pagination_container(*e))
}

fn query_page(url: &PageUrl) -> bool {
    url.parsed().query_pairs().any(|(key, value)| {
        matches!(
            key.to_lowercase().as_str(),
            "page" | "pageno" | "pagenum" | "pageindex" | "page_no"
        ) && value.parse::<u32>().is_ok()
    })
}

fn same_list_url(current: &PageUrl, target: &PageUrl) -> bool {
    fn path_parts(url: &PageUrl) -> (&str, &str, &str) {
        let (directory, file) = url.parsed().path().rsplit_once('/').unwrap_or_default();
        let (stem, extension) = file.rsplit_once('.').unwrap_or((file, ""));
        let stem = stem.trim_end_matches(|c: char| c.is_ascii_digit());
        (directory, stem, extension)
    }
    current.parsed().origin() == target.parsed().origin()
        && if current.parsed().path() == target.parsed().path() {
            query_page(current) || query_page(target)
        } else {
            let current = path_parts(current);
            !current.1.is_empty() && current == path_parts(target)
        }
}

fn pagination_control(
    element: ElementRef<'_>,
    text: &str,
    link: Option<&PageUrl>,
    current: &PageUrl,
) -> bool {
    let text = text.trim().to_lowercase();
    let label = text.trim_matches(['<', '>', '«', '»', '‹', '›', '→', '←', ' ']);
    let context = pagination_context(element).is_some();
    if matches!(label, "首页" | "first") {
        return context || link.is_some_and(|link| same_list_url(current, link));
    }
    let explicit = [
        "下一页",
        "下页",
        "下翻",
        "后页",
        "末页",
        "尾页",
        "上一页",
        "上页",
        "第一页",
        "next",
        "next page",
        "previous",
        "last",
        "first page",
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
    numeric && context
}

fn input_button(element: ElementRef<'_>) -> bool {
    element.value().name() == "input"
        && element.value().attr("type").is_some_and(|kind| {
            matches!(
                kind.to_ascii_lowercase().as_str(),
                "button" | "submit" | "image"
            )
        })
}

fn disabled_control(element: ElementRef<'_>) -> bool {
    std::iter::once(element)
        .chain(element.ancestors().filter_map(ElementRef::wrap))
        .any(|e| {
            e.value().attr("disabled").is_some()
                || e.value().attr("aria-disabled") == Some("true")
                || e.value()
                    .has_class("disabled", scraper::CaseSensitivity::AsciiCaseInsensitive)
        })
}

fn last_page(element: ElementRef<'_>, current: &Selector, total: &Selector) -> bool {
    std::iter::once(element)
        .chain(element.ancestors().filter_map(ElementRef::wrap))
        .take(5)
        .filter(|e| pagination_container(*e))
        .any(|e| {
            let number = |pattern| {
                e.select(pattern)
                    .next()
                    .and_then(|e| visible_text(e).parse::<u32>().ok())
            };
            matches!((number(current), number(total)), (Some(current), Some(total)) if total > 0 && current >= total)
        })
}

fn javascript_control(href: &str, has_scripts: bool) -> bool {
    let href: String = href
        .chars()
        .filter(|c| !c.is_whitespace() && *c != ';')
        .flat_map(char::to_lowercase)
        .collect();
    href.strip_prefix("javascript:").is_some_and(|script| {
        has_scripts || !matches!(script, "" | "void(0)" | "void0" | "undefined")
    })
}

fn forward_control(text: &str) -> bool {
    let text = text.trim().to_lowercase();
    if matches!(text.as_str(), ">" | "»" | "›" | "→") {
        return true;
    }
    let text = text.trim_matches(['<', '>', '«', '»', '‹', '›', '→', '←', ' ']);
    matches!(
        text,
        "下一页" | "下页" | "下翻" | "后页" | "末页" | "尾页" | "next" | "next page" | "last"
    )
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
    let navigation_pattern = Regex::new(
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
    .map_err(|e| CrawlError::Parse(e.to_string()))?;
    let scripts = selector("script")?;
    let has_scripts = document.select(&scripts).next().is_some();
    let current_page = selector(".curr_page, .current_page, .current-page, [aria-current='page']")?;
    let total_pages = selector(".all_pages, .total_pages, .total-pages")?;
    let title = document.select(&selector("title")?).next().map(|e| {
        e.text()
            .collect::<String>()
            .split_whitespace()
            .collect::<Vec<_>>()
            .join(" ")
    });
    let cards = repeated_cards(&document, &emails)?;
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
                let control = if element.value().name() == "input" {
                    input_button(element)
                } else {
                    href.is_some()
                        || matches!(element.value().name(), "a" | "button")
                        || element.value().attr("role") == Some("button")
                        || element.value().attr("onclick").is_some()
                };
                if control {
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
                    let ended =
                        forward_control(&text) && last_page(element, &current_page, &total_pages);
                    if !disabled_control(element)
                        && !ended
                        && (pagination_control(element, &text, link.as_ref(), url)
                            || link.as_ref().is_some_and(query_page))
                    {
                        if let Some(link) = &link {
                            if link.website() == url.website()
                                && link != url
                                && !pagination.urls.contains(link)
                            {
                                pagination.urls.push(link.clone());
                            }
                        } else if !backward_control(&text)
                            && (element.value().attr("onclick").is_some()
                                || href.is_some_and(|h| javascript_control(h, has_scripts))
                                || element.value().name() == "button"
                                || input_button(element)
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
    let script_entry = document.select(&scripts).any(|e| {
        e.value().attr("type") == Some("module")
            || e.value().attr("src").is_some_and(|s| !s.trim().is_empty())
    });
    let empty_mount = document
        .select(&selector(
            "#root, #app, #__next, #__nuxt, [data-reactroot], [ng-app]",
        )?)
        .any(|e| visible_text(e).is_empty());
    let javascript_hint = document.select(&selector("noscript")?).any(|e| {
        let text = e.text().collect::<String>().to_lowercase();
        text.contains("javascript")
            && ["enable", "need", "required", "启用", "开启", "需要"]
                .iter()
                .any(|cue| text.contains(cue))
    });
    let application_shell = units.is_empty() && script_entry && (empty_mount || javascript_hint);
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
    } else if dynamic_list || application_shell {
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
