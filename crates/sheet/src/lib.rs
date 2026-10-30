//! Spreadsheet bytes and cells, independent of the records they represent.

use calamine::{Data, Dimensions, ExcelDateTime, Reader, SheetType, Xls, Xlsx};
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
        Some("xlsx") => workbook(
            Xlsx::new(Cursor::new(data)).map_err(damaged)?,
            |book, name| {
                // Only the cells that are there: one stray space far down would otherwise
                // spread the sheet into millions of empty cells.
                let merged = book.merge_cells_by_sheet_name(name)?;
                let mut cells = Vec::new();
                let mut reader = book.worksheet_cells_reader(name)?;
                while let Some(cell) = reader.next_cell()? {
                    if let Some(text) = text(cell.get_value().clone().into()) {
                        cells.push((cell.get_position(), text));
                    }
                }
                Ok((cells, merged))
            },
        ),
        // XLS has no cell reader; the format holds at most 65536 rows and 256 columns.
        Some("xls") => workbook(
            Xls::new(Cursor::new(data)).map_err(damaged)?,
            |book, name| {
                let range = book.worksheet_range(name)?;
                let (top, left) = range.start().unwrap_or_default();
                let cells = range
                    .used_cells()
                    .filter_map(|(row, column, value)| {
                        text(value.clone())
                            .map(|text| ((top + row as u32, left + column as u32), text))
                    })
                    .collect();
                Ok((cells, book.merge_cells_by_sheet_name(name)?))
            },
        ),
        _ => Err(ReadError::Format),
    }
}

fn damaged(error: impl std::fmt::Display) -> ReadError {
    ReadError::Damaged(error.to_string())
}

/// A sheet's cells that are not empty, by row and column, and its merged regions.
type Cells = (Vec<((u32, u32), String)>, Vec<Dimensions>);

fn workbook<R: Reader<Cursor<T>>, T: AsRef<[u8]>>(
    mut reader: R,
    sheet: impl Fn(&mut R, &str) -> Result<Cells, R::Error>,
) -> Result<Vec<Sheet>, ReadError>
where
    R::Error: std::fmt::Display,
{
    let names: Vec<_> = reader
        .sheets_metadata()
        .iter()
        .filter(|s| s.typ == SheetType::WorkSheet)
        .map(|s| s.name.clone())
        .collect();
    names
        .into_iter()
        .map(|name| {
            let (cells, merged) = sheet(&mut reader, &name).map_err(damaged)?;
            let mut rows: Vec<Vec<String>> = Vec::new();
            for ((row, column), text) in cells {
                let (row, column) = (row as usize, column as usize);
                if rows.len() <= row {
                    rows.resize(row + 1, Vec::new());
                }
                let cells = &mut rows[row];
                if cells.len() <= column {
                    cells.resize(column + 1, String::new());
                }
                cells[column] = text;
            }
            // A merged cell shows its value in every cell it covers, such as one school for
            // several rows of mentors.
            let width = rows.iter().map(Vec::len).max().unwrap_or(0);
            for region in merged {
                let (top, left) = (region.start.0 as usize, region.start.1 as usize);
                // A blank one clears the values it hides, which some programs keep.
                let value = rows
                    .get(top)
                    .and_then(|row| row.get(left))
                    .cloned()
                    .unwrap_or_default();
                let end = (region.end.1 as usize + 1).min(width);
                for row in rows.iter_mut().take(region.end.0 as usize + 1).skip(top) {
                    if !value.is_empty() && row.len() < end {
                        row.resize(end, String::new());
                    }
                    for cell in row.iter_mut().take(end).skip(left) {
                        cell.clone_from(&value);
                    }
                }
            }
            Ok(Sheet { name, rows })
        })
        .collect()
}

fn text(value: Data) -> Option<String> {
    let text = match value {
        Data::Float(n) if n.fract() == 0.0 => format!("{n:.0}"),
        Data::DateTime(d) if d.is_datetime() => date(&d),
        _ => value.to_string(),
    };
    let text = text.trim();
    (!text.is_empty()).then(|| text.to_owned())
}

/// Excel stores dates as day numbers; they read as `2026-10-31`, with the time when there is one.
fn date(value: &ExcelDateTime) -> String {
    let (year, month, day, hour, minute, second, _) = value.to_ymd_hms_milli();
    let time = match second {
        0 => format!("{hour:02}:{minute:02}"),
        _ => format!("{hour:02}:{minute:02}:{second:02}"),
    };
    if value.as_f64() < 1.0 {
        time
    } else if (hour, minute, second) == (0, 0, 0) {
        format!("{year:04}-{month:02}-{day:02}")
    } else {
        format!("{year:04}-{month:02}-{day:02} {time}")
    }
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
    #[test]
    fn a_stray_space_far_down_does_not_spread_the_sheet() {
        let mut book = Workbook::new();
        let page = book.add_worksheet();
        page.write_string(0, 0, "姓名").unwrap();
        page.write_string(0, 19, "备注").unwrap();
        page.write_string(1, 0, "张三").unwrap();
        page.write_string(499_999, 0, " ").unwrap();
        let rows = read("a.xlsx", &book.save_to_buffer().unwrap())
            .unwrap()
            .remove(0)
            .rows;
        assert_eq!(rows.len(), 2);
        assert_eq!(rows[1], ["张三"]);
    }
    #[test]
    fn merged_cells_fill_what_they_cover_and_dates_read_as_shown() {
        let mut book = Workbook::new();
        let page = book.add_worksheet();
        for (row, name) in [(0, "姓名"), (1, "王明"), (2, "李明"), (3, "张华")] {
            page.write_string(row, 0, name).unwrap();
        }
        page.write_string(0, 1, "学校").unwrap();
        page.merge_range(1, 1, 2, 1, "甲大学", &Format::new())
            .unwrap();
        page.write_string(3, 1, "乙大学").unwrap();
        page.merge_range(1, 3, 2, 3, "", &Format::new()).unwrap();
        page.write_string(2, 3, "被合并盖住的旧学校").unwrap();
        for (row, value, format) in [
            (1, 46326.0, "yyyy-mm-dd"),
            (2, 46326.5, "yyyy-mm-dd hh:mm"),
            (3, 0.5, "hh:mm"),
        ] {
            page.write_number_with_format(row, 2, value, &Format::new().set_num_format(format))
                .unwrap();
        }
        let rows = read("a.xlsx", &book.save_to_buffer().unwrap())
            .unwrap()
            .remove(0)
            .rows;
        assert_eq!(
            rows[1..],
            [
                vec!["王明", "甲大学", "2026-10-31"],
                vec!["李明", "甲大学", "2026-10-31 12:00", ""],
                vec!["张华", "乙大学", "12:00"],
            ]
        );
    }
}
