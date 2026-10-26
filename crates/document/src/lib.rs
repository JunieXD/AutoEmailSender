//! Word documents: shared reading and import into the letter's supported formats.

use std::{borrow::Cow, io::Cursor};

use office_oxide::format::DocumentFormat;
use office_oxide::ir::{Element, InlineContent, List};
use serde::Serialize;
use ts_rs::TS;

/// Detect the Word format from the contents: a DOCX may have been renamed to .doc.
pub fn open_word(data: &[u8]) -> office_oxide::Result<office_oxide::Document> {
    let format = if data.starts_with(b"PK") {
        DocumentFormat::Docx
    } else {
        DocumentFormat::Doc
    };
    office_oxide::Document::from_reader(Cursor::new(data.to_vec()), format)
}

#[derive(Debug, Serialize, TS)]
#[serde(rename_all = "camelCase")]
pub struct WordTemplate {
    pub name: String,
    pub body_md: String,
}

/// Converts without saving; parsing stays off the async executor and a parser panic is an error.
pub async fn import_word_template(file_name: &str, data: Vec<u8>) -> Result<WordTemplate, String> {
    let Some((name, extension)) = file_name.rsplit_once('.') else {
        return Err("请选择 DOCX 或 DOC 文件。".into());
    };
    if !extension.eq_ignore_ascii_case("docx") && !extension.eq_ignore_ascii_case("doc") {
        return Err("请选择 DOCX 或 DOC 文件。".into());
    }
    let body_md = tokio::task::spawn_blocking(move || {
        let document = open_word(&data)?.to_ir();
        Ok::<_, office_oxide::OfficeError>(
            document
                .sections
                .iter()
                .map(|section| blocks(&section.elements))
                .filter(|text| !text.is_empty())
                .collect::<Vec<_>>()
                .join("\n\n"),
        )
    })
    .await
    .map_err(|error| unreadable(&error))?
    .map_err(|error| unreadable(&error))?;
    if body_md.trim().is_empty() {
        return Err("文件中没有可导入的文字，请换一个 Word 文件。".into());
    }
    Ok(WordTemplate {
        name: name.into(),
        body_md,
    })
}

fn unreadable(error: &impl std::fmt::Display) -> String {
    tracing::warn!("reading a Word template: {error}");
    "未能读取 Word 文件：文件可能已损坏、设有密码或不是 Word 格式，请换一个文件。".into()
}

fn blocks(elements: &[Element]) -> String {
    let mut out = Vec::new();
    let mut previous_list = None;
    for element in elements {
        let other_marker = matches!(element, Element::List(list)
            if previous_list == Some((list.ordered, false)));
        let text = match element {
            Element::Paragraph(paragraph) => inline(&paragraph.content),
            Element::Heading(heading) => inline(&heading.content),
            Element::TextBox(text_box) => blocks(&text_box.content),
            Element::List(list) => list_markdown(list, other_marker),
            Element::Table(table) => table
                .rows
                .iter()
                .map(|row| {
                    row.cells
                        .iter()
                        .map(|cell| table_cell(&cell.content))
                        .collect::<Vec<_>>()
                        .join(" ")
                })
                .filter(|text| !text.trim().is_empty())
                .collect::<Vec<_>>()
                .join("\n\n"),
            // Breaks are already paragraph boundaries; notes and decorations are not letter text.
            _ => String::new(),
        };
        if !text.trim().is_empty() {
            // Consecutive lists need different delimiters or Markdown would merge their counters.
            previous_list = match element {
                Element::List(list) => Some((list.ordered, other_marker)),
                _ => None,
            };
            out.push(text);
        }
    }
    out.join("\n\n")
}

fn table_cell(elements: &[Element]) -> String {
    elements
        .iter()
        .map(|element| match element {
            Element::List(list) => table_list(list),
            Element::TextBox(text_box) => table_cell(&text_box.content),
            Element::Table(table) => table
                .rows
                .iter()
                .flat_map(|row| &row.cells)
                .map(|cell| table_cell(&cell.content))
                .collect::<Vec<_>>()
                .join(" "),
            _ => blocks(std::slice::from_ref(element)),
        })
        .filter(|text| !text.trim().is_empty())
        .collect::<Vec<_>>()
        .join(" ")
}

fn table_list(list: &List) -> String {
    list.items
        .iter()
        .map(|item| {
            let mut text = table_cell(&item.content);
            if let Some(nested) = &item.nested {
                text.push(' ');
                text.push_str(&table_list(nested));
            }
            text
        })
        .collect::<Vec<_>>()
        .join(" ")
}

fn list_markdown(list: &List, other_marker: bool) -> String {
    list.items
        .iter()
        .enumerate()
        .filter_map(|(index, item)| {
            let mut body = blocks(&item.content);
            if let Some(nested) = &item.nested {
                let nested = list_markdown(nested, false);
                if !nested.is_empty() {
                    body.push('\n');
                    body.push_str(&nested);
                }
            }
            if body.trim().is_empty() {
                return None;
            }
            let marker = if list.ordered {
                format!(
                    "{}{} ",
                    u64::from(list.start_number.unwrap_or(1)) + index as u64,
                    if other_marker { ')' } else { '.' },
                )
            } else if other_marker {
                "+ ".into()
            } else {
                "- ".into()
            };
            let indent = " ".repeat(marker.len());
            Some(format!(
                "{marker}{}",
                body.replace('\n', &format!("\n{indent}"))
            ))
        })
        .collect::<Vec<_>>()
        .join("\n")
}

#[derive(Clone, Copy, PartialEq)]
enum Mark<'a> {
    Bold,
    Italic,
    Link(&'a str),
}

struct Part<'a> {
    text: Cow<'a, str>,
    marks: Vec<Mark<'a>>,
}

struct Open<'a> {
    mark: Mark<'a>,
    close: String,
}

fn inline(content: &[InlineContent]) -> String {
    let mut parts: Vec<Part<'_>> = Vec::new();
    for item in content {
        match item {
            InlineContent::Text(span) => {
                let mut marks = Vec::new();
                if span.bold {
                    marks.push(Mark::Bold);
                }
                if span.italic {
                    marks.push(Mark::Italic);
                }
                if let Some(address) = &span.hyperlink {
                    let scheme = address.split_once(':').map(|(scheme, _)| scheme);
                    if scheme.is_some_and(|scheme| {
                        ["http", "https", "mailto"]
                            .iter()
                            .any(|allowed| scheme.eq_ignore_ascii_case(allowed))
                    }) {
                        marks.push(Mark::Link(address));
                    }
                }
                for (index, text) in span.text.split('\n').enumerate() {
                    if index > 0 {
                        parts.push(Part {
                            text: "\n".into(),
                            marks: Vec::new(),
                        });
                    }
                    // Symbol-font NUL is not text and would cut off SQLite text functions.
                    let text = if text.contains('\0') {
                        Cow::Owned(text.replace('\0', ""))
                    } else {
                        Cow::Borrowed(text)
                    };
                    if text.is_empty() {
                        continue;
                    }
                    // Word splits text for fonts too; a literal list marker may span those runs.
                    if let Some(previous) = parts
                        .last_mut()
                        .filter(|part| part.text != "\n" && part.marks == marks)
                    {
                        previous.text.to_mut().push_str(&text);
                    } else {
                        parts.push(Part {
                            text,
                            marks: marks.clone(),
                        });
                    }
                }
            }
            InlineContent::LineBreak => parts.push(Part {
                text: "\n".into(),
                marks: Vec::new(),
            }),
            _ => {}
        }
    }
    let extent = |mark: Mark<'_>, index: usize| {
        parts[index..]
            .iter()
            .take_while(|part| part.marks.contains(&mark))
            .count()
    };
    let mut out = String::new();
    let mut open: Vec<Open<'_>> = Vec::new();
    let mut space = "";
    for (index, part) in parts.iter().enumerate() {
        let keep = open
            .iter()
            .take_while(|mark| part.marks.contains(&mark.mark))
            .count();
        let closed = keep < open.len();
        for mark in open.drain(keep..).rev() {
            out.push_str(&mark.close);
        }
        out.push_str(space);
        space = "";
        if part.text == "\n" {
            if parts[index + 1..]
                .iter()
                .any(|part| !part.text.trim().is_empty())
            {
                out.push_str("\\\n");
            }
            continue;
        }
        let middle = part.text.trim();
        if middle.is_empty() {
            out.push_str(&part.text);
            continue;
        }
        let mut opening: Vec<_> = part
            .marks
            .iter()
            .copied()
            .filter(|mark| !open.iter().any(|opened| opened.mark == *mark))
            .collect();
        opening.sort_by_key(|mark| std::cmp::Reverse(extent(*mark, index)));
        if !opening.is_empty() {
            out.push_str(&part.text[..part.text.len() - part.text.trim_start().len()]);
        }
        for (k, mark) in opening.iter().copied().enumerate() {
            // Like the editor, use underscores where adjacent star delimiters would merge.
            let after_closing = k == 0 && closed && out.ends_with('*');
            let inner = opening[k + 1..]
                .iter()
                .find(|mark| !matches!(mark, Mark::Link(_)));
            let reopens = inner.is_some_and(|inner| {
                parts[index + extent(*inner, index)..index + extent(mark, index)]
                    .iter()
                    .any(|part| part.marks.contains(inner))
            });
            let close = match mark {
                Mark::Bold => if after_closing || reopens { "__" } else { "**" }.into(),
                Mark::Italic => if after_closing || reopens { "_" } else { "*" }.into(),
                Mark::Link(address) => format!("]({})", link_address(address)),
            };
            out.push_str(if matches!(mark, Mark::Link(_)) {
                "["
            } else {
                &close
            });
            open.push(Open { mark, close });
        }
        let text = if opening.is_empty() {
            part.text.as_ref()
        } else {
            part.text.trim_start()
        };
        let changes = parts
            .get(index + 1)
            .is_none_or(|next| next.marks != part.marks);
        if changes && !part.marks.is_empty() {
            space = &text[text.trim_end().len()..];
        }
        let body = if space.is_empty() {
            text
        } else {
            text.trim_end()
        };
        let text = body.trim_start();
        out.push_str(&body[..body.len() - text.len()]);
        out.push_str(&template::markdown_text(text));
    }
    for mark in open.iter().rev() {
        out.push_str(&mark.close);
    }
    out.push_str(space);
    out
}

fn link_address(address: &str) -> String {
    let mut out = String::new();
    for character in address.chars() {
        match character {
            '(' => out.push_str("%28"),
            ')' => out.push_str("%29"),
            '<' => out.push_str("%3C"),
            '>' => out.push_str("%3E"),
            ' ' => out.push_str("%20"),
            '{' => out.push_str("%7B"),
            '}' => out.push_str("%7D"),
            _ => out.push(character),
        }
    }
    out
}
