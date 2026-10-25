//! Table preview and transactional import, with decisions tied to the values the user saw.

use crate::export::Profile;
use crate::intake::{self, Row};
use crate::supplement::append;
use crate::{Affiliation, MentorForm, tidy_email};
use serde::{Deserialize, Serialize};
use sheet::Sheet;
use sqlx::{SqliteConnection, SqlitePool};
use std::collections::BTreeMap;
use ts_rs::TS;

#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Deserialize, Serialize, TS)]
#[serde(rename_all = "camelCase")]
pub enum Field {
    Name,
    Email,
    University,
    School,
    Department,
    Title,
    Homepage,
    Alias,
    Research,
    Papers,
    SourceUrl,
    Tags,
    Note,
}

impl Field {
    pub fn header(self) -> &'static str {
        match self {
            Self::Name => "姓名",
            Self::Email => "邮箱",
            Self::University => "学校",
            Self::School => "学院",
            Self::Department => "系所",
            Self::Title => "职称",
            Self::Homepage => "主页",
            Self::Alias => "别名",
            Self::Research => "研究方向",
            Self::Papers => "代表论文",
            Self::SourceUrl => "来源网址",
            Self::Tags => "标签",
            Self::Note => "备注",
        }
    }
    pub fn recognize(header: &str) -> Option<Self> {
        let header: String = header
            .trim()
            .chars()
            .map(|c| match c {
                '！'..='～' => (c as u32 - 0xfee0) as u8 as char,
                _ => c,
            })
            .filter(|c| !c.is_whitespace())
            .collect();
        let header = header
            .trim_end_matches(|c: char| c.is_ascii_digit())
            .to_lowercase();
        Some(match header.as_str() {
            "姓名" | "导师" | "导师姓名" | "name" => Self::Name,
            "邮箱" | "邮件" | "电子邮箱" | "email" | "e-mail" => Self::Email,
            "学校" | "大学" | "university" => Self::University,
            "学院" | "school" | "college" => Self::School,
            "系所" | "系" | "研究所" | "department" => Self::Department,
            "职称" | "title" => Self::Title,
            "主页" | "个人主页" | "homepage" => Self::Homepage,
            "别名" | "英文名" | "alias" => Self::Alias,
            "研究方向" | "research" => Self::Research,
            "代表论文" | "论文" | "papers" => Self::Papers,
            "来源网址" | "来源" | "source" => Self::SourceUrl,
            "标签" | "tags" => Self::Tags,
            "备注" | "note" => Self::Note,
            _ => return None,
        })
    }
    pub fn instruction(self) -> &'static str {
        match self {
            Self::Name => "必填，一行一位导师",
            Self::Email => "第一个是发信地址；多个邮箱用换行、分号、逗号、顿号或空格隔开",
            Self::University => "单位的学校；第二个单位写在“学校 2”列，第一组为主要单位",
            Self::School => "单位的学院；第几次出现的学院属于第几个单位",
            Self::Department => "单位的系或研究所；第几次出现的系所属于第几个单位",
            Self::Title => "在这个单位的职称；第几次出现的职称属于第几个单位",
            Self::Homepage => "以 http:// 或 https:// 开头，多个主页用换行、空格或全角分号隔开",
            Self::Alias => "英文名、曾用名等，多个别名用换行、分号或顿号隔开",
            Self::Research => "研究方向文字",
            Self::Papers => "多个代表论文只用换行隔开",
            Self::SourceUrl => "发现这位导师的来源网址",
            Self::Tags => "多个标签用换行、分号或顿号隔开，没有的标签按名称新建",
            Self::Note => "写给自己的备注，分享表格前请检查",
        }
    }
}

#[derive(Debug, Default, Clone, Deserialize, TS)]
#[ts(rename = "ImportSetup")]
pub struct Setup {
    pub sheet: Option<usize>,
    pub columns: Option<Vec<Option<Field>>>,
}

#[derive(Debug, Serialize, TS)]
pub struct Column {
    pub header: String,
    pub field: Option<Field>,
}

#[derive(Debug, Clone, PartialEq, Eq, Deserialize, Serialize, TS)]
#[serde(rename_all = "camelCase")]
pub struct Difference {
    pub row: u32,
    pub id: i64,
    pub name: String,
    pub field: Field,
    pub affiliation: Option<Affiliation>,
    pub local: String,
    pub incoming: String,
}

#[derive(Debug, Serialize, TS)]
#[ts(rename = "ImportProfile")]
pub struct ProfileSummary {
    pub name: String,
    pub aliases: Vec<String>,
    pub affiliations: Vec<Affiliation>,
    pub emails: Vec<String>,
    pub research: Option<String>,
    pub homepages: Vec<String>,
}

impl From<&Profile> for ProfileSummary {
    fn from(profile: &Profile) -> Self {
        Self {
            name: profile.fields.name.clone(),
            aliases: profile.fields.aliases.clone(),
            affiliations: profile.fields.affiliations.clone(),
            emails: profile.fields.emails.clone(),
            research: profile.fields.research.clone(),
            homepages: profile.fields.homepages.clone(),
        }
    }
}

#[derive(Debug, Serialize, TS)]
pub struct ImportCandidate {
    pub id: i64,
    pub profile: ProfileSummary,
}

#[derive(Debug, Serialize, TS)]
pub struct MaybeSame {
    pub row: u32,
    pub rows: Vec<u32>,
    pub incoming: ProfileSummary,
    pub candidates: Vec<ImportCandidate>,
    pub selected: Option<i64>,
}

#[derive(Debug, Deserialize, TS)]
pub struct GroupChoice {
    pub row: u32,
    pub id: Option<i64>,
}
#[derive(Debug, Serialize, TS)]
pub struct Trashed {
    pub rows: Vec<u32>,
    pub id: i64,
    pub name: String,
}
#[derive(Debug, Serialize, TS)]
pub struct RowError {
    pub row: u32,
    pub reason: String,
}

#[derive(Debug, Default, Serialize, TS)]
#[serde(rename_all = "camelCase")]
pub struct Preview {
    pub sheets: Vec<String>,
    pub sheet: usize,
    pub columns: Vec<Column>,
    pub issue: Option<String>,
    pub created: u32,
    pub supplemented: u32,
    pub updated: u32,
    pub unchanged: u32,
    pub differences: Vec<Difference>,
    pub maybe_same: Vec<MaybeSame>,
    pub trashed: Vec<Trashed>,
    pub errors: Vec<RowError>,
}
#[derive(Debug, Default, Deserialize, TS)]
pub struct Decisions {
    pub take: Vec<Difference>,
    pub matches: Vec<GroupChoice>,
    pub restore: Vec<i64>,
}
#[derive(Debug, Default, Serialize, TS)]
pub struct Imported {
    pub created: u32,
    pub updated: u32,
}

fn header(sheet: &Sheet) -> Option<usize> {
    sheet
        .rows
        .iter()
        .position(|r| r.iter().any(|c| !c.trim().is_empty()))
}

fn read_rows(sheets: &[Sheet], setup: &Setup, preview: &mut Preview) -> Vec<Row> {
    preview.sheets = sheets.iter().map(|s| s.name.clone()).collect();
    preview.sheet = setup.sheet.unwrap_or_else(|| {
        sheets
            .iter()
            .position(|s| {
                header(s).is_some_and(|h| {
                    s.rows[h]
                        .iter()
                        .any(|c| Field::recognize(c) == Some(Field::Name))
                })
            })
            .unwrap_or(0)
    });
    let Some(sheet) = sheets.get(preview.sheet) else {
        preview.issue = Some("找不到选中的工作表，请重新选择文件或工作表".into());
        return Vec::new();
    };
    let Some(h) = header(sheet) else {
        preview.issue = Some("这张表没有内容，请换一张工作表或填写后重新导入".into());
        return Vec::new();
    };
    let width = sheet.rows.iter().map(Vec::len).max().unwrap_or(0);
    preview.columns = (0..width)
        .map(|c| Column {
            header: sheet.rows[h].get(c).cloned().unwrap_or_default(),
            field: match &setup.columns {
                Some(columns) => columns.get(c).copied().flatten(),
                None => sheet.rows[h].get(c).and_then(|s| Field::recognize(s)),
            },
        })
        .collect();
    for field in [Field::Name, Field::Research, Field::SourceUrl, Field::Note] {
        if preview
            .columns
            .iter()
            .filter(|c| c.field == Some(field))
            .count()
            > 1
        {
            preview.issue = Some(format!("“{}”只能对应一列，请修改列的对应", field.header()));
            return Vec::new();
        }
    }
    if !preview.columns.iter().any(|c| c.field == Some(Field::Name)) {
        preview.issue = Some("尚未找到姓名列，请将一列对应到“姓名”".into());
        return Vec::new();
    }
    let mut rows = Vec::new();
    for (r, cells) in sheet.rows.iter().enumerate().skip(h + 1) {
        if cells.iter().all(|c| c.trim().is_empty()) {
            continue;
        }
        let number = (r + 1) as u32;
        match read_row(cells, &preview.columns) {
            Ok(profile) => rows.push(Row {
                number,
                target: None,
                profile,
            }),
            Err(reason) => preview.errors.push(RowError {
                row: number,
                reason,
            }),
        }
    }
    rows
}

fn read_row(cells: &[String], columns: &[Column]) -> Result<Profile, String> {
    let mut values = BTreeMap::<Field, Vec<String>>::new();
    for (c, column) in columns.iter().enumerate() {
        if let Some(field) = column.field {
            let value = cells.get(c).map(String::as_str).unwrap_or_default().trim();
            values
                .entry(field)
                .or_default()
                .push(if field == Field::Email {
                    tidy_email(value)
                } else {
                    value.to_owned()
                });
        }
    }
    let single = |f| {
        values
            .get(&f)
            .and_then(|v| v.first())
            .filter(|s| !s.is_empty())
            .cloned()
    };
    let many = |f| {
        values
            .get(&f)
            .into_iter()
            .flatten()
            .flat_map(|v| {
                v.split(|c: char| {
                    matches!(c, '\n' | '\r')
                        || match f {
                            Field::Email => {
                                matches!(c, ';' | '；' | ',' | '，' | '、') || c.is_whitespace()
                            }
                            Field::Homepage => c == '；' || c.is_whitespace(),
                            Field::Alias | Field::Tags => matches!(c, ';' | '；' | '、'),
                            _ => false,
                        }
                })
            })
            .map(|s| s.trim().to_owned())
            .filter(|s| !s.is_empty())
            .collect::<Vec<_>>()
    };
    let groups = [
        Field::University,
        Field::School,
        Field::Department,
        Field::Title,
    ]
    .iter()
    .filter_map(|f| values.get(f).map(Vec::len))
    .max()
    .unwrap_or(0);
    let at = |f: Field, n: usize| {
        values
            .get(&f)
            .and_then(|v| v.get(n))
            .filter(|s| !s.is_empty())
            .cloned()
    };
    let affiliations = (0..groups)
        .map(|n| Affiliation {
            university: at(Field::University, n),
            school: at(Field::School, n),
            department: at(Field::Department, n),
            title: at(Field::Title, n),
        })
        .collect();
    let fields = MentorForm {
        name: single(Field::Name).unwrap_or_default(),
        aliases: many(Field::Alias),
        emails: many(Field::Email),
        affiliations,
        homepages: many(Field::Homepage),
        research: single(Field::Research).unwrap_or_default(),
    }
    .check()?;
    let mut papers = Vec::new();
    append(&mut papers, &many(Field::Papers));
    let mut tags = Vec::new();
    append(&mut tags, &many(Field::Tags));
    Ok(Profile {
        id: 0,
        fields,
        source_url: single(Field::SourceUrl),
        papers,
        note: single(Field::Note),
        tags,
        trashed: false,
    })
}

pub async fn preview(
    db: &SqlitePool,
    sheets: &[Sheet],
    setup: &Setup,
) -> Result<Preview, sqlx::Error> {
    preview_decisions(db, sheets, setup, &Decisions::default()).await
}

pub async fn preview_decisions(
    db: &SqlitePool,
    sheets: &[Sheet],
    setup: &Setup,
    decisions: &Decisions,
) -> Result<Preview, sqlx::Error> {
    let mut tx = db.begin().await?;
    Ok(calculate(&mut tx, sheets, setup, decisions).await?.0)
}

pub async fn import(
    db: &SqlitePool,
    sheets: &[Sheet],
    setup: &Setup,
    decisions: &Decisions,
    now: i64,
) -> Result<Imported, sqlx::Error> {
    let mut tx = db.begin_with("BEGIN IMMEDIATE").await?;
    let (_, changes) = calculate(&mut tx, sheets, setup, decisions).await?;
    let (result, _) = intake::apply(&mut tx, changes, now).await?;
    tx.commit().await?;
    Ok(result)
}

async fn calculate(
    conn: &mut SqliteConnection,
    sheets: &[Sheet],
    setup: &Setup,
    decisions: &Decisions,
) -> Result<(Preview, Vec<intake::Change>), sqlx::Error> {
    let mut table = Preview::default();
    let rows = read_rows(sheets, setup, &mut table);
    let (mut preview, changes) = intake::calculate(conn, rows, decisions).await?;
    preview.sheets = table.sheets;
    preview.sheet = table.sheet;
    preview.columns = table.columns;
    preview.issue = table.issue;
    preview.errors.extend(table.errors);
    preview.errors.sort_by_key(|e| e.row);
    Ok((preview, changes))
}
