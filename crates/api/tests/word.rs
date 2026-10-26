#![cfg(test)]

use api::{Context, ErrorCode, call};
use base64::prelude::*;
use office_oxide::docx::write::{DocxWriter, HfType, Run};
use office_oxide::ir::{
    Element, Image, ImageFormat, InlineContent, List, ListItem, Paragraph, Table, TableCell,
    TableRow, TextBox, TextSpan,
};
use serde_json::json;
use sqlx::SqlitePool;
use std::io::Cursor;

fn context() -> Context {
    Context::new(
        SqlitePool::connect_lazy("sqlite::memory:").unwrap(),
        net::Net::new(vec![]).unwrap(),
        Default::default(),
    )
}

#[tokio::test]
async fn damaged_word_upload_returns_an_actionable_error() {
    let ctx = context();
    for data in [b"PK broken Word file".as_slice(), b"not a Word file"] {
        let error = call(
            &ctx,
            "importWordTemplate",
            json!({"fileName": "申请.docx", "data": BASE64_STANDARD.encode(data)}),
        )
        .await
        .unwrap_err();
        assert!(matches!(error.code, ErrorCode::Invalid));
        assert!(error.message.contains("请换一个"), "{}", error.message);
    }
}

#[tokio::test]
async fn word_upload_returns_its_name_and_formatted_html() {
    let mut writer = DocxWriter::new();
    writer.add_ir_paragraph(
        &[
            Run::new("重点").bold(),
            Run {
                hyperlink: Some("https://example.com/cv".into()),
                ..Run::new("简历")
            },
        ],
        None,
    );
    let mut data = Cursor::new(Vec::new());
    writer.write_to(&mut data).unwrap();
    let result = call(
        &context(),
        "importWordTemplate",
        json!({"fileName": "申请.DOC", "data": BASE64_STANDARD.encode(data.into_inner())}),
    )
    .await
    .unwrap();
    assert_eq!(result["name"], "申请");
    let html = result["html"].as_str().unwrap();
    assert!(html.contains("<strong>"));
    assert!(html.contains("href=\"https://example.com/cv\""));
}

fn paragraph(text: &str) -> Element {
    Element::Paragraph(Paragraph {
        content: vec![InlineContent::Text(TextSpan::plain(text))],
        ..Default::default()
    })
}

#[tokio::test]
async fn word_upload_imports_only_the_letter_body() {
    let image = Image {
        alt_text: Some("IMAGE 私人图片说明".into()),
        data: Some(
            BASE64_STANDARD
                .decode(concat!(
                    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAAC0lEQVR4nGP4",
                    "DwQACfsD/fteaysAAAAASUVORK5CYII="
                ))
                .unwrap(),
        ),
        format: Some(ImageFormat::Png),
        ..Default::default()
    };
    for with_body in [true, false] {
        let mut writer = DocxWriter::new();
        writer.add_footnote(1, &[paragraph("FOOTNOTE 私人备注")], None);
        writer.add_endnote(2, &[paragraph("ENDNOTE 内部说明")], None);
        writer.add_comment(3, Some("审阅人".into()), &[paragraph("COMMENT 不要发出")]);
        for hf_type in [
            HfType::DefaultHeader,
            HfType::FirstPageHeader,
            HfType::EvenPageHeader,
            HfType::DefaultFooter,
            HfType::FirstPageFooter,
            HfType::EvenPageFooter,
        ] {
            writer.add_section_header(hf_type, vec![paragraph("HEADER FOOTER 私人页眉页脚")]);
        }
        writer.add_ir_image(&image);
        writer.add_rich_paragraph(&[
            Run {
                footnote_ref: Some(1),
                ..Default::default()
            },
            Run {
                endnote_ref: Some(2),
                ..Default::default()
            },
        ]);
        writer.add_list(&[""], false);
        if with_body {
            writer.add_heading("申请正文", 1);
            writer.add_ir_table(&Table {
                rows: vec![TableRow {
                    cells: vec![TableCell {
                        content: vec![paragraph("表格正文"), Element::Image(image.clone())],
                        col_span: 1,
                        row_span: 1,
                        ..Default::default()
                    }],
                    ..Default::default()
                }],
                ..Default::default()
            });
            writer.add_ir_list(&List {
                items: vec![ListItem {
                    content: vec![paragraph("列表正文")],
                    nested: Some(List {
                        items: vec![ListItem {
                            content: vec![paragraph("子列表正文")],
                            ..Default::default()
                        }],
                        ..Default::default()
                    }),
                }],
                ..Default::default()
            });
            writer.add_text_box(&TextBox {
                content: vec![paragraph("文本框正文"), Element::Image(image.clone())],
                ..Default::default()
            });
        }
        let mut data = Cursor::new(Vec::new());
        writer.write_to(&mut data).unwrap();
        let result = call(
            &context(),
            "importWordTemplate",
            json!({"fileName": "申请.docx", "data": BASE64_STANDARD.encode(data.into_inner())}),
        )
        .await;
        if with_body {
            let result = result.unwrap();
            let html = result["html"].as_str().unwrap();
            for text in [
                "申请正文",
                "表格正文",
                "列表正文",
                "子列表正文",
                "文本框正文",
            ] {
                assert!(html.contains(text), "{html}");
            }
            for text in [
                "FOOTNOTE",
                "ENDNOTE",
                "COMMENT",
                "审阅人",
                "HEADER",
                "FOOTER",
                "IMAGE",
            ] {
                assert!(!html.contains(text), "{html}");
            }
        } else {
            let error = result.unwrap_err();
            assert!(matches!(error.code, ErrorCode::Invalid));
            assert!(error.message.contains("没有可导入的文字"));
        }
    }
}
