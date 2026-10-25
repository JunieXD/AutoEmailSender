//! Checks before an email goes out (docs/design.md#联系): problems the user may fix or ignore.

use comrak::nodes::NodeValue;
use comrak::{Arena, Options, parse_document};
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
    Placeholder {
        name: String,
    },
    /// The body never addresses the mentor; `salutation`, a Markdown paragraph, would.
    NoSalutation {
        salutation: String,
    },
    /// The body speaks of an attachment, but there is none.
    NoAttachment,
    OtherMentor {
        name: String,
    },
    RecentlyContacted {
        count: i64,
        last_at: i64,
    },
    /// The domain of the mentor's address names no server to take mail.
    NoMailServer {
        domain: String,
    },
}

/// What follows a surname when addressing a mentor, as in 雷老师.
const TITLES: [&str; 9] = [
    "老师",
    "教授",
    "副教授",
    "助理教授",
    "博士",
    "研究员",
    "副研究员",
    "助理研究员",
    "讲师",
];

/// Surnames of two characters, the common ones.
const COMPOUND_SURNAMES: [&str; 20] = [
    "欧阳", "司马", "上官", "诸葛", "东方", "皇甫", "尉迟", "公孙", "慕容", "长孙", "宇文", "司徒",
    "夏侯", "轩辕", "令狐", "钟离", "端木", "南宫", "独孤", "呼延",
];

/// What is wrong with a letter to the mentor named `mentor`, who also goes by `aliases`.
pub fn letter(
    mentor: &str,
    aliases: &[String],
    subject: &str,
    body_md: &str,
    attached: bool,
) -> Vec<Problem> {
    let arena = Arena::new();
    let mut options = Options::default();
    options.extension.cjk_friendly_emphasis = true;
    let root = parse_document(&arena, body_md, &options);
    let mut body_text = String::new();
    for node in root.descendants() {
        match &node.data().value {
            NodeValue::Text(text) => body_text.push_str(text),
            NodeValue::Code(code) => body_text.push_str(&code.literal),
            NodeValue::CodeBlock(code) => body_text.push_str(&code.literal),
            NodeValue::HtmlInline(html) => body_text.push_str(html),
            NodeValue::HtmlBlock(html) => body_text.push_str(&html.literal),
            NodeValue::SoftBreak
            | NodeValue::LineBreak
            | NodeValue::Paragraph
            | NodeValue::Item(_) => body_text.push('\n'),
            _ => {}
        }
    }
    let mut problems: Vec<Problem> = template::PLACEHOLDERS
        .into_iter()
        .filter(|name| {
            let written = format!("{{{name}}}");
            subject.contains(&written) || body_text.contains(&written)
        })
        .map(|name| Problem::Placeholder { name: name.into() })
        .collect();
    for text in [subject, &body_text] {
        for rest in text.split("【请").skip(1) {
            if let Some((content, _)) = rest.split_once('】') {
                let prefix: String = content
                    .chars()
                    .filter(|c| !c.is_whitespace())
                    .take(2)
                    .collect();
                if prefix != "补充" {
                    continue;
                }
                let problem = Problem::Placeholder {
                    name: format!("【请{content}】"),
                };
                if !problems.contains(&problem) {
                    problems.push(problem);
                }
            }
        }
    }
    let addressed = std::iter::once(mentor)
        .chain(aliases.iter().map(String::as_str))
        .any(|name| addresses(body_md, name));
    if !body_md.trim().is_empty() && !addressed {
        problems.push(Problem::NoSalutation {
            salutation: salutation(mentor),
        });
    }
    if !attached && mentions_attachment(body_md) {
        problems.push(Problem::NoAttachment);
    }
    problems
}

pub fn recent(count: i64, last: Option<i64>, now: i64) -> Option<Problem> {
    last.filter(|at| now - at < 7 * 86_400_000)
        .map(|last_at| Problem::RecentlyContacted { count, last_at })
}

/// Other task mentors' names outside occurrences of the recipient's names or aliases.
pub fn other_mentors(
    mentor: &str,
    aliases: &[String],
    others: &[String],
    subject: &str,
    body: &str,
) -> Vec<Problem> {
    let texts = [subject, body].map(|text| {
        let text = text.to_lowercase();
        let own: Vec<_> = std::iter::once(mentor)
            .chain(aliases.iter().map(String::as_str))
            .flat_map(|name| {
                text.match_indices(&name.to_lowercase())
                    .map(|(start, matched)| start..start + matched.len())
                    .collect::<Vec<_>>()
            })
            .collect();
        (text, own)
    });
    let mut found = Vec::new();
    for name in others {
        let folded = name.to_lowercase();
        let english = name.chars().any(latin);
        let short = name.chars().count() <= 2;
        let present = texts.iter().any(|(text, own)| {
            text.match_indices(&folded).any(|(start, matched)| {
                let end = start + matched.len();
                if own
                    .iter()
                    .any(|span| span.start <= start && end <= span.end)
                {
                    return false;
                }
                if english {
                    let word = |c: char| latin(c) || c.is_ascii_digit() || c == '_';
                    !text[..start].ends_with(word) && !text[end..].starts_with(word)
                } else {
                    !short || TITLES.iter().any(|title| text[end..].starts_with(title))
                }
            })
        });
        if !found.contains(name) && present {
            found.push(name.clone());
        }
    }
    found
        .into_iter()
        .map(|name| Problem::OtherMentor { name })
        .collect()
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

/// As in "附件是我的简历" or "I have attached my CV".
fn mentions_attachment(body: &str) -> bool {
    body.contains("附件") || body.contains("附上") || body.to_lowercase().contains("attach")
}

/// Lowercased, of two letters or more: a single letter is an initial.
fn latin_words(text: &str) -> Vec<String> {
    text.split(|c: char| !latin(c))
        .filter(|w| w.chars().count() > 1)
        .map(str::to_lowercase)
        .collect()
}

/// A letter of the Latin alphabet, accented ones too, as in Inès or Łukasz.
fn latin(c: char) -> bool {
    c.is_ascii_alphabetic()
        || (c.is_alphabetic() && matches!(u32::from(c), 0xC0..=0x24F | 0x1E00..=0x1EFF))
}

fn salutation(name: &str) -> String {
    let written = template::markdown_text(name);
    if name.starts_with(latin) {
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
    #[test]
    fn recent_contact_warning_expires_after_seven_days() {
        let now = 1_800_000_000_000;
        assert_eq!(super::recent(0, None, now), None);
        assert_eq!(super::recent(2, Some(now - 7 * 86_400_000), now), None);
        assert_eq!(
            super::recent(2, Some(now - 7 * 86_400_000 + 1), now),
            Some(super::Problem::RecentlyContacted {
                count: 2,
                last_at: now - 7 * 86_400_000 + 1
            })
        );
    }

    use super::*;

    #[test]
    fn ordinary_words_and_names_inside_the_recipients_names_are_not_other_mentors() {
        let results: Vec<_> = [
            (
                "李明",
                vec![],
                "高峰",
                "李明老师：我的课题研究交通高峰时段的预测。",
            ),
            ("李明", vec![], "明", "李明老师：您好！"),
            ("李明", vec![], "明", "李明副教授：您好！"),
            ("李明", vec![], "Ann", "Annual reports"),
            (
                "李明",
                vec!["欧阳晓明".into()],
                "晓明",
                "欧阳晓明老师：您好！",
            ),
            (
                "李明",
                vec!["欧阳晓明".into()],
                "晓明",
                "欧阳晓明助理研究员：您好！",
            ),
            (
                "李明",
                vec!["Joann Smith".into()],
                "Ann Smith",
                "Dear JOANN SMITH,",
            ),
        ]
        .into_iter()
        .map(|(mentor, aliases, other, body)| {
            other_mentors(mentor, &aliases, &[other.into()], "申请", body)
        })
        .collect();
        assert!(results.iter().all(Vec::is_empty), "{results:?}");
    }

    #[test]
    fn other_full_english_names_are_case_insensitive_whole_words() {
        for body in [
            "Dear john smith, I would like to apply.",
            "I read John Smith's paper.",
        ] {
            assert_eq!(
                other_mentors("李明", &[], &["John Smith".into()], "申请", body),
                [Problem::OtherMentor {
                    name: "John Smith".into()
                }],
                "{body}"
            );
        }
        assert!(other_mentors("李明", &[], &["John Smith".into()], "", "John Smithson").is_empty());
        assert!(
            other_mentors(
                "JOHN SMITH",
                &[],
                &["John Smith".into()],
                "",
                "Dear john smith,"
            )
            .is_empty()
        );
    }

    #[test]
    fn short_chinese_names_need_a_title_and_long_names_do_not() {
        for (name, text) in [
            ("张伟", "张伟老师：您好！"),
            ("高峰", "高峰教授：您好！"),
            ("明", "明研究员：您好！"),
            ("王伟", "尊敬的王伟副教授：您好！"),
            ("王伟", "王伟副研究员：您好！"),
            ("王伟", "王伟助理教授：您好！"),
            ("王伟", "王伟助理研究员：您好！"),
            ("王伟", "王伟讲师：您好！"),
            ("张小伟", "我读过张小伟的论文。"),
        ] {
            for (subject, body) in [(text, ""), ("申请", text)] {
                assert_eq!(
                    other_mentors("李明", &[], &[name.into()], subject, body),
                    [Problem::OtherMentor { name: name.into() }],
                    "{text}"
                );
            }
        }
        // A short shared name can still be a separate address outside the recipient's full name.
        assert_eq!(
            other_mentors(
                "李明",
                &[],
                &["明".into()],
                "",
                "李明老师：明博士也参与了课题。"
            ),
            [Problem::OtherMentor { name: "明".into() }]
        );
    }

    #[test]
    fn other_task_names_are_reported_once_and_own_aliases_are_excluded() {
        let names = vec![
            "王伟".into(),
            "李明".into(),
            "李明".into(),
            "Wei Wang".into(),
        ];
        assert_eq!(
            other_mentors(
                "王伟",
                &["Wei Wang".into()],
                &names,
                "给李明",
                "李明老师：王伟 Wei Wang"
            ),
            vec![Problem::OtherMentor {
                name: "李明".into()
            }]
        );
        assert!(other_mentors("王伟", &[], &names, "申请", "王伟老师：您好").is_empty());
    }

    #[test]
    fn each_placeholder_left_is_one_problem() {
        assert_eq!(
            letter(
                "雷娜",
                &[],
                "{学院}申请",
                "雷老师：\n\n我在{学院}，{我的姓名}。",
                false
            ),
            [
                Problem::Placeholder {
                    name: "学院".into()
                },
                Problem::Placeholder {
                    name: "我的姓名".into()
                }
            ]
        );
    }

    #[test]
    fn student_decisions_are_unfilled_placeholders_until_replaced() {
        let marker = "【请补充：方便面谈的时间】";
        assert_eq!(
            letter(
                "王伟",
                &[],
                marker,
                &format!("王老师：{marker}，{marker}"),
                false
            ),
            [Problem::Placeholder {
                name: marker.into()
            }]
        );
        assert!(letter("王伟", &[], "", "王老师：周五下午方便面谈。", false).is_empty());
        assert!(letter("王伟", &[], "", "王老师：【请补充：尚未闭合", false).is_empty());
    }

    #[test]
    fn student_decision_markers_are_checked_as_visible_text() {
        for (body, marker) in [
            ("王老师：【重点】这是一段正常内容。", None),
            (
                "王老师：时间为【请补充：面谈时间】。",
                Some("【请补充：面谈时间】"),
            ),
            (
                "王老师：时间为【请补充:面谈时间】。",
                Some("【请补充:面谈时间】"),
            ),
            (
                "王老师：时间为【请**补充**：面谈时间】。",
                Some("【请补充：面谈时间】"),
            ),
            (
                "王老师：时间为【请补\n充：面谈时间】。",
                Some("【请补\n充：面谈时间】"),
            ),
            (
                "王老师：时间为【请[补充](https://example.edu)：面谈时间】。",
                Some("【请补充：面谈时间】"),
            ),
            (
                "王老师：时间为【**请补充：**面谈时间】。",
                Some("【请补充：面谈时间】"),
            ),
        ] {
            let expected: Vec<_> = marker
                .map(|name| Problem::Placeholder { name: name.into() })
                .into_iter()
                .collect();
            assert_eq!(letter("王伟", &[], "", body, false), expected, "{body}");
        }
    }

    #[test]
    fn a_letter_addresses_the_mentor_by_name_or_surname_and_title() {
        for (name, body) in [
            ("雷娜", "雷娜老师：您好！"),
            ("雷娜", "尊敬的雷教授："),
            ("雷娜", "雷副教授：您好！"),
            ("雷娜", "雷副研究员：您好！"),
            ("雷娜", "雷助理教授：您好！"),
            ("雷娜", "雷助理研究员：您好！"),
            ("雷娜", "雷讲师：您好！"),
            ("欧阳娜娜", "欧阳老师："),
            ("John Smith", "Dear Prof. smith,"),
            ("Wei Zhang", "Zhang老师您好！"),
            ("张伟（Wei Zhang）", "张老师您好！"),
            // Nothing to send yet, so nothing to address.
            ("雷娜", " \n"),
        ] {
            assert_eq!(letter(name, &[], "申请", body, false), [], "{body}");
        }
        let aliases = ["Na Lei".to_owned()];
        assert_eq!(
            letter("雷娜", &aliases, "申请", "Dear Prof. Lei,", false),
            []
        );
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
            // Not "in", which only starts the name.
            (
                "Inès Martin",
                "I am interested in your research.",
                "Dear Inès Martin,",
            ),
        ] {
            assert_eq!(
                letter(name, &[], "申请", body, false),
                [Problem::NoSalutation {
                    salutation: salutation.into()
                }],
                "{body}"
            );
        }
    }

    #[test]
    fn a_letter_that_speaks_of_an_attachment_has_one() {
        let body = "雷老师：\n\n随信附上我的简历。";
        assert_eq!(
            letter("雷娜", &[], "申请", body, false),
            [Problem::NoAttachment]
        );
        assert_eq!(letter("雷娜", &[], "申请", body, true), []);
        assert_eq!(
            letter(
                "John Smith",
                &[],
                "Application",
                "Dear Prof. Smith, my CV is Attached.",
                false
            ),
            [Problem::NoAttachment]
        );
    }
}
