use regex::Regex;
use scraper::{ElementRef, Selector};

use crate::{PageUrl, Pagination, page::visible_text};
use std::sync::LazyLock;

#[derive(Clone, Copy)]
enum Kind {
    Forward,
    Last,
    Backward,
    First,
    Number(u32),
    Jump,
    Other,
}

pub(crate) struct Control<'a> {
    pub element: ElementRef<'a>,
    pub link: Option<PageUrl>,
    kind: Kind,
    text: String,
}

impl<'a> Control<'a> {
    pub fn new(element: ElementRef<'a>, text: &str, link: Option<PageUrl>) -> Self {
        let control_text = text.trim().to_owned();
        let text = text.trim().to_lowercase();
        let label = text.trim_matches(['<', '>', '«', '»', '‹', '›', '→', '←', ' ']);
        let kind = if element.value().attr("rel").is_some_and(|rel| {
            rel.split_whitespace()
                .any(|r| r.eq_ignore_ascii_case("next"))
        }) || matches!(text.as_str(), ">" | "»" | "›" | "→")
            || matches!(
                label,
                "下一页" | "下页" | "下翻" | "后页" | "next" | "next page"
            ) {
            Kind::Forward
        } else if matches!(label, "末页" | "尾页" | "last" | "last page") {
            Kind::Last
        } else if matches!(text.as_str(), "<" | "«" | "‹" | "←")
            || matches!(label, "上一页" | "上页" | "previous" | "previous page")
        {
            Kind::Backward
        } else if matches!(label, "首页" | "第一页" | "first" | "first page") {
            Kind::First
        } else if label == "跳转" {
            Kind::Jump
        } else if let Ok(number) = text.parse() {
            Kind::Number(number)
        } else {
            Kind::Other
        };
        Self {
            element,
            link,
            kind,
            text: control_text,
        }
    }
}

pub(crate) fn input_button(element: ElementRef<'_>) -> bool {
    element.value().name() == "input"
        && element.value().attr("type").is_some_and(|kind| {
            matches!(
                kind.to_ascii_lowercase().as_str(),
                "button" | "submit" | "image"
            )
        })
}

fn disabled(element: ElementRef<'_>) -> bool {
    std::iter::once(element)
        .chain(element.ancestors().filter_map(ElementRef::wrap))
        .any(|e| {
            e.value().attr("disabled").is_some()
                || e.value().attr("aria-disabled") == Some("true")
                || e.value()
                    .has_class("disabled", scraper::CaseSensitivity::AsciiCaseInsensitive)
        })
}

fn page_key(key: &str) -> bool {
    matches!(
        key.to_ascii_lowercase().as_str(),
        "page" | "pageno" | "pagenum" | "pageindex" | "page_no"
    )
}

fn query_page(url: &PageUrl) -> bool {
    url.parsed()
        .query_pairs()
        .any(|(key, value)| page_key(&key) && value.parse::<u32>().is_ok())
}

fn same_list_url(current: &PageUrl, target: &PageUrl) -> bool {
    if current.parsed().origin() != target.parsed().origin() {
        return false;
    }
    if current.parsed().path() == target.parsed().path() {
        let page_fields = |url: &PageUrl| {
            url.parsed()
                .query_pairs()
                .filter(|(key, _)| page_key(key))
                .map(|(key, value)| (key.into_owned(), value.parse::<u32>()))
                .collect::<Vec<_>>()
        };
        let current_fields = page_fields(current);
        let target_fields = page_fields(target);
        if current_fields.len() > 1
            || target_fields.len() > 1
            || current_fields
                .iter()
                .chain(&target_fields)
                .any(|(_, value)| value.is_err())
            || current_fields
                .first()
                .zip(target_fields.first())
                .is_some_and(|(a, b)| a.0 != b.0)
        {
            return false;
        }
        let other_query = |url: &PageUrl| {
            url.parsed()
                .query_pairs()
                .filter(|(key, _)| !page_key(key))
                .map(|(k, v)| (k.into_owned(), v.into_owned()))
                .collect::<Vec<_>>()
        };
        return current != target
            && (query_page(current) || query_page(target))
            && other_query(current) == other_query(target);
    }
    let current_path = current.parsed().path();
    let target_path = target.parsed().path();
    let mut common = current_path
        .chars()
        .zip(target_path.chars())
        .take_while(|(a, b)| a == b)
        .map(|(c, _)| c.len_utf8())
        .sum::<usize>();
    while common > 0 && current_path.as_bytes()[common - 1].is_ascii_digit() {
        common -= 1;
    }
    let current_tail = current_path[common..].trim_start_matches(['_', '-']);
    let target_tail = target_path[common..].trim_start_matches(['_', '-']);
    let current_rest = current_tail.trim_start_matches(|c: char| c.is_ascii_digit());
    let target_rest = target_tail.trim_start_matches(|c: char| c.is_ascii_digit());
    (current_rest != current_tail || target_rest != target_tail)
        && current_rest == target_rest
        && current.parsed().query() == target.parsed().query()
}

fn marked_container(element: ElementRef<'_>) -> bool {
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
                "page navigation",
                "翻页",
                "分页",
            ]
            .iter()
            .any(|cue| s.contains(cue))
                || s.split_whitespace().any(|s| s == "pages")
        })
}

struct Context<'a> {
    element: ElementRef<'a>,
    recognized: bool,
    consecutive: bool,
    last: bool,
    last_page: Option<u32>,
}

static CURRENT: LazyLock<Selector> = LazyLock::new(|| {
    Selector::parse(".curr_page, .current_page, .current-page, .current, [aria-current='page']")
        .unwrap_or_else(|_| unreachable!("fixed current page selector is valid"))
});
static TOTAL: LazyLock<Selector> = LazyLock::new(|| {
    Selector::parse(".all_pages, .total_pages, .total-pages")
        .unwrap_or_else(|_| unreachable!("fixed total pages selector is valid"))
});
static PAGE_COUNTS: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"(?:第\s*)?(\d+)\s*(?:页\s*)?/\s*(?:共\s*)?(\d+)\s*页?")
        .unwrap_or_else(|_| unreachable!("fixed page count pattern is valid"))
});

pub(crate) fn extract(controls: &[Control<'_>], url: &PageUrl, has_scripts: bool) -> Pagination {
    let mut contexts: Vec<Context<'_>> = Vec::new();
    let mut pagination = Pagination::default();
    for control in controls {
        if disabled(control.element) {
            continue;
        }
        let mut context = None;
        for element in control
            .element
            .ancestors()
            .filter_map(ElementRef::wrap)
            .take_while(|e| !matches!(e.value().name(), "body" | "html"))
            .take_while(|_| !matches!(control.kind, Kind::Other))
        {
            let index =
                if let Some(index) = contexts.iter().position(|c| c.element.id() == element.id()) {
                    index
                } else {
                    let members: Vec<_> = controls
                        .iter()
                        .filter(|c| c.element.ancestors().any(|n| n.id() == element.id()))
                        .collect();
                    let group = members.iter().all(|c| !matches!(c.kind, Kind::Other));
                    let mut numbers: Vec<_> = members
                        .iter()
                        .filter_map(|c| match c.kind {
                            Kind::Number(n) => Some(n),
                            _ => None,
                        })
                        .collect();
                    let consecutive_links = group
                        && numbers
                            .windows(2)
                            .any(|pair| pair[0].checked_add(1) == Some(pair[1]));
                    let number = |selector: &Selector| {
                        element
                            .select(selector)
                            .next()
                            .and_then(|e| visible_text(e).parse::<u32>().ok())
                    };
                    let marked = marked_container(element);
                    let counts = (marked || group)
                        .then(|| {
                            number(&CURRENT).zip(number(&TOTAL)).or_else(|| {
                                let text = visible_text(element);
                                let found = PAGE_COUNTS.captures(&text)?;
                                Some((found[1].parse().ok()?, found[2].parse().ok()?))
                            })
                        })
                        .flatten();
                    let last = counts.is_some_and(|(current, total)| total > 0 && current >= total);
                    let recognized = marked || consecutive_links || counts.is_some();
                    if recognized {
                        numbers.extend(
                            element
                                .select(&CURRENT)
                                .filter_map(|e| visible_text(e).parse::<u32>().ok()),
                        );
                        numbers.sort_unstable();
                        numbers.dedup();
                    }
                    let consecutive = recognized
                        && numbers
                            .windows(2)
                            .any(|pair| pair[0].checked_add(1) == Some(pair[1]));
                    contexts.push(Context {
                        element,
                        recognized,
                        consecutive,
                        last,
                        last_page: counts.map(|(_, total)| total),
                    });
                    contexts.len() - 1
                };
            if contexts[index].recognized {
                context = Some(&contexts[index]);
                break;
            }
        }
        let is_page = match control.kind {
            Kind::Forward | Kind::Last | Kind::Backward | Kind::Jump => true,
            Kind::First => {
                context.is_some()
                    || control
                        .link
                        .as_ref()
                        .is_some_and(|link| same_list_url(url, link))
            }
            Kind::Number(_) => {
                context.is_some_and(|c| c.consecutive)
                    || control
                        .link
                        .as_ref()
                        .is_some_and(|link| same_list_url(url, link))
            }
            Kind::Other => control.link.as_ref().is_some_and(query_page),
        };
        if !is_page
            || (matches!(control.kind, Kind::Forward | Kind::Last)
                && context.is_some_and(|c| c.last))
        {
            continue;
        }
        if let Some(link) = &control.link {
            if link.website() == url.website() && link != url && !pagination.urls.contains(link) {
                pagination.urls.push(link.clone());
            }
        } else if !matches!(control.kind, Kind::Backward | Kind::First)
            && (control.element.value().attr("onclick").is_some()
                || control
                    .element
                    .value()
                    .attr("href")
                    .is_some_and(|h| javascript_control(h, has_scripts))
                || control.element.value().name() == "button"
                || input_button(control.element)
                || control.element.value().attr("role") == Some("button"))
        {
            pagination.needs_browser = true;
            if !matches!(control.kind, Kind::Jump) {
                let page = match control.kind {
                    Kind::Number(n) => Some(n),
                    Kind::Last => context.and_then(|c| c.last_page),
                    _ => None,
                };
                if (!matches!(control.kind, Kind::Last) || page.is_some())
                    && !pagination.buttons.iter().any(|b| b.control == control.text)
                {
                    pagination.buttons.push(crate::page::ButtonPage {
                        page,
                        control: control.text.clone(),
                    });
                }
            }
        }
    }
    pagination
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
