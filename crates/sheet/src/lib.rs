//! Spreadsheet bytes and cells, independent of the records they represent.

use calamine::{Data, Reader, Xls, Xlsx};
use rust_xlsxwriter::{Format, Workbook, XlsxError};
use std::io::Cursor;

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Sheet {
    pub name: String,
    pub rows: Vec<Vec<String>>,
}

#[derive(Debug)]
pub enum ReadError {
    Format,
    Damaged(String),
}

impl std::fmt::Display for ReadError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::Format => write!(f, "不支持这种表格格式，请选择 CSV、XLSX 或 XLS 文件"),
            Self::Damaged(_) => write!(
                f,
                "表格无法读取，可能已损坏或受密码保护。请用表格软件打开后重新保存，再导入"
            ),
        }
    }
}
impl std::error::Error for ReadError {}

pub fn read(file_name: &str, data: &[u8]) -> Result<Vec<Sheet>, ReadError> {
    match file_name
        .rsplit_once('.')
        .map(|(_, e)| e.to_ascii_lowercase())
        .as_deref()
    {
        Some("csv") => {
            let text = match std::str::from_utf8(data) {
                Ok(s) => std::borrow::Cow::Borrowed(s.trim_start_matches('\u{feff}')),
                Err(_) => encoding_rs::GB18030.decode(data).0,
            };
            let mut reader = csv::ReaderBuilder::new()
                .has_headers(false)
                .flexible(true)
                .from_reader(text.as_bytes());
            let mut rows = Vec::new();
            let mut record = csv::StringRecord::new();
            while reader
                .read_record(&mut record)
                .map_err(|e| ReadError::Damaged(e.to_string()))?
            {
                let start = record.position().map(|p| p.byte() as usize).unwrap_or(0);
                let mut prefix = &text[start..];
                if start > 0 && text.as_bytes()[start - 1] == b'\r' && prefix.starts_with('\n') {
                    prefix = &prefix[1..];
                }
                while let Some(rest) = prefix
                    .strip_prefix("\r\n")
                    .or_else(|| prefix.strip_prefix(['\r', '\n']))
                {
                    rows.push(Vec::new());
                    prefix = rest;
                }
                rows.push(
                    record
                        .iter()
                        .map(|s| {
                            let s = s.trim();
                            s.strip_prefix('\'')
                                .filter(|rest| rest.starts_with(['=', '+', '-', '@', '\'']))
                                .unwrap_or(s)
                                .to_owned()
                        })
                        .collect(),
                );
            }
            Ok(vec![Sheet {
                name: "CSV".into(),
                rows,
            }])
        }
        Some("xlsx") => {
            workbook(Xlsx::new(Cursor::new(data)).map_err(|e| ReadError::Damaged(e.to_string()))?)
        }
        Some("xls") => {
            workbook(Xls::new(Cursor::new(data)).map_err(|e| ReadError::Damaged(e.to_string()))?)
        }
        _ => Err(ReadError::Format),
    }
}

fn workbook<R: Reader<Cursor<T>>, T: AsRef<[u8]>>(mut reader: R) -> Result<Vec<Sheet>, ReadError>
where
    R::Error: std::fmt::Display,
{
    reader
        .sheet_names()
        .to_vec()
        .into_iter()
        .map(|name| {
            let range = reader
                .worksheet_range(&name)
                .map_err(|e| ReadError::Damaged(e.to_string()))?;
            let (top, left) = range.start().unwrap_or((0, 0));
            let mut rows = vec![Vec::new(); top as usize];
            for row in range.rows() {
                let mut cells = vec![String::new(); left as usize];
                cells.extend(
                    row.iter()
                        .map(|v| match v {
                            Data::Float(n) if n.fract() == 0.0 => format!("{n:.0}"),
                            _ => v.to_string(),
                        })
                        .map(|s| s.trim().to_owned()),
                );
                rows.push(cells);
            }
            Ok(Sheet { name, rows })
        })
        .collect()
}

pub fn write_xlsx(sheets: &[Sheet]) -> Result<Vec<u8>, XlsxError> {
    let mut book = Workbook::new();
    let text = Format::new().set_text_wrap();
    let header = Format::new().set_bold().set_text_wrap();
    for sheet in sheets {
        let page = book.add_worksheet();
        page.set_name(&sheet.name)?;
        page.set_freeze_panes(1, 0)?;
        let mut widths = Vec::<usize>::new();
        for (r, row) in sheet.rows.iter().enumerate() {
            for (c, value) in row.iter().enumerate() {
                page.write_string_with_format(
                    r as u32,
                    c as u16,
                    value,
                    if r == 0 { &header } else { &text },
                )?;
                let width = value
                    .lines()
                    .map(|s| s.chars().map(|c| if c.is_ascii() { 1 } else { 2 }).sum())
                    .max()
                    .unwrap_or(0);
                if widths.len() <= c {
                    widths.resize(c + 1, 0);
                }
                widths[c] = widths[c].max(width);
            }
        }
        for (c, width) in widths.into_iter().enumerate() {
            page.set_column_width(c as u16, (width + 2).clamp(12, 48) as f64)?;
        }
    }
    book.save_to_buffer()
}

pub fn write_csv(rows: &[Vec<String>]) -> Vec<u8> {
    let mut writer = csv::WriterBuilder::new()
        .flexible(true)
        .from_writer(vec![0xef, 0xbb, 0xbf]);
    for row in rows {
        let cells = row.iter().map(|s| {
            if s.starts_with(['=', '+', '-', '@', '\'']) {
                format!("'{s}")
            } else {
                s.clone()
            }
        });
        if let Err(e) = writer.write_record(cells) {
            unreachable!("writing to Vec: {e}");
        }
    }
    writer
        .into_inner()
        .unwrap_or_else(|e| unreachable!("writing to Vec: {e}"))
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn csv_encodings_and_quoted_cells() {
        let text = "姓名,备注\r\n王明,\"一行\n第二行\"\r\n李明,\"他说\"\"你好\"\"\"";
        let expected = read("a.csv", text.as_bytes()).unwrap();
        assert_eq!(expected[0].rows[1][1], "一行\n第二行");
        assert_eq!(expected[0].rows[2][1], "他说\"你好\"");
        assert_eq!(
            read("a.CSV", format!("\u{feff}{text}").as_bytes()).unwrap(),
            expected
        );
        assert_eq!(
            read("a.csv", &encoding_rs::GB18030.encode(text).0).unwrap(),
            expected
        );
    }
    #[test]
    fn csv_blank_rows_keep_spreadsheet_row_numbers() {
        for newline in ["\n", "\r\n"] {
            let text = [
                "",
                "姓名,备注",
                "",
                "王明,\"第一行\n第二行\"",
                "",
                "李明,备注",
            ]
            .join(newline);
            let rows = read("a.csv", text.as_bytes()).unwrap().remove(0).rows;
            assert_eq!(rows.len(), 6);
            assert_eq!(rows[3][0], "王明");
            assert_eq!(rows[5][0], "李明");
        }
    }
    #[test]
    fn xlsx_round_trip_and_csv_formula_prefixes() {
        let sheets = vec![Sheet {
            name: "导师".into(),
            rows: vec![
                vec!["姓名".into(), "备注".into()],
                vec!["王明".into(), "=1+2\n含引号\"".into()],
            ],
        }];
        assert_eq!(
            read("a.xlsx", &write_xlsx(&sheets).unwrap()).unwrap(),
            sheets
        );
        let rows = vec![vec![
            "=1".into(),
            "+1".into(),
            "-1".into(),
            "@a".into(),
            "普通".into(),
            "'=备注".into(),
            "'原文".into(),
            "''原文".into(),
        ]];
        let csv = write_csv(&rows);
        let text = std::str::from_utf8(&csv).unwrap();
        assert!(text.starts_with("\u{feff}'=1,'+1,'-1,'@a,"));
        assert!(text.contains("''=备注"));
        assert_eq!(read("a.csv", &csv).unwrap()[0].rows[0], rows[0]);
    }
}
