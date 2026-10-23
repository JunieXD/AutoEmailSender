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
/// contact summary for every mentor made counting 100 000 mentors ten times slower.
pub async fn list(db: &SqlitePool, query: &MentorQuery) -> Result<MentorPage, sqlx::Error> {
    let (phrases, name_like) = match query.keyword.as_deref().and_then(keyword) {
        Some(Keyword::Phrases(p)) => (Some(p), None),
        Some(Keyword::NameChar(c)) => (None, Some(format!("%{c}%"))),
        None => (None, None),
    };
    let offset = i64::from(query.page.get() - 1) * i64::from(PAGE_SIZE);
    let rows = sqlx::query!(
        r#"SELECT m.id, m.name, m.title, m.university, m.school, m.department,
                  substr(d.research, 1, 120) AS "research?: String",
                  m.match_state AS "match_state: MatchColumn", m.match_score,
                  c.status AS "status?: ContactStatus", c.contact_count AS "contact_count?",
                  c.last_out_at, c.last_reply_at, c.unread AS "unread?: bool"
           FROM mentor m
           LEFT JOIN mentor_detail d ON d.mentor_id = m.id
           LEFT JOIN mentor_contact c ON c.mentor_id = m.id
           WHERE m.trashed_at IS NULL
             AND (?1 IS NULL
                  OR (?1 = 'none' AND m.id NOT IN (SELECT mentor_id FROM mentor_contact))
                  OR m.id IN (SELECT mentor_id FROM mentor_contact WHERE status = ?1))
             AND (?2 IS NULL OR m.id IN (SELECT rowid FROM mentor_search WHERE mentor_search MATCH ?2))
             AND (?3 IS NULL OR m.name LIKE ?3)
           ORDER BY m.match_score DESC, m.id
           LIMIT ?4 OFFSET ?5"#,
        query.status,
        phrases,
        name_like,
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
             AND (?2 IS NULL OR m.id IN (SELECT rowid FROM mentor_search WHERE mentor_search MATCH ?2))
             AND (?3 IS NULL OR m.name LIKE ?3)"#,
        query.status,
        phrases,
        name_like,
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

enum Keyword {
    /// FTS5 query: one quoted phrase of bigrams per word, all of which must appear.
    Phrases(String),
    /// A single character only searches names.
    NameChar(char),
}

/// None when the text has nothing to search for.
fn keyword(text: &str) -> Option<Keyword> {
    let words: Vec<Vec<char>> = words(text).collect();
    if let [word] = words.as_slice()
        && let [c] = word.as_slice()
    {
        return Some(Keyword::NameChar(*c));
    }
    let phrases: Vec<String> = words
        .iter()
        .filter(|w| w.len() >= 2)
        .map(|w| format!("\"{}\"", bigrams_of(w).join(" ")))
        .collect();
    (!phrases.is_empty()).then(|| Keyword::Phrases(phrases.join(" ")))
}

/// Runs of letters and digits, lowercased.
fn words(text: &str) -> impl Iterator<Item = Vec<char>> {
    text.split(|c: char| !c.is_alphanumeric())
        .filter(|w| !w.is_empty())
        .map(|w| w.to_lowercase().chars().collect())
}

fn bigrams_of(word: &[char]) -> Vec<String> {
    if word.len() == 1 {
        vec![word.iter().collect()]
    } else {
        word.windows(2).map(|w| w.iter().collect()).collect()
    }
}

/// The text stored in the keyword index: overlapping two-character tokens, so that a phrase of the
/// query's tokens finds any field containing the query (docs/data-model.md#导师).
fn search_text(fields: &[Option<&str>]) -> String {
    fields
        .iter()
        .flatten()
        .flat_map(|f| words(f))
        .flat_map(|w| bigrams_of(&w))
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
        "INSERT INTO mentor_search (rowid, body) VALUES (?, ?)",
        id,
        text
    )
    .execute(&mut *tx)
    .await?;
    tx.commit().await?;
    Ok(id)
}
