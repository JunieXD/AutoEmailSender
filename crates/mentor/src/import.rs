//! Table preview and transactional import, with decisions tied to the values the user saw.

use crate::export::{Profile, profiles};
use crate::supplement::{add_missing, append, comparable, same};
use crate::{Affiliation, MentorForm, SaveError, create, trash, update};
use serde::{Deserialize, Serialize};
use sheet::Sheet;
use sqlx::{SqliteConnection, SqlitePool, types::Json};
use std::collections::{BTreeMap, BTreeSet, HashMap};
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
        let header: String = tidy(header)
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

fn tidy(value: &str) -> String {
    let value = value.trim();
    value
        .chars()
        .map(|c| match c {
            'Ａ'..='Ｚ' | 'ａ'..='ｚ' | '０'..='９' | '＠' | '．' => {
                (c as u32 - 0xfee0) as u8 as char
            }
            _ => c,
        })
        .collect()
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
    pub affiliations: Vec<Affiliation>,
    pub emails: Vec<String>,
    pub research: Option<String>,
    pub homepages: Vec<String>,
}

impl From<&Profile> for ProfileSummary {
    fn from(profile: &Profile) -> Self {
        Self {
            name: profile.fields.name.clone(),
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

struct Row {
    number: u32,
    profile: Profile,
}
struct Change {
    before: Option<Profile>,
    after: Profile,
    restore: bool,
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
            Ok(profile) => rows.push(Row { number, profile }),
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
            values
                .entry(field)
                .or_default()
                .push(tidy(cells.get(c).map(String::as_str).unwrap_or_default()));
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
            .map(tidy)
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

struct Candidates {
    profiles: BTreeMap<i64, Profile>,
    by_name: HashMap<String, Vec<i64>>,
}

async fn candidates(conn: &mut SqliteConnection, rows: &[Row]) -> Result<Candidates, sqlx::Error> {
    let emails = Json(
        rows.iter()
            .flat_map(|r| &r.profile.fields.emails)
            .collect::<Vec<_>>(),
    );
    let owners = sqlx::query!("SELECT address, mentor_id FROM mentor_email WHERE address IN (SELECT value FROM json_each(?))", emails).fetch_all(&mut *conn).await?;
    let addresses: BTreeSet<_> = owners.iter().map(|e| e.address.to_lowercase()).collect();
    let mut ids: BTreeSet<_> = owners.into_iter().map(|e| e.mentor_id).collect();
    let names: BTreeSet<_> = rows
        .iter()
        .filter(|r| {
            !r.profile
                .fields
                .emails
                .iter()
                .any(|e| addresses.contains(&e.to_lowercase()))
        })
        .flat_map(|r| std::iter::once(&r.profile.fields.name).chain(&r.profile.fields.aliases))
        .map(|name| comparable(name))
        .collect();
    let mut by_name = HashMap::<String, Vec<i64>>::new();
    if !names.is_empty() {
        // Reading just names avoids large FTS OR queries and loading unrelated profile details.
        for record in sqlx::query!(
            "SELECT id, name FROM mentor UNION ALL SELECT mentor_id, name FROM mentor_alias"
        )
        .fetch_all(&mut *conn)
        .await?
        {
            let name = comparable(&record.name);
            if names.contains(&name) {
                by_name.entry(name).or_default().push(record.id);
                ids.insert(record.id);
            }
        }
    }
    Ok(Candidates {
        profiles: profiles(conn, &ids.into_iter().collect::<Vec<_>>()).await?,
        by_name,
    })
}

fn root(parents: &[usize], mut n: usize) -> usize {
    while parents[n] != n {
        n = parents[n];
    }
    n
}

fn combine_rows(rows: &[Row]) -> Profile {
    let mut incoming = rows[0].profile.clone();
    for row in rows.iter().skip(1) {
        let mut emails = incoming.fields.emails.clone();
        for email in &row.profile.fields.emails {
            if !emails.iter().any(|e| e.eq_ignore_ascii_case(email)) {
                emails.push(email.clone());
            }
        }
        add_missing(
            &mut incoming.fields,
            &mut incoming.papers,
            &row.profile.fields,
            &row.profile.papers,
        );
        incoming.fields.emails = emails;
        if !same(&incoming.fields.name, &row.profile.fields.name) {
            append(
                &mut incoming.fields.aliases,
                std::slice::from_ref(&row.profile.fields.name),
            );
        }
        append(&mut incoming.tags, &row.profile.tags);
        if incoming.fields.research.is_none() {
            incoming
                .fields
                .research
                .clone_from(&row.profile.fields.research);
        }
        if incoming.source_url.is_none() {
            incoming.source_url.clone_from(&row.profile.source_url);
        }
        if incoming.note.is_none() {
            incoming.note.clone_from(&row.profile.note);
        }
    }
    incoming
}

fn supplement(original: &Profile, incoming: &Profile, row: u32) -> (Profile, Vec<Difference>) {
    let mut after = original.clone();
    let titles = add_missing(
        &mut after.fields,
        &mut after.papers,
        &incoming.fields,
        &incoming.papers,
    );
    append(&mut after.tags, &incoming.tags);
    let mut differences = Vec::new();
    for (field, a, b) in [
        (
            Field::Name,
            Some(&original.fields.name),
            Some(&incoming.fields.name),
        ),
        (
            Field::Research,
            original.fields.research.as_ref(),
            incoming.fields.research.as_ref(),
        ),
        (
            Field::SourceUrl,
            original.source_url.as_ref(),
            incoming.source_url.as_ref(),
        ),
        (Field::Note, original.note.as_ref(), incoming.note.as_ref()),
    ] {
        if let (Some(a), Some(b)) = (a, b)
            && !same(a, b)
        {
            differences.push(Difference {
                row,
                id: original.id,
                name: original.fields.name.clone(),
                field,
                affiliation: None,
                local: a.clone(),
                incoming: b.clone(),
            });
        }
    }
    for title in titles {
        let mut affiliation = after.fields.affiliations[title.position].clone();
        affiliation.title = None;
        differences.push(Difference {
            row,
            id: original.id,
            name: original.fields.name.clone(),
            field: Field::Title,
            affiliation: Some(affiliation),
            local: title.local,
            incoming: title.incoming,
        });
    }
    if after.fields.research.is_none() {
        after.fields.research = incoming.fields.research.clone();
    }
    if after.source_url.is_none() {
        after.source_url = incoming.source_url.clone();
    }
    if after.note.is_none() {
        after.note = incoming.note.clone();
    }
    (after, differences)
}

async fn calculate(
    conn: &mut SqliteConnection,
    sheets: &[Sheet],
    setup: &Setup,
    decisions: &Decisions,
) -> Result<(Preview, Vec<Change>), sqlx::Error> {
    let mut preview = Preview::default();
    let rows = read_rows(sheets, setup, &mut preview);
    if rows.is_empty() {
        return Ok((preview, Vec::new()));
    }
    let Candidates {
        profiles: local,
        by_name,
    } = candidates(conn, &rows).await?;
    let emails: BTreeMap<_, _> = local
        .values()
        .flat_map(|p| {
            p.fields
                .emails
                .iter()
                .map(move |e| (e.to_lowercase(), p.id))
        })
        .collect();
    let schools: BTreeMap<_, Vec<_>> = local
        .values()
        .map(|p| {
            (
                p.id,
                p.fields
                    .affiliations
                    .iter()
                    .filter_map(|a| a.university.as_deref())
                    .map(comparable)
                    .collect(),
            )
        })
        .collect();
    let mut parents: Vec<_> = (0..rows.len()).collect();
    let mut by_email = BTreeMap::new();
    for (n, row) in rows.iter().enumerate() {
        for email in &row.profile.fields.emails {
            if let Some(other) = by_email.insert(email.to_lowercase(), n) {
                let a = root(&parents, n);
                let b = root(&parents, other);
                parents[a.max(b)] = a.min(b);
            }
        }
    }
    let mut email_groups = BTreeMap::<usize, Vec<Row>>::new();
    for (n, row) in rows.into_iter().enumerate() {
        email_groups.entry(root(&parents, n)).or_default().push(row);
    }
    let mut groups = BTreeMap::<(Option<i64>, u32), Vec<Row>>::new();
    for rows in email_groups.into_values() {
        let owners: BTreeSet<_> = rows
            .iter()
            .flat_map(|r| &r.profile.fields.emails)
            .filter_map(|e| emails.get(&e.to_lowercase()).copied())
            .collect();
        if owners.len() > 1 {
            for row in rows {
                preview.errors.push(RowError {
                    row: row.number,
                    reason: "这些行的邮箱对应两位已有的导师，请先合并导师或修改表格".into(),
                });
            }
            continue;
        }
        let first = &rows[0];
        let mut target = owners.first().copied();
        if target.is_none() {
            let has_email = rows.iter().any(|r| !r.profile.fields.emails.is_empty());
            let schools = &schools;
            let possible: BTreeSet<_> = rows
                .iter()
                .flat_map(|row| {
                    let universities: Vec<_> = row
                        .profile
                        .fields
                        .affiliations
                        .iter()
                        .filter_map(|a| a.university.as_deref())
                        .map(comparable)
                        .collect();
                    std::iter::once(&row.profile.fields.name)
                        .chain(&row.profile.fields.aliases)
                        .filter_map(|name| by_name.get(&comparable(name)))
                        .flatten()
                        .map(|id| &local[id])
                        .filter(move |p| !has_email || p.fields.emails.is_empty())
                        .filter(move |p| {
                            universities.is_empty()
                                || universities.iter().any(|u| schools[&p.id].contains(u))
                        })
                })
                .map(|p| p.id)
                .collect();
            if !possible.is_empty() {
                // Choose each email group before combining groups that target the same mentor.
                let incoming = combine_rows(&rows);
                let exact: Vec<_> = possible
                    .iter()
                    .copied()
                    .filter(|id| {
                        let (after, differences) = supplement(&local[id], &incoming, first.number);
                        after == local[id] && differences.is_empty()
                    })
                    .collect();
                target = match exact.as_slice() {
                    [id] => Some(*id),
                    _ if possible.len() == 1 => possible.first().copied(),
                    _ => None,
                };
                if let Some(choice) = decisions.matches.iter().find(|choice| {
                    choice.row == first.number && choice.id.is_none_or(|id| possible.contains(&id))
                }) {
                    target = choice.id;
                }
                preview.maybe_same.push(MaybeSame {
                    row: first.number,
                    rows: rows.iter().map(|r| r.number).collect(),
                    incoming: (&incoming).into(),
                    candidates: possible
                        .iter()
                        .map(|id| ImportCandidate {
                            id: *id,
                            profile: (&local[id]).into(),
                        })
                        .collect(),
                    selected: target,
                });
            }
        }
        let key = target.map_or((None, first.number), |id| (Some(id), 0));
        groups.entry(key).or_default().extend(rows);
    }
    let mut changes = Vec::new();
    for ((target, _), mut rows) in groups {
        rows.sort_by_key(|r| r.number);
        let first = &rows[0];
        let before = target.and_then(|id| local.get(&id)).cloned();
        let restore = before.as_ref().is_some_and(|p| p.trashed);
        if let Some(p) = before.as_ref().filter(|p| p.trashed) {
            preview.trashed.push(Trashed {
                rows: rows.iter().map(|r| r.number).collect(),
                id: p.id,
                name: p.fields.name.clone(),
            });
            if !decisions.restore.contains(&p.id) {
                continue;
            }
        }
        let incoming = combine_rows(&rows);
        let Some(original) = &before else {
            preview.created += 1;
            changes.push(Change {
                before: None,
                after: incoming,
                restore: false,
            });
            continue;
        };
        let (mut after, differences) = supplement(original, &incoming, first.number);
        let supplemented = &after != original;
        for d in &differences {
            if !decisions.take.iter().any(|choice| {
                choice.id == d.id
                    && choice.field == d.field
                    && choice.affiliation == d.affiliation
                    && choice.local == d.local
                    && choice.incoming == d.incoming
            }) {
                continue;
            }
            match d.field {
                Field::Name => {
                    after.fields.aliases.retain(|a| !same(a, &d.incoming));
                    append(
                        &mut after.fields.aliases,
                        std::slice::from_ref(&after.fields.name),
                    );
                    after.fields.name.clone_from(&d.incoming);
                }
                Field::Research => after.fields.research = Some(d.incoming.clone()),
                Field::SourceUrl => after.source_url = Some(d.incoming.clone()),
                Field::Note => after.note = Some(d.incoming.clone()),
                Field::Title => {
                    if let Some(a) = after.fields.affiliations.iter_mut().find(|a| {
                        let mut a = (*a).clone();
                        a.title = None;
                        Some(&a) == d.affiliation.as_ref()
                    }) {
                        a.title = Some(d.incoming.clone());
                    }
                }
                _ => unreachable!("only scalar conflicts have decisions"),
            }
        }
        if supplemented {
            preview.supplemented += 1;
        } else if differences.is_empty() {
            preview.unchanged += 1;
        }
        preview.differences.extend(differences);
        if &after != original || restore {
            changes.push(Change {
                before,
                after,
                restore,
            });
        }
    }
    preview.updated = changes.iter().filter(|c| c.before.is_some()).count() as u32;
    preview.errors.sort_by_key(|e| e.row);
    Ok((preview, changes))
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
    let mut result = Imported::default();
    for change in changes {
        let p = change.after;
        let id = if change.before.is_some() {
            if change.restore {
                trash::restore(&mut *tx, &[p.id], now).await?;
            }
            update(&mut *tx, p.id, &p.fields, now)
                .await
                .map_err(save_error)?;
            result.updated += 1;
            p.id
        } else {
            result.created += 1;
            create(&mut *tx, &p.fields, now).await.map_err(save_error)?
        };
        sqlx::query!(
            "UPDATE mentor SET source_url = ? WHERE id = ?",
            p.source_url,
            id
        )
        .execute(&mut *tx)
        .await?;
        sqlx::query!(
            "UPDATE mentor_detail SET papers = ?, note = ? WHERE mentor_id = ?",
            Json(&p.papers),
            p.note,
            id
        )
        .execute(&mut *tx)
        .await?;
        for name in &p.tags {
            crate::tags::add_by_name(&mut tx, id, name).await?;
        }
    }
    tx.commit().await?;
    Ok(result)
}

fn save_error(error: SaveError) -> sqlx::Error {
    match error {
        SaveError::Db(e) => e,
        SaveError::EmailTaken { .. } => sqlx::Error::Protocol(error.to_string()),
    }
}
