//! Browsing, intake and revision-based synchronization of official mentor records.

pub mod add;
pub mod sync;
pub mod updates;

use cloud_api::{Empty, library as wire, path};
use mentor::{Affiliation, MentorForm, export::Profile, intake::comparable};
use reqwest::Method;
use serde::Serialize;
use sqlx::{SqlitePool, types::Json};
use ts_rs::TS;

#[derive(Debug, Serialize, TS)]
#[serde(tag = "kind", rename_all = "camelCase")]
#[ts(rename = "LibraryLocal")]
pub enum Local {
    Added,
    Trashed,
    MaybeExisting { id: i64, name: String },
}

#[derive(Debug, Serialize, TS)]
#[ts(rename = "LibraryMentor")]
pub struct Mentor {
    #[serde(flatten)]
    pub summary: wire::Summary,
    pub local: Option<Local>,
}

#[derive(Debug, Serialize, TS)]
#[ts(rename = "LibraryMentors")]
pub struct Mentors {
    pub total: u32,
    pub mentors: Vec<Mentor>,
}

#[derive(Debug, Serialize, TS)]
#[ts(rename = "LibraryMentorPreview")]
pub struct Preview {
    #[serde(flatten)]
    pub preview: wire::Preview,
    pub local: Option<Local>,
}

pub async fn orgs(client: &account::Client, db: &SqlitePool) -> Result<wire::Orgs, Error> {
    Ok(client
        .authenticated(db, Method::GET, path::LIBRARY_ORGS, None::<&Empty>)
        .await?)
}

pub async fn list(
    client: &account::Client,
    db: &SqlitePool,
    input: &wire::ListRequest,
) -> Result<Mentors, Error> {
    if input.page == 0
        || input
            .q
            .as_ref()
            .is_some_and(|q| !(1..=50).contains(&q.trim().chars().count()))
    {
        return Err(Error::Invalid(
            "请填写 1–50 个字的关键词，页码从 1 开始。".into(),
        ));
    }
    let path = {
        let mut query = url::form_urlencoded::Serializer::new(String::new());
        query.append_pair("page", &input.page.to_string());
        if let Some(org) = &input.org {
            query.append_pair("org", org);
        }
        if let Some(q) = &input.q {
            query.append_pair("q", q.trim());
        }
        format!("{}?{}", path::LIBRARY_MENTORS, query.finish())
    };
    let result: wire::List = client
        .authenticated(db, Method::GET, &path, None::<&Empty>)
        .await?;
    let marks = local(db, &result.mentors).await?;
    Ok(Mentors {
        total: result.total,
        mentors: result
            .mentors
            .into_iter()
            .zip(marks)
            .map(|(summary, local)| Mentor { summary, local })
            .collect(),
    })
}

pub async fn preview(
    client: &account::Client,
    db: &SqlitePool,
    id: &str,
) -> Result<Preview, Error> {
    let id: String = url::form_urlencoded::byte_serialize(id.as_bytes()).collect();
    let path = format!("{}/{id}", path::LIBRARY_MENTORS);
    let preview: wire::Preview = client
        .authenticated(db, Method::GET, &path, None::<&Empty>)
        .await?;
    let local = local(db, std::slice::from_ref(&preview.summary))
        .await?
        .pop()
        .flatten();
    Ok(Preview { preview, local })
}

pub async fn quota(client: &account::Client, db: &SqlitePool) -> Result<wire::Quota, Error> {
    Ok(client
        .authenticated(db, Method::GET, path::LIBRARY_QUOTA, None::<&Empty>)
        .await?)
}

pub async fn local(
    db: &SqlitePool,
    summaries: &[wire::Summary],
) -> Result<Vec<Option<Local>>, sqlx::Error> {
    let incoming = Json(
        summaries
            .iter()
            .map(|s| {
                let names: Vec<_> = std::iter::once(&s.name)
                    .chain(&s.aliases)
                    .map(|n| comparable(n))
                    .collect();
                (&s.id, names, s.unit.university.as_deref().map(comparable))
            })
            .collect::<Vec<_>>(),
    );
    // Keep the small page before name lookups; SQLite otherwise starts with all live mentors.
    let rows = sqlx::query!(
        r#"WITH page AS (
            SELECT CAST(key AS INTEGER) AS position, json_extract(value, '$[0]') AS library_id,
                value FROM json_each(?)
        )
        SELECT page.position AS "position!: i64", m.id AS "id!", m.name AS "name!",
            m.trashed_at IS NOT NULL AS "trashed!: bool", 1 AS "linked!: bool"
        FROM page JOIN mentor_library l ON l.library_id = page.library_id
        JOIN mentor m ON m.id = l.mentor_id
        UNION ALL
        SELECT DISTINCT page.position AS "position!: i64", m.id AS "id!", m.name AS "name!",
            0 AS "trashed!: bool", 0 AS "linked!: bool"
        FROM page CROSS JOIN mentor_name n ON n.name IN (SELECT value FROM json_each(page.value, '$[1]'))
        CROSS JOIN mentor m ON m.id = n.mentor_id
        WHERE m.trashed_at IS NULL
            AND NOT EXISTS (SELECT 1 FROM mentor_library WHERE library_id = page.library_id)
            AND EXISTS (SELECT 1 FROM mentor_affiliation WHERE mentor_id = m.id
                AND university_match = json_extract(page.value, '$[2]'))
        ORDER BY 1, 2"#,
        incoming
    )
    .fetch_all(db)
    .await?;
    let mut marks: Vec<Option<Local>> = std::iter::repeat_with(|| None)
        .take(summaries.len())
        .collect();
    for row in rows {
        marks[row.position as usize].get_or_insert({
            if row.linked {
                if row.trashed {
                    Local::Trashed
                } else {
                    Local::Added
                }
            } else {
                Local::MaybeExisting {
                    id: row.id,
                    name: row.name,
                }
            }
        });
    }
    Ok(marks)
}

pub(crate) fn affiliation(unit: &wire::Unit) -> Affiliation {
    Affiliation {
        university: unit.university.clone(),
        school: unit.school.clone(),
        department: unit.department.clone(),
        title: unit.title.clone(),
    }
}

pub(crate) fn profile(record: &wire::Record) -> Result<Profile, String> {
    let p = &record.preview;
    let fields = MentorForm {
        name: p.summary.name.clone(),
        aliases: p.summary.aliases.clone(),
        emails: record.emails.clone(),
        affiliations: p.units.iter().map(affiliation).collect(),
        homepages: p.homepages.clone(),
        research: p.summary.research.clone().unwrap_or_default(),
    }
    .check()?;
    Ok(Profile {
        id: 0,
        fields,
        source_url: p.source_url.clone(),
        papers: p.papers.clone(),
        note: None,
        tags: vec![],
        trashed: false,
    })
}

#[derive(Debug)]
pub enum Error {
    Account(account::Error),
    Database(sqlx::Error),
    Invalid(String),
}
impl From<account::Error> for Error {
    fn from(value: account::Error) -> Self {
        Self::Account(value)
    }
}
impl From<sqlx::Error> for Error {
    fn from(value: sqlx::Error) -> Self {
        Self::Database(value)
    }
}
impl std::fmt::Display for Error {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(self.category())
    }
}
impl std::error::Error for Error {}
impl Error {
    pub fn category(&self) -> &'static str {
        match self {
            Self::Account(account::Error::SignedOut) => "signed_out",
            Self::Account(account::Error::Network(_)) => "network",
            Self::Account(_) => "account",
            Self::Database(_) => "database",
            Self::Invalid(_) => "invalid",
        }
    }
    pub fn message(&self) -> String {
        match self {
            Self::Account(account::Error::Server(cloud_api::ApiError {
                code: cloud_api::ErrorCode::NotFound,
                ..
            })) => "这位导师已不在导师库，请刷新列表。".into(),
            Self::Account(error) => error.message(),
            Self::Invalid(message) => message.clone(),
            _ => "导师库处理未能完成，请重试；仍然失败时导出日志联系开发者。".into(),
        }
    }
}
