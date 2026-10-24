//! Mentor table columns, export scopes and the profile data needed for round trips.

use crate::import::Field;
use crate::{Affiliation, ContactStatus, MentorFields, filtered_ids, keyword};
use serde::Deserialize;
use sqlx::{SqliteConnection, SqlitePool, types::Json};
use std::collections::BTreeMap;
use ts_rs::TS;

#[derive(Debug, Deserialize, TS)]
#[serde(tag = "kind", content = "value", rename_all = "camelCase")]
pub enum Scope {
    Filter(Filter),
    Ids(Vec<i64>),
}

#[derive(Debug, Deserialize, TS)]
pub struct Filter {
    #[ts(optional)]
    pub status: Option<ContactStatus>,
    #[ts(optional)]
    pub keyword: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) struct Profile {
    pub id: i64,
    pub fields: MentorFields,
    pub source_url: Option<String>,
    pub papers: Vec<String>,
    pub note: Option<String>,
    pub tags: Vec<String>,
    pub trashed: bool,
}

pub(crate) async fn profiles(
    conn: &mut SqliteConnection,
    ids: &[i64],
) -> Result<BTreeMap<i64, Profile>, sqlx::Error> {
    let ids = Json(ids);
    let mut profiles: BTreeMap<_, _> = sqlx::query!(
        r#"SELECT m.id, m.name, m.source_url, m.trashed_at IS NOT NULL AS "trashed!: bool",
            d.research, d.papers AS "papers: Json<Vec<String>>", d.note
           FROM mentor m JOIN mentor_detail d ON d.mentor_id = m.id
           WHERE m.id IN (SELECT value FROM json_each(?))"#,
        ids
    )
    .fetch_all(&mut *conn)
    .await?
    .into_iter()
    .map(|r| {
        (
            r.id,
            Profile {
                id: r.id,
                fields: MentorFields {
                    name: r.name,
                    research: r.research,
                    ..MentorFields::default()
                },
                source_url: r.source_url,
                papers: r.papers.map(|Json(p)| p).unwrap_or_default(),
                note: r.note,
                tags: Vec::new(),
                trashed: r.trashed,
            },
        )
    })
    .collect();
    for r in sqlx::query!("SELECT mentor_id, name FROM mentor_alias WHERE mentor_id IN (SELECT value FROM json_each(?)) ORDER BY position", ids).fetch_all(&mut *conn).await? {
        if let Some(p) = profiles.get_mut(&r.mentor_id) { p.fields.aliases.push(r.name); }
    }
    for r in sqlx::query!("SELECT mentor_id, address FROM mentor_email WHERE mentor_id IN (SELECT value FROM json_each(?)) ORDER BY position", ids).fetch_all(&mut *conn).await? {
        if let Some(p) = profiles.get_mut(&r.mentor_id) { p.fields.emails.push(r.address); }
    }
    for r in sqlx::query!("SELECT mentor_id, university, school, department, title FROM mentor_affiliation WHERE mentor_id IN (SELECT value FROM json_each(?)) ORDER BY position", ids).fetch_all(&mut *conn).await? {
        if let Some(p) = profiles.get_mut(&r.mentor_id) { p.fields.affiliations.push(Affiliation { university: r.university, school: r.school, department: r.department, title: r.title }); }
    }
    for r in sqlx::query!("SELECT mentor_id, url FROM mentor_homepage WHERE mentor_id IN (SELECT value FROM json_each(?)) ORDER BY position", ids).fetch_all(&mut *conn).await? {
        if let Some(p) = profiles.get_mut(&r.mentor_id) { p.fields.homepages.push(r.url); }
    }
    for r in sqlx::query!("SELECT mt.mentor_id, t.name FROM mentor_tag mt JOIN tag t ON t.id = mt.tag_id WHERE mt.mentor_id IN (SELECT value FROM json_each(?)) ORDER BY t.position", ids).fetch_all(&mut *conn).await? {
        if let Some(p) = profiles.get_mut(&r.mentor_id) { p.tags.push(r.name); }
    }
    Ok(profiles)
}

pub async fn export(
    db: &SqlitePool,
    scope: &Scope,
    note: bool,
) -> Result<Vec<Vec<String>>, sqlx::Error> {
    let mut tx = db.begin().await?;
    let ids = match scope {
        Scope::Filter(f) => filtered_ids(&mut tx, f.status, f.keyword.as_deref().and_then(keyword).as_deref(), -1, 0).await?,
        Scope::Ids(ids) => sqlx::query_scalar!("SELECT id FROM mentor WHERE trashed_at IS NULL AND id IN (SELECT value FROM json_each(?)) ORDER BY match_score DESC, id", Json(ids)).fetch_all(&mut *tx).await?,
    };
    let mut data = profiles(&mut tx, &ids).await?;
    let groups = data
        .values()
        .map(|p| p.fields.affiliations.len())
        .max()
        .unwrap_or(1)
        .max(1);
    let mut columns = vec![(Field::Name, 0), (Field::Email, 0)];
    for n in 0..groups {
        columns.extend(
            [
                Field::University,
                Field::School,
                Field::Department,
                Field::Title,
            ]
            .map(|f| (f, n)),
        );
    }
    columns.extend(
        [
            Field::Homepage,
            Field::Alias,
            Field::Research,
            Field::Papers,
            Field::SourceUrl,
            Field::Tags,
        ]
        .map(|f| (f, 0)),
    );
    if note {
        columns.push((Field::Note, 0));
    }
    let mut rows = vec![
        columns
            .iter()
            .map(|(f, n)| {
                if *n == 0 {
                    f.header().into()
                } else {
                    format!("{} {}", f.header(), n + 1)
                }
            })
            .collect(),
    ];
    for id in ids {
        if let Some(p) = data.remove(&id) {
            rows.push(
                columns
                    .iter()
                    .map(|(f, n)| match f {
                        Field::Name => p.fields.name.clone(),
                        Field::Email => p.fields.emails.join("\n"),
                        Field::University | Field::School | Field::Department | Field::Title => p
                            .fields
                            .affiliations
                            .get(*n)
                            .and_then(|a| match f {
                                Field::University => a.university.clone(),
                                Field::School => a.school.clone(),
                                Field::Department => a.department.clone(),
                                _ => a.title.clone(),
                            })
                            .unwrap_or_default(),
                        Field::Homepage => p.fields.homepages.join("\n"),
                        Field::Alias => p.fields.aliases.join("\n"),
                        Field::Research => p.fields.research.clone().unwrap_or_default(),
                        Field::Papers => p.papers.join("\n"),
                        Field::SourceUrl => p.source_url.clone().unwrap_or_default(),
                        Field::Tags => p.tags.join("\n"),
                        Field::Note => p.note.clone().unwrap_or_default(),
                    })
                    .collect(),
            );
        }
    }
    Ok(rows)
}

pub fn instructions(headers: &[String]) -> sheet::Sheet {
    let mut rows = vec![vec!["列".into(), "填写方法".into()]];
    rows.extend(headers.iter().map(|header| {
        vec![
            header.clone(),
            Field::recognize(header)
                .map(Field::instruction)
                .unwrap_or_default()
                .into(),
        ]
    }));
    sheet::Sheet {
        name: "填写说明".into(),
        rows,
    }
}
