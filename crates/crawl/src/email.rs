use regex::Regex;
use std::sync::LazyLock;

use crate::{EmailEvidence, EmailLocation};

pub(crate) struct Emails {
    candidate: Regex,
    at: Regex,
    dot: Regex,
    hash: Regex,
}

pub(crate) static EMAILS: LazyLock<Emails> = LazyLock::new(|| {
    let compile =
        |s| Regex::new(s).unwrap_or_else(|_| unreachable!("fixed email pattern is valid"));
    let at = r"[ \t]*(?:\([ \t]*at[ \t]*\)|\[[ \t]*at[ \t]*\]|[ \t]+at[ \t]+|@)[ \t]*";
    let dot = r"[ \t]*(?:\([ \t]*dot[ \t]*\)|\[[ \t]*dot[ \t]*\]|[ \t]+dot[ \t]+)[ \t]*";
    let local = r"[a-z0-9.!#$%&'*+/=?^_`{|}~\-]+";
    let domain = r"[a-z0-9\-]+";
    Emails {
        candidate: compile(&format!(
            "(?i){local}(?:{dot}{local})*(?:{at}|[ \\t]*#[ \\t]*)\
                {domain}(?:(?:\\.|{dot}){domain})+"
        )),
        at: compile(&format!("(?i){at}")),
        dot: compile(&format!("(?i){dot}")),
        hash: compile(r"[ \t]*#[ \t]*"),
    }
});

impl Emails {
    pub fn text(&self, text: &str) -> Vec<EmailEvidence> {
        let mut normalized = String::new();
        let mut positions = Vec::new();
        for (start, c) in text.char_indices() {
            let end = start + c.len_utf8();
            if matches!(c, '\u{200b}' | '\u{200c}' | '\u{200d}' | '\u{feff}') {
                continue;
            }
            let c = match c {
                '\u{ff01}'..='\u{ff5e}' => char::from_u32(c as u32 - 0xfee0).unwrap_or(c),
                '\u{3000}' => ' ',
                _ => c,
            };
            normalized.push(c);
            positions.extend(std::iter::repeat_n((start, end), c.len_utf8()));
        }
        self.candidate
            .find_iter(&normalized)
            .filter_map(|found| {
                let mut start = found.start();
                let end = found.end();
                if normalized[start..end].starts_with('\'') && normalized[end..].starts_with('\'') {
                    start += 1;
                }
                start += normalized[start..end].len()
                    - normalized[start..end].trim_start_matches('.').len();
                // Normalize only a complete address fragment; prose punctuation keeps its spaces.
                let raw = self.at.replace_all(&normalized[start..end], "@");
                let raw = self.dot.replace_all(&raw, ".");
                let raw = self.hash.replace_all(&raw, "#");
                if raw.contains('#') && !raw.contains('@') {
                    let line_start = normalized[..found.start()].rfind('\n').map_or(0, |p| p + 1);
                    let line_end = normalized[found.end()..]
                        .find('\n')
                        .map_or(normalized.len(), |p| found.end() + p);
                    let previous = normalized[..line_start]
                        .trim_end_matches('\n')
                        .rfind('\n')
                        .map_or(0, |p| p + 1);
                    let context = normalized[previous..line_end].to_lowercase();
                    if !["邮箱", "邮件", "email", "e-mail", "联系"]
                        .iter()
                        .any(|cue| context.contains(cue))
                    {
                        return None;
                    }
                }
                let address = if raw.contains('@') {
                    raw.to_ascii_lowercase()
                } else {
                    raw.replacen('#', "@", 1).to_ascii_lowercase()
                };
                cloud_api::is_email(&address).then(|| EmailEvidence {
                    address,
                    location: EmailLocation::Text {
                        start: positions[start].0,
                        end: positions[end - 1].1,
                    },
                })
            })
            .collect()
    }

    pub fn mailto(&self, href: &str, element: usize, offset: usize) -> Vec<EmailEvidence> {
        let Some((scheme, rest)) = href.split_once(':') else {
            return Vec::new();
        };
        if !scheme.eq_ignore_ascii_case("mailto") {
            return Vec::new();
        }
        let addresses = rest.split('?').next().unwrap_or_default();
        let addresses = percent_encoding::percent_decode_str(addresses).decode_utf8_lossy();
        self.text(&addresses)
            .into_iter()
            .map(|mut evidence| {
                evidence.location = EmailLocation::Attribute {
                    element,
                    attribute: "href".into(),
                    raw: href.into(),
                    offset,
                };
                evidence
            })
            .collect()
    }
}

pub(crate) fn cloudflare(encoded: &str) -> Option<String> {
    if encoded.len() < 4 || !encoded.len().is_multiple_of(2) || !encoded.is_ascii() {
        return None;
    }
    let key = u8::from_str_radix(&encoded[..2], 16).ok()?;
    let decoded: Option<Vec<u8>> = (2..encoded.len())
        .step_by(2)
        .map(|i| {
            u8::from_str_radix(&encoded[i..i + 2], 16)
                .ok()
                .map(|b| b ^ key)
        })
        .collect();
    let address = String::from_utf8(decoded?).ok()?.to_lowercase();
    cloud_api::is_email(&address).then_some(address)
}
