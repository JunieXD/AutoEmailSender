use regex::Regex;

use crate::{CrawlError, EmailEvidence, EmailLocation};

pub(crate) struct Emails {
    candidate: Regex,
    at: Regex,
    dot: Regex,
    hash: Regex,
}

impl Emails {
    pub fn new() -> Result<Self, CrawlError> {
        let compile =
            |s| Regex::new(s).map_err(|e| CrawlError::Parse(format!("email pattern: {e}")));
        let at = r"[ \t]*(?:\([ \t]*at[ \t]*\)|\[[ \t]*at[ \t]*\]|[ \t]+at[ \t]+|@)[ \t]*";
        let dot = r"[ \t]*(?:\([ \t]*dot[ \t]*\)|\[[ \t]*dot[ \t]*\]|[ \t]+dot[ \t]+)[ \t]*";
        let local = r"[a-z0-9.!#$%&'*+/=?^_`{|}~\-]+";
        let domain = r"[a-z0-9\-]+";
        Ok(Self {
            candidate: compile(&format!(
                r"(?i){local}(?:{dot}{local})*(?:{at}|[ \t]*#[ \t]*){domain}(?:(?:\.|{dot}){domain})+"
            ))?,
            at: compile(&format!("(?i){at}"))?,
            dot: compile(&format!("(?i){dot}"))?,
            hash: compile(r"[ \t]*#[ \t]*")?,
        })
    }

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
                // Normalize only a complete address fragment; prose punctuation keeps its spaces.
                let raw = self.at.replace_all(found.as_str(), "@");
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
                mentor::is_email(&address).then(|| EmailEvidence {
                    address,
                    location: EmailLocation::Text {
                        start: positions[found.start()].0,
                        end: positions[found.end() - 1].1,
                    },
                })
            })
            .collect()
    }

    pub fn mailto(&self, href: &str, element: usize) -> Vec<EmailEvidence> {
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
    mentor::is_email(&address).then_some(address)
}
