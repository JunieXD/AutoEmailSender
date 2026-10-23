//! Mentors: the home list with its filters, keyword search and adding mentors.

use std::collections::BTreeMap;
use std::num::NonZeroU32;

use serde::{Deserialize, Serialize};
use sqlx::{Acquire, Sqlite, SqlitePool};
use ts_rs::TS;

pub const PAGE_SIZE: u32 = 50;

/// How far contact with a mentor has got, derived from mail (docs/data-model.md#联系状态).
#[derive(Debug, Clone, Copy, PartialEq, Eq, Deserialize, Serialize, TS, sqlx::Type)]
#[serde(rename_all = "snake_case")]
#[sqlx(rename_all = "snake_case")]
pub enum ContactStatus {
    None,
    Contacted,
    Replied,
    Bounced,
}

#[derive(Debug, Deserialize, TS)]
#[serde(rename_all = "camelCase")]
pub struct MentorQuery {
    /// All mentors when absent.
    #[ts(optional)]
    pub status: Option<ContactStatus>,
    #[ts(optional)]
    pub keyword: Option<String>,
    /// Starts at 1.
    pub page: NonZeroU32,
}

#[derive(Debug, Serialize, TS)]
#[serde(rename_all = "camelCase")]
pub struct MentorPage {
    pub mentors: Vec<MentorRow>,
    pub total: i64,
}

#[derive(Debug, Serialize, TS)]
#[serde(rename_all = "camelCase")]
pub struct MentorRow {
    pub id: i64,
    pub name: String,
    pub title: Option<String>,
    pub university: Option<String>,
    pub school: Option<String>,
    pub department: Option<String>,
    /// The start of the research description, enough for the two lines the list shows.
    pub research: Option<String>,
    pub match_state: MatchState,
    pub contact: Contact,
    pub tags: Vec<TagLabel>,
}

#[derive(Debug, PartialEq, Eq, Serialize, TS)]
#[serde(tag = "state", rename_all = "snake_case")]
pub enum MatchState {
    NotAnalyzed,
    Queued,
    Running,
    Done { score: i64 },
    Failed,
}

/// Times are UTC milliseconds.
#[derive(Debug, PartialEq, Eq, Serialize, TS)]
#[serde(
    tag = "status",
    rename_all = "snake_case",
    rename_all_fields = "camelCase"
)]
pub enum Contact {
    None,
    Contacted {
        count: i64,
        last_out_at: i64,
    },
    /// A mentor can write first, so there may be nothing sent yet.
    Replied {
        count: i64,
        last_out_at: Option<i64>,
        last_reply_at: i64,
        unread: bool,
    },
    Bounced {
        count: i64,
        last_out_at: i64,
    },
}

#[derive(Debug, Serialize, TS)]
pub struct TagLabel {
    pub name: String,
    pub color: String,
}

#[derive(Debug, Clone, Copy, sqlx::Type)]
#[sqlx(rename_all = "snake_case")]
enum MatchColumn {
    Queued,
    Running,
    Done,
    Failed,
}

/// One page of the home list, best matches first.
///
/// The filters test only the mentor row: each `IN` set is built once per query, whereas joining the
/// contact summary for every mentor made counting 100 000 mentors ten times slower. For the same
/// reason the page is cut before the joins, so skipped rows are never joined.
pub async fn list(db: &SqlitePool, query: &MentorQuery) -> Result<MentorPage, sqlx::Error> {
    let keyword = query.keyword.as_deref().and_then(keyword);
    let offset = i64::from(query.page.get() - 1) * i64::from(PAGE_SIZE);
    let rows = sqlx::query!(
        r#"SELECT m.id, m.name, m.title, m.university, m.school, m.department,
                  substr(d.research, 1, 120) AS "research?: String",
                  m.match_state AS "match_state: MatchColumn", m.match_score,
                  c.status AS "status?: ContactStatus", c.contact_count AS "contact_count?",
                  c.last_out_at, c.last_reply_at, c.unread AS "unread?: bool"
           FROM (SELECT m.id FROM mentor m
                 WHERE m.trashed_at IS NULL
                   AND (?1 IS NULL
                        OR (?1 = 'none' AND m.id NOT IN (SELECT mentor_id FROM mentor_contact))
                        OR m.id IN (SELECT mentor_id FROM mentor_contact WHERE status = ?1))
                   AND (?2 IS NULL OR m.id IN (SELECT rowid FROM mentor_search WHERE mentor_search MATCH ?2))
                 ORDER BY m.match_score DESC, m.id
                 LIMIT ?3 OFFSET ?4) page
           JOIN mentor m ON m.id = page.id
           LEFT JOIN mentor_detail d ON d.mentor_id = m.id
           LEFT JOIN mentor_contact c ON c.mentor_id = m.id
           ORDER BY m.match_score DESC, m.id"#,
        query.status,
        keyword,
        PAGE_SIZE,
        offset,
    )
    .fetch_all(db)
    .await?;
    let total = sqlx::query_scalar!(
        r#"SELECT count(*) AS "n!: i64"
           FROM mentor m
           WHERE m.trashed_at IS NULL
             AND (?1 IS NULL
                  OR (?1 = 'none' AND m.id NOT IN (SELECT mentor_id FROM mentor_contact))
                  OR m.id IN (SELECT mentor_id FROM mentor_contact WHERE status = ?1))
             AND (?2 IS NULL OR m.id IN (SELECT rowid FROM mentor_search WHERE mentor_search MATCH ?2))"#,
        query.status,
        keyword,
    )
    .fetch_one(db)
    .await?;

    let ids = rows
        .iter()
        .map(|r| r.id.to_string())
        .collect::<Vec<_>>()
        .join(",");
    let tag_rows = sqlx::query!(
        r#"SELECT mt.mentor_id, t.name, t.color
           FROM mentor_tag mt JOIN tag t ON t.id = mt.tag_id
           WHERE mt.mentor_id IN (SELECT value FROM json_each(?1))
           ORDER BY t.position"#,
        format!("[{ids}]"),
    )
    .fetch_all(db)
    .await?;
    let mut tags: BTreeMap<i64, Vec<TagLabel>> = BTreeMap::new();
    for t in tag_rows {
        tags.entry(t.mentor_id).or_default().push(TagLabel {
            name: t.name,
            color: t.color,
        });
    }

    let mentors = rows
        .into_iter()
        .map(|r| {
            let inconsistent = || {
                sqlx::Error::Decode(
                    format!("mentor {} has inconsistent match or contact columns", r.id).into(),
                )
            };
            let match_state = match (r.match_state, r.match_score) {
                (None, _) => MatchState::NotAnalyzed,
                (Some(MatchColumn::Queued), _) => MatchState::Queued,
                (Some(MatchColumn::Running), _) => MatchState::Running,
                (Some(MatchColumn::Done), Some(score)) => MatchState::Done { score },
                (Some(MatchColumn::Failed), _) => MatchState::Failed,
                (Some(MatchColumn::Done), None) => return Err(inconsistent()),
            };
            let contact = match (
                r.status,
                r.contact_count,
                r.last_out_at,
                r.last_reply_at,
                r.unread,
            ) {
                (None, ..) => Contact::None,
                (Some(ContactStatus::Contacted), Some(count), Some(last_out_at), _, _) => {
                    Contact::Contacted { count, last_out_at }
                }
                (Some(ContactStatus::Bounced), Some(count), Some(last_out_at), _, _) => {
                    Contact::Bounced { count, last_out_at }
                }
                (
                    Some(ContactStatus::Replied),
                    Some(count),
                    last_out_at,
                    Some(last_reply_at),
                    Some(unread),
                ) => Contact::Replied {
                    count,
                    last_out_at,
                    last_reply_at,
                    unread,
                },
                _ => return Err(inconsistent()),
            };
            Ok(MentorRow {
                id: r.id,
                name: r.name,
                title: r.title,
                university: r.university,
                school: r.school,
                department: r.department,
                research: r.research,
                match_state,
                contact,
                tags: tags.remove(&r.id).unwrap_or_default(),
            })
        })
        .collect::<Result<_, _>>()?;
    Ok(MentorPage { mentors, total })
}

/// The FTS5 query for a keyword search, in which every word must appear: a phrase of bigrams per
/// word. A single character would match nearly every mentor's text, so a one-character word only
/// searches the characters of names. None when the text has nothing to search for.
fn keyword(text: &str) -> Option<String> {
    let terms: Vec<String> = words(text)
        .map(|w| match w.as_slice() {
            [c] => format!("name:\"{c}\""),
            _ => format!("\"{}\"", bigrams_of(&w).join(" ")),
        })
        .collect();
    (!terms.is_empty()).then(|| terms.join(" "))
}

/// Runs of letters and digits, lowercased.
fn words(text: &str) -> impl Iterator<Item = Vec<char>> {
    text.split(|c: char| !c.is_alphanumeric())
        .filter(|w| !w.is_empty())
        .map(|w| w.to_lowercase().chars().collect())
}

fn bigrams_of(word: &[char]) -> Vec<String> {
    word.windows(2).map(|w| w.iter().collect()).collect()
}

/// The text stored in the keyword index: overlapping two-character tokens, so that a phrase of the
/// query's tokens finds any field containing the query (docs/data-model.md#导师). Words are kept
/// apart by a one-character token, which no query phrase contains, so a phrase never runs from the
/// end of one word into the next.
fn search_text(fields: &[Option<&str>]) -> String {
    fields
        .iter()
        .flatten()
        .flat_map(|f| words(f))
        .map(|w| bigrams_of(&w).join(" "))
        .collect::<Vec<_>>()
        .join(" 0 ")
}

/// The name's letters and digits as one-character tokens.
fn name_chars(name: &str) -> String {
    words(name)
        .flatten()
        .map(String::from)
        .collect::<Vec<_>>()
        .join(" ")
}

#[derive(Debug, Default)]
pub struct NewMentor {
    pub name: String,
    pub email: Option<String>,
    pub title: Option<String>,
    pub university: Option<String>,
    pub school: Option<String>,
    pub department: Option<String>,
    pub homepage_url: Option<String>,
    pub research: Option<String>,
}

/// Adds a mentor and indexes it for keyword search. `now` is UTC milliseconds.
pub async fn create<'c>(
    conn: impl Acquire<'c, Database = Sqlite>,
    mentor: &NewMentor,
    now: i64,
) -> Result<i64, sqlx::Error> {
    let mut tx = conn.begin().await?;
    let id = sqlx::query_scalar!(
        "INSERT INTO mentor (name, email, title, university, school, department, homepage_url, created_at, updated_at)
         VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) RETURNING id",
        mentor.name,
        mentor.email,
        mentor.title,
        mentor.university,
        mentor.school,
        mentor.department,
        mentor.homepage_url,
        now,
        now,
    )
    .fetch_one(&mut *tx)
    .await?;
    sqlx::query!(
        "INSERT INTO mentor_detail (mentor_id, research) VALUES (?, ?)",
        id,
        mentor.research
    )
    .execute(&mut *tx)
    .await?;
    let text = search_text(&[
        Some(&mentor.name),
        mentor.university.as_deref(),
        mentor.school.as_deref(),
        mentor.department.as_deref(),
        mentor.research.as_deref(),
    ]);
    sqlx::query!(
        "INSERT INTO mentor_search (rowid, body, name) VALUES (?, ?, ?)",
        id,
        text,
        name_chars(&mentor.name),
    )
    .execute(&mut *tx)
    .await?;
    tx.commit().await?;
    Ok(id)
}
