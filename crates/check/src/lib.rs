//! Checks before an email goes out (docs/design.md#联系): problems the user may fix or ignore.

use hickory_resolver::Resolver;
use hickory_resolver::net::NetError;
use hickory_resolver::proto::rr::RData;
use serde::Serialize;
use ts_rs::TS;

#[derive(Debug, PartialEq, Eq, Serialize, TS)]
#[serde(
    tag = "kind",
    rename_all = "snake_case",
    rename_all_fields = "camelCase"
)]
pub enum Problem {
    /// A placeholder left as written, as what fills it was missing.
    Placeholder { name: &'static str },
    /// The body never addresses the mentor; `salutation`, a Markdown paragraph, would.
    NoSalutation { salutation: String },
    /// The domain of the mentor's address cannot receive mail.
    CannotReceive { domain: String },
}

/// What follows a surname when addressing a mentor, as in 雷老师.
const TITLES: [&str; 4] = ["老师", "教授", "博士", "研究员"];

/// What is wrong with a letter to the mentor named `mentor`.
pub fn letter(mentor: &str, subject: &str, body_md: &str) -> Vec<Problem> {
    let mut problems: Vec<Problem> = template::PLACEHOLDERS
        .into_iter()
        .filter(|name| {
            let written = format!("{{{name}}}");
            subject.contains(&written) || body_md.contains(&written)
        })
        .map(|name| Problem::Placeholder { name })
        .collect();
    if !body_md.trim().is_empty() && !addresses(body_md, mentor) {
        problems.push(Problem::NoSalutation {
            salutation: salutation(mentor),
        });
    }
    problems
}

/// Whether the body addresses the mentor: by full name, or by surname and title; a name in Latin
/// letters by any of its words.
fn addresses(body: &str, name: &str) -> bool {
    if body.contains(name) {
        return true;
    }
    if latin(name) {
        let words: Vec<String> = body
            .split(|c: char| !c.is_alphabetic())
            .map(str::to_lowercase)
            .collect();
        return name
            .split(|c: char| !c.is_alphabetic())
            .filter(|part| part.chars().count() > 1)
            .any(|part| words.contains(&part.to_lowercase()));
    }
    // A compound surname, such as 欧阳, is two characters.
    (1..=2).any(|n| {
        let surname: String = name.chars().take(n).collect();
        TITLES
            .iter()
            .any(|title| body.contains(&format!("{surname}{title}")))
    })
}

fn latin(name: &str) -> bool {
    name.chars().any(|c| c.is_ascii_alphabetic())
}

fn salutation(name: &str) -> String {
    let written = template::markdown_text(name);
    if latin(name) {
        format!("Dear {written},")
    } else {
        format!("{written}老师：")
    }
}

/// Whether mail to `address` would find no server, as far as DNS tells. A lookup that fails, such
/// as when offline, tells nothing.
pub async fn mail_domain(address: &str) -> Option<Problem> {
    let (_, domain) = address.rsplit_once('@')?;
    match receives(domain).await {
        Ok(true) => None,
        Ok(false) => Some(Problem::CannotReceive {
            domain: domain.to_owned(),
        }),
        Err(e) => {
            tracing::warn!("looking up a mail domain: {e}");
            None
        }
    }
}

/// Going by MX records alone: through a system proxy that answers with fake addresses, every name
/// has an A record.
async fn receives(domain: &str) -> Result<bool, NetError> {
    // Built for each lookup, so that it follows the system's DNS settings as networks change.
    let resolver = Resolver::builder_tokio()?.build()?;
    // Fully qualified, so that the system's search domains are not tried.
    match resolver.mx_lookup(format!("{domain}.").as_str()).await {
        // A domain that takes no mail says so with the single exchange "." (RFC 7505).
        Ok(mx) => Ok(mx
            .answers()
            .iter()
            .any(|r| matches!(&r.data, RData::MX(mx) if !mx.exchange.is_root()))),
        // Also when the domain does not exist.
        Err(e) if e.is_no_records_found() => Ok(false),
        Err(e) => Err(e),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn each_placeholder_left_is_one_problem() {
        assert_eq!(
            letter("雷娜", "{学院}申请", "雷老师：\n\n我在{学院}，{我的姓名}。"),
            [
                Problem::Placeholder { name: "学院" },
                Problem::Placeholder {
                    name: "我的姓名"
                }
            ]
        );
    }

    #[test]
    fn a_letter_addresses_the_mentor_by_name_or_surname_and_title() {
        for (name, body) in [
            ("雷娜", "雷娜老师：您好！"),
            ("雷娜", "尊敬的雷教授："),
            ("欧阳娜娜", "欧阳老师："),
            ("John Smith", "Dear Prof. smith,"),
            // Nothing to send yet, so nothing to address.
            ("雷娜", " \n"),
        ] {
            assert_eq!(letter(name, "申请", body), [], "{body}");
        }
    }

    #[test]
    fn a_letter_that_never_addresses_the_mentor_gets_a_salutation() {
        for (name, body, salutation) in [
            ("雷娜", "您好！我做雷达方面的研究。", "雷娜老师："),
            // A word that only starts with the name is not the name.
            ("Li Wei", "I would like to apply.", "Dear Li Wei,"),
            ("A*B", "Hello", r"Dear A\*B,"),
        ] {
            assert_eq!(
                letter(name, "申请", body),
                [Problem::NoSalutation {
                    salutation: salutation.into()
                }],
                "{body}"
            );
        }
    }
}
