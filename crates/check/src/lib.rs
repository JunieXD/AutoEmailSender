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
    /// The domain of the mentor's address names no server to take mail.
    NoMailServer { domain: String },
}

/// What follows a surname when addressing a mentor, as in 雷老师.
const TITLES: [&str; 4] = ["老师", "教授", "博士", "研究员"];

/// Surnames of two characters, the common ones.
const COMPOUND_SURNAMES: [&str; 20] = [
    "欧阳", "司马", "上官", "诸葛", "东方", "皇甫", "尉迟", "公孙", "慕容", "长孙", "宇文", "司徒",
    "夏侯", "轩辕", "令狐", "钟离", "端木", "南宫", "独孤", "呼延",
];

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

/// Whether the body addresses the mentor: by full name, by surname and title, or by a word of the
/// name in Latin letters, as in "Dear Prof. Smith" or "Zhang老师".
fn addresses(body: &str, name: &str) -> bool {
    let length = if COMPOUND_SURNAMES.iter().any(|s| name.starts_with(s)) {
        2
    } else {
        1
    };
    let surname: String = name.chars().take(length).collect();
    let said = latin_words(body);
    body.contains(name)
        || TITLES
            .iter()
            .any(|title| body.contains(&format!("{surname}{title}")))
        || latin_words(name).iter().any(|w| said.contains(w))
}

/// Lowercased, of two letters or more: a single letter is an initial.
fn latin_words(text: &str) -> Vec<String> {
    text.split(|c: char| !c.is_ascii_alphabetic())
        .filter(|w| w.len() > 1)
        .map(str::to_ascii_lowercase)
        .collect()
}

fn salutation(name: &str) -> String {
    let written = template::markdown_text(name);
    if name.starts_with(|c: char| c.is_ascii_alphabetic()) {
        format!("Dear {written},")
    } else {
        format!("{written}老师：")
    }
}

/// What DNS tells of the domain of `address`. MX records alone count: through a system proxy that
/// answers with fake addresses, every name has an A record. A domain may take mail at its A record
/// instead, but so few do that a missing MX more likely means a mistyped address.
pub async fn mail_domain(address: &str) -> Result<Option<Problem>, NetError> {
    let Some((_, domain)) = address.rsplit_once('@') else {
        return Ok(None);
    };
    // Built for each lookup, so that it follows the system's DNS settings as networks change.
    let resolver = Resolver::builder_tokio()?.build()?;
    // Fully qualified, so that the system's search domains are not tried.
    let servers = match resolver.mx_lookup(format!("{domain}.").as_str()).await {
        // A domain that takes no mail says so with the single exchange "." (RFC 7505).
        Ok(mx) => mx
            .answers()
            .iter()
            .any(|r| matches!(&r.data, RData::MX(mx) if !mx.exchange.is_root())),
        // Also when the domain does not exist.
        Err(e) if e.is_no_records_found() => false,
        Err(e) => return Err(e),
    };
    Ok((!servers).then(|| Problem::NoMailServer {
        domain: domain.to_owned(),
    }))
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
            ("Wei Zhang", "Zhang老师您好！"),
            ("张伟（Wei Zhang）", "张老师您好！"),
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
            ("欧阳娜娜", "欧老师：", "欧阳娜娜老师："),
            ("陈晓明", "陈晓老师：", "陈晓明老师："),
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
