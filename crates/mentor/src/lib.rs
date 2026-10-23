//! Mentors: the home list with its filters, keyword search, one mentor's page, and adding and
//! editing mentors.

use std::collections::BTreeMap;
use std::num::NonZeroU32;

use serde::{Deserialize, Serialize};
use sqlx::types::Json;
use sqlx::{Acquire, Sqlite, SqliteConnection, SqlitePool};
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
#[serde(rename_all = "camelCase")]
pub struct MentorDetail {
    pub id: i64,
    pub name: String,
    pub email: Option<String>,
    pub title: Option<String>,
    pub university: Option<String>,
    pub school: Option<String>,
    pub department: Option<String>,
    pub homepage_url: Option<String>,
    /// The page the mentor was found on.
    pub source_url: Option<String>,
    pub research: Option<String>,
    pub papers: Vec<String>,
    pub note: Option<String>,
    pub analysis: MatchResult,
    pub contact: Contact,
    pub tags: Vec<TagLabel>,
}

/// The latest match analysis in full; `MatchState` is its summary in the list.
#[derive(Debug, Serialize, TS)]
#[serde(
    tag = "state",
    rename_all = "snake_case",
    rename_all_fields = "camelCase"
)]
pub enum MatchResult {
    NotAnalyzed,
    Queued,
    Running,
    Done {
        score: i64,
        matched_at: i64,
        model: String,
        reason: String,
        directions: Vec<String>,
        highlights: Vec<String>,
        risks: Vec<String>,
    },
    Failed {
        error: String,
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

    let mut tags = tags_of(db, rows.iter().map(|r| r.id)).await?;
    let mentors = rows
        .into_iter()
        .map(|r| {
            let match_state = match (r.match_state, r.match_score) {
                (None, _) => MatchState::NotAnalyzed,
                (Some(MatchColumn::Queued), _) => MatchState::Queued,
                (Some(MatchColumn::Running), _) => MatchState::Running,
                (Some(MatchColumn::Done), Some(score)) => MatchState::Done { score },
                (Some(MatchColumn::Failed), _) => MatchState::Failed,
                (Some(MatchColumn::Done), None) => return Err(inconsistent(r.id)),
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
                contact: contact(
                    r.id,
                    r.status,
                    r.contact_count,
                    r.last_out_at,
                    r.last_reply_at,
                    r.unread,
                )?,
                tags: tags.remove(&r.id).unwrap_or_default(),
            })
        })
        .collect::<Result<_, _>>()?;
    Ok(MentorPage { mentors, total })
}

/// Everything the mentor's page shows. None when there is no such mentor.
pub async fn get(db: &SqlitePool, id: i64) -> Result<Option<MentorDetail>, sqlx::Error> {
    let Some(r) = sqlx::query!(
        r#"SELECT m.id, m.name, m.email, m.title, m.university, m.school, m.department,
                  m.homepage_url, m.source_url,
                  m.match_state AS "match_state: MatchColumn", m.match_score, m.matched_at,
                  m.match_model, m.match_error,
                  d.research, d.papers AS "papers: Json<Vec<String>>", d.note, d.match_reason,
                  d.match_directions AS "match_directions: Json<Vec<String>>",
                  d.match_highlights AS "match_highlights: Json<Vec<String>>",
                  d.match_risks AS "match_risks: Json<Vec<String>>",
                  c.status AS "status?: ContactStatus", c.contact_count AS "contact_count?",
                  c.last_out_at, c.last_reply_at, c.unread AS "unread?: bool"
           FROM mentor m
           JOIN mentor_detail d ON d.mentor_id = m.id
           LEFT JOIN mentor_contact c ON c.mentor_id = m.id
           WHERE m.id = ?"#,
        id,
    )
    .fetch_optional(db)
    .await?
    else {
        return Ok(None);
    };
    let analysis = match r.match_state {
        None => MatchResult::NotAnalyzed,
        Some(MatchColumn::Queued) => MatchResult::Queued,
        Some(MatchColumn::Running) => MatchResult::Running,
        Some(MatchColumn::Done) => match (
            r.match_score,
            r.matched_at,
            r.match_model,
            r.match_reason,
            r.match_directions,
            r.match_highlights,
            r.match_risks,
        ) {
            (
                Some(score),
                Some(matched_at),
                Some(model),
                Some(reason),
                Some(Json(directions)),
                Some(Json(highlights)),
                Some(Json(risks)),
            ) => MatchResult::Done {
                score,
                matched_at,
                model,
                reason,
                directions,
                highlights,
                risks,
            },
            _ => return Err(inconsistent(id)),
        },
        Some(MatchColumn::Failed) => MatchResult::Failed {
            error: r.match_error.ok_or_else(|| inconsistent(id))?,
        },
    };
    Ok(Some(MentorDetail {
        id: r.id,
        name: r.name,
        email: r.email,
        title: r.title,
        university: r.university,
        school: r.school,
        department: r.department,
        homepage_url: r.homepage_url,
        source_url: r.source_url,
        research: r.research,
        papers: r.papers.map(|Json(p)| p).unwrap_or_default(),
        note: r.note,
        analysis,
        contact: contact(
            id,
            r.status,
            r.contact_count,
            r.last_out_at,
            r.last_reply_at,
            r.unread,
        )?,
        tags: tags_of(db, [id]).await?.remove(&id).unwrap_or_default(),
    }))
}

/// Replaces the mentor's private note; None clears it. False when there is no such mentor.
pub async fn set_note(db: &SqlitePool, id: i64, note: Option<&str>) -> Result<bool, sqlx::Error> {
    let done = sqlx::query!(
        "UPDATE mentor_detail SET note = ? WHERE mentor_id = ?",
        note,
        id
    )
    .execute(db)
    .await?;
    Ok(done.rows_affected() == 1)
}

/// The tags of each of `ids`, in the user's order.
async fn tags_of(
    db: &SqlitePool,
    ids: impl IntoIterator<Item = i64>,
) -> Result<BTreeMap<i64, Vec<TagLabel>>, sqlx::Error> {
    let ids = ids
        .into_iter()
        .map(|id| id.to_string())
        .collect::<Vec<_>>()
        .join(",");
    let rows = sqlx::query!(
        r#"SELECT mt.mentor_id, t.name, t.color
           FROM mentor_tag mt JOIN tag t ON t.id = mt.tag_id
           WHERE mt.mentor_id IN (SELECT value FROM json_each(?1))
           ORDER BY t.position"#,
        format!("[{ids}]"),
    )
    .fetch_all(db)
    .await?;
    let mut tags: BTreeMap<i64, Vec<TagLabel>> = BTreeMap::new();
    for t in rows {
        tags.entry(t.mentor_id).or_default().push(TagLabel {
            name: t.name,
            color: t.color,
        });
    }
    Ok(tags)
}

/// The contact summary's columns as a `Contact`; they are absent for a mentor never contacted.
fn contact(
    id: i64,
    status: Option<ContactStatus>,
    count: Option<i64>,
    last_out_at: Option<i64>,
    last_reply_at: Option<i64>,
    unread: Option<bool>,
) -> Result<Contact, sqlx::Error> {
    Ok(match (status, count, last_out_at, last_reply_at, unread) {
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
        _ => return Err(inconsistent(id)),
    })
}

fn inconsistent(id: i64) -> sqlx::Error {
    sqlx::Error::Decode(format!("mentor {id} has inconsistent match or contact columns").into())
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

/// A mentor's fields as the user typed them in the form; `check` turns them into `MentorFields`.
#[derive(Debug, Deserialize, TS)]
#[serde(rename_all = "camelCase")]
pub struct MentorForm {
    pub name: String,
    pub title: String,
    pub email: String,
    pub university: String,
    pub school: String,
    pub department: String,
    pub homepage_url: String,
    pub research: String,
}

impl MentorForm {
    /// Trims every field, leaving blank ones empty, and checks those with a format. The error says
    /// what to fix.
    pub fn check(self) -> Result<MentorFields, String> {
        fn text(s: String) -> Option<String> {
            let t = s.trim();
            (!t.is_empty()).then(|| t.to_owned())
        }
        let name = text(self.name).ok_or("请填写姓名")?;
        let email = text(self.email);
        if email.as_deref().is_some_and(|e| !is_email(e)) {
            return Err("邮箱地址格式不正确".into());
        }
        let homepage_url = text(self.homepage_url);
        if homepage_url.as_deref().is_some_and(|u| !is_web_page(u)) {
            return Err("主页地址不正确，需以 http:// 或 https:// 开头".into());
        }
        Ok(MentorFields {
            name,
            email,
            title: text(self.title),
            university: text(self.university),
            school: text(self.school),
            department: text(self.department),
            homepage_url,
            research: text(self.research),
        })
    }
}

/// A plain address as people write them: `local@domain`, where the local part is dot-separated
/// runs of letters, digits and the symbols addresses allow, and the domain is two or more labels.
/// Catches what copying from a web page drags along, such as a trailing "；" or "/people".
fn is_email(s: &str) -> bool {
    let Some((local, domain)) = s.split_once('@') else {
        return false;
    };
    let atom = |part: &str| {
        !part.is_empty()
            && part
                .chars()
                .all(|c| c.is_alphanumeric() || "!#$%&'*+/=?^_`{|}~-".contains(c))
    };
    let label = |part: &str| {
        !part.is_empty()
            && !part.starts_with('-')
            && !part.ends_with('-')
            && part.chars().all(|c| c.is_alphanumeric() || c == '-')
    };
    local.split('.').all(atom) && domain.split('.').count() > 1 && domain.split('.').all(label)
}

/// An http or https address with a host, in any letter case.
fn is_web_page(s: &str) -> bool {
    url::Url::parse(s).is_ok_and(|u| {
        matches!(u.scheme(), "http" | "https")
            && u.host().is_some()
            && !s.contains(char::is_whitespace)
    })
}

/// A mentor's own fields, checked; blank ones are None.
#[derive(Debug, Default)]
pub struct MentorFields {
    pub name: String,
    pub email: Option<String>,
    pub title: Option<String>,
    pub university: Option<String>,
    pub school: Option<String>,
    pub department: Option<String>,
    pub homepage_url: Option<String>,
    pub research: Option<String>,
}

#[derive(Debug)]
pub enum SaveError {
    /// Another mentor, perhaps one in the trash, already has the email.
    EmailTaken {
        name: String,
        trashed: bool,
    },
    Db(sqlx::Error),
}

impl From<sqlx::Error> for SaveError {
    fn from(e: sqlx::Error) -> Self {
        SaveError::Db(e)
    }
}

impl std::fmt::Display for SaveError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            SaveError::EmailTaken { .. } => write!(f, "email belongs to another mentor"),
            SaveError::Db(e) => write!(f, "{e}"),
        }
    }
}

impl std::error::Error for SaveError {}

/// Adds a mentor and indexes it for keyword search. `now` is UTC milliseconds.
pub async fn create<'c>(
    conn: impl Acquire<'c, Database = Sqlite>,
    mentor: &MentorFields,
    now: i64,
) -> Result<i64, SaveError> {
    let mut tx = conn.begin().await?;
    let inserted = sqlx::query_scalar!(
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
    .await;
    let id = match inserted {
        Ok(id) => id,
        Err(e) => return Err(save_error(&mut tx, e, mentor, None).await),
    };
    sqlx::query!(
        "INSERT INTO mentor_detail (mentor_id, research) VALUES (?, ?)",
        id,
        mentor.research
    )
    .execute(&mut *tx)
    .await?;
    index(&mut tx, id, mentor).await?;
    tx.commit().await?;
    Ok(id)
}

/// Replaces the mentor's own fields and reindexes it. False when there is no such mentor.
pub async fn update(
    db: &SqlitePool,
    id: i64,
    mentor: &MentorFields,
    now: i64,
) -> Result<bool, SaveError> {
    let mut tx = db.begin().await?;
    let updated = sqlx::query!(
        "UPDATE mentor SET name = ?, email = ?, title = ?, university = ?, school = ?, department = ?,
           homepage_url = ?, updated_at = ?
         WHERE id = ?",
        mentor.name,
        mentor.email,
        mentor.title,
        mentor.university,
        mentor.school,
        mentor.department,
        mentor.homepage_url,
        now,
        id,
    )
    .execute(&mut *tx)
    .await;
    let done = match updated {
        Ok(done) => done,
        Err(e) => return Err(save_error(&mut tx, e, mentor, Some(id)).await),
    };
    if done.rows_affected() == 0 {
        return Ok(false);
    }
    sqlx::query!(
        "UPDATE mentor_detail SET research = ? WHERE mentor_id = ?",
        mentor.research,
        id
    )
    .execute(&mut *tx)
    .await?;
    index(&mut tx, id, mentor).await?;
    tx.commit().await?;
    Ok(true)
}

/// Explains a failed insert or update. The unique index on email is the only one these statements
/// can break, so the user learns which mentor has the address. Writing first and explaining
/// afterwards keeps the transaction a plain write: reading before writing would make it fail at
/// once instead of waiting whenever another save commits in between.
async fn save_error(
    tx: &mut SqliteConnection,
    error: sqlx::Error,
    mentor: &MentorFields,
    id: Option<i64>,
) -> SaveError {
    let unique = error
        .as_database_error()
        .is_some_and(|e| e.is_unique_violation());
    let Some(email) = mentor.email.as_deref().filter(|_| unique) else {
        return error.into();
    };
    // The column's NOCASE collation applies, as it does for the unique index.
    let owner = sqlx::query!(
        r#"SELECT name, trashed_at IS NOT NULL AS "trashed!: bool"
           FROM mentor WHERE email = ? AND id IS NOT ?"#,
        email,
        id
    )
    .fetch_optional(&mut *tx)
    .await;
    match owner {
        Ok(Some(o)) => SaveError::EmailTaken {
            name: o.name,
            trashed: o.trashed,
        },
        Ok(None) => error.into(),
        Err(e) => e.into(),
    }
}

/// Writes the mentor's keyword index entry, replacing any earlier one.
async fn index(
    tx: &mut SqliteConnection,
    id: i64,
    mentor: &MentorFields,
) -> Result<(), sqlx::Error> {
    let text = search_text(&[
        Some(&mentor.name),
        mentor.university.as_deref(),
        mentor.school.as_deref(),
        mentor.department.as_deref(),
        mentor.research.as_deref(),
    ]);
    sqlx::query!("DELETE FROM mentor_search WHERE rowid = ?", id)
        .execute(&mut *tx)
        .await?;
    sqlx::query!(
        "INSERT INTO mentor_search (rowid, body, name) VALUES (?, ?, ?)",
        id,
        text,
        name_chars(&mentor.name),
    )
    .execute(&mut *tx)
    .await?;
    Ok(())
}
