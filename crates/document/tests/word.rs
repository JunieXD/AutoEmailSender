#![cfg(test)]

use std::io::Cursor;

use office_oxide::docx::write::{DocxWriter, HfType, Run};
use office_oxide::ir::{
    Element, Image, ImageFormat, InlineContent, List, ListItem, Paragraph, Table, TableCell,
    TableRow, TextBox, TextSpan,
};

fn paragraph(text: &str) -> Element {
    Element::Paragraph(Paragraph {
        content: vec![InlineContent::Text(TextSpan {
            text: text.into(),
            ..Default::default()
        })],
        ..Default::default()
    })
}

fn bytes(writer: &DocxWriter) -> Vec<u8> {
    let mut data = Cursor::new(Vec::new());
    writer.write_to(&mut data).unwrap();
    data.into_inner()
}

#[tokio::test]
async fn word_keeps_only_letter_formats_and_literal_text() {
    let mut writer = DocxWriter::new();
    writer
        .add_heading("申请信", 1)
        .add_paragraph("")
        .add_ir_paragraph(
            &[
                Run::new("{导师姓名}老师："),
                Run::new("重点").bold(),
                Run::new(" 与 "),
                Run::new("研究").italic(),
                Run::new(" "),
                Run {
                    hyperlink: Some("http://example.com/(cv) {导师姓名}".into()),
                    ..Run::new("简历")
                },
                Run::new(" "),
                Run {
                    hyperlink: Some("javascript:alert(1)".into()),
                    ..Run::new("危险链接")
                },
                Run::new(" "),
                Run {
                    hyperlink: Some("HTTPS://example.com".into()),
                    ..Run::new("网站")
                },
                Run::new(" "),
                Run {
                    hyperlink: Some("mailto:me@example.com".into()),
                    ..Run::new("邮箱")
                },
            ],
            None,
        )
        .add_paragraph("* 原样\n_ 原样\n# 开头\n1. 文字")
        .add_rich_paragraph(&[Run::new(" 1"), Run::new(". 普通文字").color("FF0000")])
        .add_rich_paragraph(&[
            Run::new("甲").bold(),
            Run::new("乙").bold().italic(),
            Run::new("丙").italic(),
            Run::new("丁"),
        ])
        .add_ir_list(&List {
            items: vec![
                ListItem {
                    content: vec![paragraph("项目一")],
                    nested: Some(List {
                        ordered: true,
                        level: 1,
                        items: vec![ListItem {
                            content: vec![paragraph("子项目")],
                            nested: None,
                        }],
                        ..Default::default()
                    }),
                },
                ListItem {
                    content: vec![paragraph("项目二")],
                    nested: None,
                },
            ],
            ..Default::default()
        })
        .add_ir_list(&List {
            ordered: true,
            start_number: Some(3),
            items: vec![ListItem {
                content: vec![paragraph("第一组")],
                nested: None,
            }],
            ..Default::default()
        })
        .add_paragraph("")
        .add_ir_list(&List {
            ordered: true,
            items: vec![ListItem {
                content: vec![paragraph("重新计数")],
                nested: None,
            }],
            ..Default::default()
        })
        .add_table(&[vec!["学校", "方向"], vec!["示例大学", "机器学习"]])
        .add_ir_table(&Table {
            rows: vec![TableRow {
                cells: vec![TableCell {
                    content: vec![
                        paragraph("项目经历"),
                        Element::List(List {
                            items: vec![ListItem {
                                content: vec![paragraph("项目甲")],
                                nested: None,
                            }],
                            ..Default::default()
                        }),
                    ],
                    ..Default::default()
                }],
                ..Default::default()
            }],
            ..Default::default()
        })
        .add_ir_image(&Image {
            alt_text: Some("不导入图片".into()),
            data: Some(vec![
                137, 80, 78, 71, 13, 10, 26, 10, 0, 0, 0, 13, 73, 72, 68, 82, 0, 0, 0, 1, 0, 0, 0,
                1, 8, 4, 0, 0, 0, 181, 28, 12, 2, 0, 0, 0, 11, 73, 68, 65, 84, 120, 218, 99, 100,
                248, 15, 0, 1, 5, 1, 1, 39, 24, 227, 102, 0, 0, 0, 0, 73, 69, 78, 68, 174, 66, 96,
                130,
            ]),
            format: Some(ImageFormat::Png),
            ..Default::default()
        })
        .add_text_box(&TextBox {
            content: vec![paragraph("文本框")],
            ..Default::default()
        })
        .add_page_break()
        .add_paragraph("此致")
        .add_column_break()
        .add_paragraph("敬礼")
        .add_footnote(1, &[paragraph("不导入脚注")], None)
        .add_endnote(2, &[paragraph("不导入尾注")], None)
        .add_comment(3, None, &[paragraph("不导入批注")])
        .add_section_header(HfType::DefaultHeader, vec![paragraph("不导入页眉")])
        .add_section_header(HfType::DefaultFooter, vec![paragraph("不导入页脚")]);

    // A DOCX named .DOC also exercises the shared, content-based Word reader.
    let data = bytes(&writer);
    let imported = document::import_word_template("首封.DOC", data)
        .await
        .unwrap();
    assert_eq!(
        (imported.name.as_str(), imported.body_md.as_str()),
        (
            "首封",
            concat!(
                "申请信\n\n",
                "{导师姓名}老师：**重点** 与 *研究* ",
                "[简历](http://example.com/%28cv%29%20%7B导师姓名%7D) 危险链接 ",
                "[网站](HTTPS://example.com) [邮箱](mailto:me@example.com)\n\n",
                "\\* 原样\\\n\\_ 原样\\\n# 开头\\\n1\\. 文字\n\n",
                " 1\\. 普通文字\n\n",
                "**甲*乙***_丙_丁\n\n",
                "- 项目一\n  1. 子项目\n- 项目二\n\n",
                "3. 第一组\n\n1) 重新计数\n\n",
                "学校 方向\n\n示例大学 机器学习\n\n项目经历 项目甲\n\n文本框\n\n此致\n\n敬礼"
            )
        )
    );
}

#[tokio::test]
async fn empty_word_tells_the_user_to_choose_another_file() {
    let error = document::import_word_template("申请.docx", bytes(&DocxWriter::new()))
        .await
        .unwrap_err();
    assert!(error.contains("请换一个"), "{error}");
}
