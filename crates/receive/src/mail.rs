//! What a message says, read from its header fields and the start of the message
//! (docs/design.md#桌面端). Only reads bytes: the rest of the crate decides what to do with it.

use std::borrow::Cow;

use html2text::render::RichAnnotation;
use mail_parser::{AddressList, MessageParser, PartKind};

/// The header fields `headers` reads, fetched instead of whole messages.
pub(crate) const FIELDS: [&str; 13] = [
    "FROM",
    "TO",
    "CC",
    "BCC",
    "MESSAGE-ID",
    "IN-REPLY-TO",
    "REFERENCES",
    "SUBJECT",
    "DATE",
    "CONTENT-TYPE",
    "AUTO-SUBMITTED",
    "X-AUTOREPLY",
    "X-AUTORESPOND",
];

/// What a message's header fields say. Message-IDs are in angle brackets, as the app writes its
/// own; addresses are in lowercase.
#[derive(Debug, Default, PartialEq, Eq)]
pub(crate) struct Headers {
    pub message_id: Option<String>,
    pub in_reply_to: Option<String>,
    /// References, separated by spaces.
    pub references: Option<String>,
    pub from: Option<String>,
    /// To, Cc and Bcc.
    pub recipients: Vec<String>,
    pub subject: Option<String>,
    /// UTC milliseconds.
    pub date: Option<i64>,
    /// Sent by a program on the sender's behalf, such as an out-of-office notice.
    pub automatic: bool,
    /// From a mail system, or a delivery status report: perhaps a bounce.
    pub report: bool,
}

impl Headers {
    /// The Message-IDs this message follows up on.
    pub fn thread(&self) -> Vec<String> {
        let references = self.references.iter().flat_map(|r| r.split(' '));
        self.in_reply_to
            .iter()
            .map(String::as_str)
            .chain(references)
            .map(str::to_owned)
            .collect()
    }
}

/// Subjects that out-of-office and other automatic replies start with, in lowercase.
const AUTOMATIC_SUBJECTS: [&str; 7] = [
    "自动回复",
    "自动答复",
    "automatic reply",
    "auto reply",
    "auto-reply",
    "autoreply",
    "out of office",
];

pub(crate) fn headers(raw: &[u8]) -> Headers {
    let Some(message) = MessageParser::new().parse_headers(raw) else {
        return Headers::default();
    };
    let fields = message.headers();
    let raw_value = |name: &str| {
        fields.get(name).map(|h| {
            String::from_utf8_lossy(h.raw_value())
                .trim()
                .to_ascii_lowercase()
        })
    };
    let subject = message.subject().map(|s| s.trim().to_owned());
    let lowercase_subject = subject.as_deref().unwrap_or_default().to_lowercase();
    let automatic = raw_value("Auto-Submitted")
        .is_some_and(|v| v.split(';').next().is_some_and(|v| v.trim() != "no"))
        || fields.contains("X-Autoreply")
        || fields.contains("X-Autorespond")
        || AUTOMATIC_SUBJECTS
            .iter()
            .any(|s| lowercase_subject.starts_with(s));
    let from = message
        .from()
        .and_then(|list| list.first())
        .and_then(|m| m.address().map(str::to_lowercase));
    let system = from.as_deref().is_some_and(|a| {
        let local = a.split('@').next().unwrap_or_default();
        local == "mailer-daemon" || local == "postmaster"
    });
    let delivery_report = message.content_type().is_some_and(|t| {
        t.ctype().eq_ignore_ascii_case("multipart")
            && t.subtype()
                .is_some_and(|s| s.eq_ignore_ascii_case("report"))
            && t.attribute("report-type")
                .is_some_and(|r| r.eq_ignore_ascii_case("delivery-status"))
    });
    let references: Vec<String> = message
        .references()
        .iter()
        .flat_map(|list| list.iter())
        .map(bracketed)
        .collect();
    Headers {
        message_id: message.message_id().map(bracketed),
        in_reply_to: message
            .in_reply_to()
            .and_then(|list| list.first())
            .map(bracketed),
        references: (!references.is_empty()).then(|| references.join(" ")),
        from,
        recipients: [message.to(), message.cc(), message.bcc()]
            .iter()
            .flatten()
            .flat_map(addresses)
            .collect(),
        subject: subject.filter(|s| !s.is_empty()),
        date: message.date().map(|d| d.to_timestamp() * 1000),
        automatic,
        report: system || delivery_report,
    }
}

fn addresses(list: &AddressList) -> Vec<String> {
    list.mailboxes()
        .filter_map(|m| m.address().map(str::to_lowercase))
        .collect()
}

fn bracketed(id: &str) -> String {
    format!("<{}>", id.trim_matches(['<', '>']))
}

/// The message's text: its plain-text part, or else its HTML part as text.
pub(crate) fn text(raw: &[u8]) -> Option<String> {
    let message = MessageParser::new().parse(raw)?;
    let part = message.text_body().next()?;
    let text = part.text()?;
    match part.kind() {
        PartKind::Html => html_text(&text),
        _ => Some(text.replace("\r\n", "\n")),
    }
}

/// HTML as text: paragraphs and line breaks kept, quotes marked with `>`, and the address of a link
/// written after its words, which may not say where it goes.
fn html_text(html: &str) -> Option<String> {
    let lines = html2text::config::rich()
        .raw_mode(true)
        .no_link_wrapping()
        // Wide enough that no line wraps.
        .lines_from_read(html.as_bytes(), usize::MAX / 2)
        .ok()?;
    let mut text = String::new();
    for line in lines {
        let mut link: Option<String> = None;
        for piece in line.tagged_strings() {
            let target = piece.tag.iter().find_map(|t| match t {
                RichAnnotation::Link(url) => Some(url.clone()),
                _ => None,
            });
            if link != target {
                end_link(&mut text, link.take());
                link = target;
            }
            text += &piece.s.replace('\u{a0}', " ");
        }
        end_link(&mut text, link);
        text += "\n";
    }
    Some(text.trim_end().to_owned())
}

/// Writes where a link goes after its words, unless they already say it.
fn end_link(text: &mut String, link: Option<String>) {
    if let Some(url) = link {
        let shown = url.strip_prefix("mailto:").unwrap_or(&url);
        if !text.ends_with(shown) {
            *text += &format!(" ({shown})");
        }
    }
}

/// Mail that came back: the Message-IDs it mentions, the bounced one among them, and why.
#[derive(Debug, PartialEq, Eq)]
pub(crate) struct Bounce {
    pub originals: Vec<String>,
    pub reason: String,
}

/// What the start of a possible bounce says: None when it only reports a delay, or delivery.
pub(crate) fn bounce(raw: &[u8]) -> Option<Bounce> {
    let message = MessageParser::new().parse(raw)?;
    let whole = String::from_utf8_lossy(raw);
    // Delivery status fields are plain text, wherever the report puts them.
    let fields = |name: &'static str| {
        whole.lines().filter_map(move |line| {
            let line = line.trim_start();
            line.get(..name.len())
                .filter(|start| start.eq_ignore_ascii_case(name))
                .map(|_| line[name.len()..].trim())
        })
    };
    let actions: Vec<&str> = fields("Action:").collect();
    if !actions.is_empty() && !actions.iter().any(|a| a.eq_ignore_ascii_case("failed")) {
        return None;
    }
    let own = message.message_id().map(bracketed);
    let nested = message
        .messages()
        .skip(1)
        .filter_map(|m| m.message_id().map(bracketed));
    let quoted = fields("Message-ID:").filter_map(|v| {
        let id = v.split_whitespace().next()?;
        Some(bracketed(id))
    });
    let thread = message
        .in_reply_to()
        .iter()
        .chain(message.references().iter())
        .flat_map(|list| list.iter().map(bracketed).collect::<Vec<_>>())
        .collect::<Vec<_>>();
    let mut originals: Vec<String> = Vec::new();
    for id in nested.chain(quoted).chain(thread) {
        if Some(&id) != own.as_ref() && !originals.contains(&id) {
            originals.push(id);
        }
    }
    let diagnostic = fields("Diagnostic-Code:").next().map(|d| {
        let d = d.split_once(';').map_or(d, |(kind, rest)| {
            if kind.trim().eq_ignore_ascii_case("smtp") {
                rest
            } else {
                d
            }
        });
        d.trim().to_owned()
    });
    let status = fields("Status:").next().map(str::to_owned);
    let body = message.body_text(0).unwrap_or(Cow::Borrowed(""));
    let said = format!(
        "{} {} {} {body}",
        diagnostic.as_deref().unwrap_or_default(),
        status.as_deref().unwrap_or_default(),
        message.subject().unwrap_or_default(),
    )
    .to_lowercase();
    let category = CAUSES
        .iter()
        .find(|(_, words)| words.iter().any(|w| said.contains(w)))
        .map_or("邮件被退回", |(cause, _)| cause);
    let detail = diagnostic
        .or(status)
        .map(|d| d.chars().take(150).collect::<String>());
    Some(Bounce {
        originals,
        reason: match detail {
            Some(d) => format!("{category}（{d}）"),
            None => category.to_owned(),
        },
    })
}

/// Why mail bounces, by what the report says, in lowercase; the first that matches wins.
const CAUSES: [(&str, &[&str]); 3] = [
    (
        "收件地址不存在",
        &[
            "5.1.1",
            "5.1.0",
            "user unknown",
            "unknown user",
            "no such user",
            "does not exist",
            "doesn't exist",
            "user not found",
            "recipient not found",
            "address rejected",
            "invalid recipient",
            "mailbox not found",
            "不存在",
            "无此用户",
        ],
    ),
    (
        "对方邮箱已满",
        &[
            "5.2.2",
            "mailbox full",
            "over quota",
            "quota exceeded",
            "空间不足",
            "邮箱已满",
        ],
    ),
    (
        "被对方拒收",
        &[
            "5.7.",
            "spam",
            "blocked",
            "blacklist",
            "policy",
            "拒收",
            "拒绝",
            "垃圾邮件",
        ],
    ),
];

#[cfg(test)]
mod tests {
    use super::{headers, text};

    #[test]
    fn chinese_encodings_are_read() {
        let raw = "From: li@pku.edu.cn\r\nSubject: =?gb2312?B?u9i4tKO6yerH6w==?=\r\nContent-Type: text/plain; charset=gbk\r\nContent-Transfer-Encoding: base64\r\n\r\nwO7Az8qmo7q7ttOtsai/vKGj\r\n";
        assert_eq!(
            headers(raw.as_bytes()).subject.as_deref(),
            Some("回复：申请")
        );
        assert_eq!(text(raw.as_bytes()).as_deref(), Some("李老师：欢迎报考。"));
    }

    #[test]
    fn html_reads_as_lines_with_where_links_go() {
        let raw = "Content-Type: text/html; charset=utf-8\r\n\r\n<html><head><style>p{color:red}</style></head><body><div>林晓同学：</div><div>请&nbsp;<a href=\"https://lab.example.com/join\">填写<b>报名表</b></a>，或写信到 <a href=\"mailto:li@pku.edu.cn\">li@pku.edu.cn</a>。</div><blockquote>李老师：您好！</blockquote></body></html>";
        assert_eq!(
            text(raw.as_bytes()).as_deref(),
            Some(
                "林晓同学：\n请 填写报名表 (https://lab.example.com/join)，或写信到 li@pku.edu.cn。\n\n> 李老师：您好！"
            )
        );
    }
}
