//! Mentors: the home list with its filters, keyword search, one mentor's page, adding and editing
//! mentors, their tags and the trash.

use std::collections::BTreeMap;
use std::num::NonZeroU32;

use serde::{Deserialize, Serialize};
use sqlx::types::Json;
use sqlx::{Acquire, Sqlite, SqliteConnection, SqlitePool};
use ts_rs::TS;

pub mod merge;
pub mod tags;
pub mod trash;

use tags::Tag;

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
    /// The first of the mentor's affiliations, the main one.
    pub affiliation: Option<Affiliation>,
    pub affiliation_count: i64,
    /// The start of the research description, enough for the two lines the list shows.
    pub research: Option<String>,
    pub match_state: MatchState,
    pub contact: Contact,
    pub tags: Vec<Tag>,
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

/// Where a mentor works, and as what there (docs/data-model.md#导师). At least one is filled in.
#[derive(Debug, Clone, Default, PartialEq, Eq, Deserialize, Serialize, TS)]
#[serde(rename_all = "camelCase")]
pub struct Affiliation {
    pub university: Option<String>,
    pub school: Option<String>,
    pub department: Option<String>,
    pub title: Option<String>,
}

#[derive(Debug, Serialize, TS)]
#[serde(rename_all = "camelCase")]
pub struct MentorDetail {
    pub id: i64,
    pub name: String,
    pub aliases: Vec<String>,
    /// The first is the one mail goes to.
    pub emails: Vec<String>,
    /// The first is the main one.
    pub affiliations: Vec<Affiliation>,
    pub homepages: Vec<String>,
    /// The page the mentor was found on.
    pub source_url: Option<String>,
    pub research: Option<String>,
    pub papers: Vec<String>,
    pub note: Option<String>,
    pub analysis: MatchResult,
    pub contact: Contact,
    pub tags: Vec<Tag>,
    pub trashed: bool,
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
/// reason the page is cut before `rows` joins anything, so skipped rows are never joined.
pub async fn list(db: &SqlitePool, query: &MentorQuery) -> Result<MentorPage, sqlx::Error> {
    let keyword = query.keyword.as_deref().and_then(keyword);
    // One snapshot for the page, the total and the rows; read only, so it simply ends when dropped.
    let mut tx = db.begin().await?;
    let ids = sqlx::query_scalar!(
        "SELECT m.id FROM mentor m
         WHERE m.trashed_at IS NULL
           AND (?1 IS NULL
                OR (?1 = 'none' AND m.id NOT IN (SELECT mentor_id FROM mentor_contact))
                OR m.id IN (SELECT mentor_id FROM mentor_contact WHERE status = ?1))
           AND (?2 IS NULL OR m.id IN (SELECT rowid FROM mentor_search WHERE mentor_search MATCH ?2))
         ORDER BY m.match_score DESC, m.id
         LIMIT ?3 OFFSET ?4",
        query.status,
        keyword,
        PAGE_SIZE,
        offset(query.page),
    )
    .fetch_all(&mut *tx)
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
    .fetch_one(&mut *tx)
    .await?;
    Ok(MentorPage {
        mentors: rows(&mut tx, &ids).await?,
        total,
    })
}

fn offset(page: NonZeroU32) -> i64 {
    i64::from(page.get() - 1) * i64::from(PAGE_SIZE)
}

/// The list rows for these mentors, in the given order.
async fn rows(conn: &mut SqliteConnection, ids: &[i64]) -> Result<Vec<MentorRow>, sqlx::Error> {
    let rows = sqlx::query!(
        r#"SELECT m.id, m.name, a.university, a.school, a.department, a.title,
                  (SELECT count(*) FROM mentor_affiliation WHERE mentor_id = m.id)
                    AS "affiliation_count!: i64",
                  substr(d.research, 1, 120) AS "research?: String",
                  m.match_state AS "match_state: MatchColumn", m.match_score,
                  c.status AS "status?: ContactStatus", c.contact_count AS "contact_count?",
                  c.last_out_at, c.last_reply_at, c.unread AS "unread?: bool"
           FROM json_each(?) page
           JOIN mentor m ON m.id = page.value
           LEFT JOIN mentor_affiliation a ON a.mentor_id = m.id
             AND a.position = (SELECT min(position) FROM mentor_affiliation WHERE mentor_id = m.id)
           LEFT JOIN mentor_detail d ON d.mentor_id = m.id
           LEFT JOIN mentor_contact c ON c.mentor_id = m.id
           ORDER BY page.key"#,
        Json(ids),
    )
    .fetch_all(&mut *conn)
    .await?;
    let mut tags = tags_of(conn, ids).await?;
    rows.into_iter()
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
                affiliation: (r.affiliation_count > 0).then_some(Affiliation {
                    university: r.university,
                    school: r.school,
                    department: r.department,
                    title: r.title,
                }),
                affiliation_count: r.affiliation_count,
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
        .collect()
}

/// Everything the mentor's page shows. None when there is no such mentor.
pub async fn get(db: &SqlitePool, id: i64) -> Result<Option<MentorDetail>, sqlx::Error> {
    detail(&mut *db.begin().await?, id).await
}

async fn detail(conn: &mut SqliteConnection, id: i64) -> Result<Option<MentorDetail>, sqlx::Error> {
    let Some(r) = sqlx::query!(
        r#"SELECT m.id, m.name, m.source_url, m.trashed_at IS NOT NULL AS "trashed!: bool",
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
    .fetch_optional(&mut *conn)
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
    let aliases = sqlx::query_scalar!(
        "SELECT name FROM mentor_alias WHERE mentor_id = ? ORDER BY position",
        id
    )
    .fetch_all(&mut *conn)
    .await?;
    let emails = sqlx::query_scalar!(
        "SELECT address FROM mentor_email WHERE mentor_id = ? ORDER BY position",
        id
    )
    .fetch_all(&mut *conn)
    .await?;
    let affiliations = sqlx::query_as!(
        Affiliation,
        "SELECT university, school, department, title FROM mentor_affiliation
         WHERE mentor_id = ? ORDER BY position",
        id
    )
    .fetch_all(&mut *conn)
    .await?;
    let homepages = sqlx::query_scalar!(
        "SELECT url FROM mentor_homepage WHERE mentor_id = ? ORDER BY position",
        id
    )
    .fetch_all(&mut *conn)
    .await?;
    Ok(Some(MentorDetail {
        id: r.id,
        name: r.name,
        aliases,
        emails,
        affiliations,
        homepages,
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
        tags: tags_of(&mut *conn, &[id])
            .await?
            .remove(&id)
            .unwrap_or_default(),
        trashed: r.trashed,
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
    conn: &mut SqliteConnection,
    ids: &[i64],
) -> Result<BTreeMap<i64, Vec<Tag>>, sqlx::Error> {
    let rows = sqlx::query!(
        r#"SELECT mt.mentor_id, t.id, t.name, t.color
           FROM mentor_tag mt JOIN tag t ON t.id = mt.tag_id
           WHERE mt.mentor_id IN (SELECT value FROM json_each(?1))
           ORDER BY t.position"#,
        Json(ids),
    )
    .fetch_all(conn)
    .await?;
    let mut tags: BTreeMap<i64, Vec<Tag>> = BTreeMap::new();
    for t in rows {
        tags.entry(t.mentor_id).or_default().push(Tag {
            id: t.id,
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
fn search_text(fields: &[&str]) -> String {
    fields
        .iter()
        .flat_map(|f| words(f))
        .map(|w| bigrams_of(&w).join(" "))
        .collect::<Vec<_>>()
        .join(" 0 ")
}

/// The letters and digits of the names as one-character tokens.
fn name_chars(names: &[&str]) -> String {
    names
        .iter()
        .flat_map(|n| words(n))
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
    pub aliases: Vec<String>,
    pub emails: Vec<String>,
    pub affiliations: Vec<Affiliation>,
    pub homepages: Vec<String>,
    pub research: String,
}

impl MentorForm {
    /// Trims every field and leaves out blank and repeated entries, keeping the order, and checks
    /// those with a format. The error says what to fix.
    pub fn check(self) -> Result<MentorFields, String> {
        fn text(s: &str) -> Option<String> {
            let t = s.trim();
            (!t.is_empty()).then(|| t.to_owned())
        }
        let name = text(&self.name).ok_or("请填写姓名")?;
        let mut aliases = Vec::new();
        for alias in self.aliases.iter().filter_map(|a| text(a)) {
            if alias != name && !aliases.contains(&alias) {
                aliases.push(alias);
            }
        }
        let mut emails: Vec<String> = Vec::new();
        for email in self.emails.iter().filter_map(|e| text(e)) {
            if !is_email(&email) {
                return Err(format!("邮箱地址“{email}”格式不正确"));
            }
            // As the database compares them.
            if !emails.iter().any(|e| e.eq_ignore_ascii_case(&email)) {
                emails.push(email);
            }
        }
        let field = |f: &Option<String>| f.as_deref().and_then(text);
        let mut affiliations = Vec::new();
        for a in &self.affiliations {
            let a = Affiliation {
                university: field(&a.university),
                school: field(&a.school),
                department: field(&a.department),
                title: field(&a.title),
            };
            if a != Affiliation::default() && !affiliations.contains(&a) {
                affiliations.push(a);
            }
        }
        let mut homepages = Vec::new();
        for url in self.homepages.iter().filter_map(|u| text(u)) {
            if !is_web_page(&url) {
                return Err(format!("主页“{url}”不正确，需以 http:// 或 https:// 开头"));
            }
            if !homepages.contains(&url) {
                homepages.push(url);
            }
        }
        Ok(MentorFields {
            name,
            aliases,
            emails,
            affiliations,
            homepages,
            research: text(&self.research),
        })
    }
}

/// A plain address as people write them: `local@domain`, where the local part is dot-separated
/// runs of letters, digits and the symbols addresses allow, and the domain is two or more labels.
/// Catches what copying from a web page drags along, such as a trailing "；" or "/people".
pub fn is_email(s: &str) -> bool {
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

/// A mentor's own fields, checked: blank ones are None or left out of their lists.
#[derive(Debug, Default)]
pub struct MentorFields {
    pub name: String,
    pub aliases: Vec<String>,
    pub emails: Vec<String>,
    pub affiliations: Vec<Affiliation>,
    pub homepages: Vec<String>,
    pub research: Option<String>,
}

#[derive(Debug)]
pub enum SaveError {
    /// Another mentor, perhaps one in the trash, already has the address.
    EmailTaken {
        address: String,
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
    let id = sqlx::query_scalar!(
        "INSERT INTO mentor (name, created_at, updated_at) VALUES (?, ?, ?) RETURNING id",
        mentor.name,
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
    save_lists(&mut tx, id, mentor).await?;
    index(&mut tx, id, mentor).await?;
    tx.commit().await?;
    Ok(id)
}

/// Records the mailbox that first sends to the mentor as the one to contact them from, unless the
/// mentor has one (docs/data-model.md#导师), or the mailbox has been deleted while it sent.
pub async fn assign_mailbox(
    conn: &mut SqliteConnection,
    id: i64,
    mailbox_id: i64,
) -> Result<(), sqlx::Error> {
    sqlx::query!(
        "UPDATE mentor SET mailbox_id = ?1
         WHERE id = ?2 AND mailbox_id IS NULL AND ?1 IN (SELECT id FROM mailbox)",
        mailbox_id,
        id,
    )
    .execute(conn)
    .await?;
    Ok(())
}

/// Replaces the mentor's own fields and reindexes it. False when there is no such mentor.
pub async fn update(
    db: &SqlitePool,
    id: i64,
    mentor: &MentorFields,
    now: i64,
) -> Result<bool, SaveError> {
    let mut tx = db.begin().await?;
    let done = sqlx::query!(
        "UPDATE mentor SET name = ?, updated_at = ? WHERE id = ?",
        mentor.name,
        now,
        id,
    )
    .execute(&mut *tx)
    .await?;
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
    save_lists(&mut tx, id, mentor).await?;
    index(&mut tx, id, mentor).await?;
    tx.commit().await?;
    Ok(true)
}

/// Writes the mentor's lists in the given order. An address the mentor keeps keeps its row, so
/// that it is not looked up in the mailboxes again; one another mentor has fails the save.
async fn save_lists(
    tx: &mut SqliteConnection,
    id: i64,
    mentor: &MentorFields,
) -> Result<(), SaveError> {
    let emails = Json(&mentor.emails);
    sqlx::query!(
        "DELETE FROM mentor_email
         WHERE mentor_id = ? AND address NOT IN (SELECT value FROM json_each(?))",
        id,
        emails,
    )
    .execute(&mut *tx)
    .await?;
    for (position, address) in (0_i64..).zip(&mentor.emails) {
        let saved = sqlx::query!(
            "INSERT INTO mentor_email (address, mentor_id, position) VALUES (?, ?, ?)
             ON CONFLICT (address) DO UPDATE SET address = excluded.address, position = excluded.position
             WHERE mentor_id = excluded.mentor_id",
            address,
            id,
            position,
        )
        .execute(&mut *tx)
        .await?;
        if saved.rows_affected() == 0 {
            return Err(taken(tx, address).await);
        }
    }
    sqlx::query!("DELETE FROM mentor_affiliation WHERE mentor_id = ?", id)
        .execute(&mut *tx)
        .await?;
    let affiliations = Json(&mentor.affiliations);
    sqlx::query!(
        "INSERT INTO mentor_affiliation (mentor_id, position, university, school, department, title)
         SELECT ?1, j.key, j.value ->> 'university', j.value ->> 'school',
           j.value ->> 'department', j.value ->> 'title'
         FROM json_each(?2) j",
        id,
        affiliations,
    )
    .execute(&mut *tx)
    .await?;
    sqlx::query!("DELETE FROM mentor_homepage WHERE mentor_id = ?", id)
        .execute(&mut *tx)
        .await?;
    let homepages = Json(&mentor.homepages);
    sqlx::query!(
        "INSERT INTO mentor_homepage (mentor_id, position, url)
         SELECT ?1, j.key, j.value FROM json_each(?2) j",
        id,
        homepages,
    )
    .execute(&mut *tx)
    .await?;
    sqlx::query!("DELETE FROM mentor_alias WHERE mentor_id = ?", id)
        .execute(&mut *tx)
        .await?;
    let aliases = Json(&mentor.aliases);
    sqlx::query!(
        "INSERT INTO mentor_alias (mentor_id, position, name)
         SELECT ?1, j.key, j.value FROM json_each(?2) j",
        id,
        aliases,
    )
    .execute(&mut *tx)
    .await?;
    Ok(())
}

/// Whether an address is a school's, which goes before other addresses the app adds: one with a
/// domain label `edu` or `ac`, as pku.edu.cn, ucas.ac.cn, mit.edu or ox.ac.uk.
pub fn is_school(address: &str) -> bool {
    address.rsplit_once('@').is_some_and(|(_, domain)| {
        domain
            .split('.')
            .any(|label| label.eq_ignore_ascii_case("edu") || label.eq_ignore_ascii_case("ac"))
    })
}

/// Where an address the app adds goes among the mentor's: a school's before the first that is not,
/// else last (docs/data-model.md#导师).
fn place(addresses: &[String], address: &str) -> usize {
    addresses
        .iter()
        .position(|a| is_school(address) && !is_school(a))
        .unwrap_or(addresses.len())
}

/// Adds an address, such as one the mentor wrote from, to the mentor's where [`place`] puts it.
/// False when the mentor is gone.
pub async fn add_email(
    db: &SqlitePool,
    id: i64,
    address: &str,
    now: i64,
) -> Result<bool, SaveError> {
    let mut tx = db.begin().await?;
    let done = sqlx::query!("UPDATE mentor SET updated_at = ? WHERE id = ?", now, id)
        .execute(&mut *tx)
        .await?;
    if done.rows_affected() == 0 {
        return Ok(false);
    }
    let owner = sqlx::query_scalar!(
        "SELECT mentor_id FROM mentor_email WHERE address = ?",
        address
    )
    .fetch_optional(&mut *tx)
    .await?;
    match owner {
        Some(owner) if owner == id => return Ok(true),
        Some(_) => return Err(taken(&mut tx, address).await),
        None => {}
    }
    let addresses = sqlx::query_scalar!(
        "SELECT address FROM mentor_email WHERE mentor_id = ? ORDER BY position",
        id
    )
    .fetch_all(&mut *tx)
    .await?;
    let position = place(&addresses, address) as i64;
    sqlx::query!(
        "UPDATE mentor_email SET position = position + 1 WHERE mentor_id = ? AND position >= ?",
        id,
        position,
    )
    .execute(&mut *tx)
    .await?;
    sqlx::query!(
        "INSERT INTO mentor_email (address, mentor_id, position) VALUES (?, ?, ?)",
        address,
        id,
        position,
    )
    .execute(&mut *tx)
    .await?;
    tx.commit().await?;
    Ok(true)
}

/// Says which mentor has the address. Writing first and explaining afterwards keeps the
/// transaction a plain write: reading before writing would make it fail at once instead of waiting
/// whenever another save commits in between.
async fn taken(tx: &mut SqliteConnection, address: &str) -> SaveError {
    // The column's NOCASE collation applies, as it does for the key.
    let owner = sqlx::query!(
        r#"SELECT m.name, m.trashed_at IS NOT NULL AS "trashed!: bool"
           FROM mentor_email e JOIN mentor m ON m.id = e.mentor_id WHERE e.address = ?"#,
        address
    )
    .fetch_one(tx)
    .await;
    match owner {
        Ok(o) => SaveError::EmailTaken {
            address: address.to_owned(),
            name: o.name,
            trashed: o.trashed,
        },
        Err(e) => e.into(),
    }
}

/// Writes the mentor's keyword index entry, replacing any earlier one.
async fn index(
    tx: &mut SqliteConnection,
    id: i64,
    mentor: &MentorFields,
) -> Result<(), sqlx::Error> {
    let names: Vec<&str> = std::iter::once(&mentor.name)
        .chain(&mentor.aliases)
        .map(String::as_str)
        .collect();
    let places = mentor.affiliations.iter().flat_map(|a| {
        [&a.university, &a.school, &a.department]
            .into_iter()
            .filter_map(Option::as_deref)
    });
    let fields: Vec<&str> = names
        .iter()
        .copied()
        .chain(places)
        .chain(mentor.research.as_deref())
        .collect();
    let (body, name) = (search_text(&fields), name_chars(&names));
    sqlx::query!("DELETE FROM mentor_search WHERE rowid = ?", id)
        .execute(&mut *tx)
        .await?;
    sqlx::query!(
        "INSERT INTO mentor_search (rowid, body, name) VALUES (?, ?, ?)",
        id,
        body,
        name,
    )
    .execute(&mut *tx)
    .await?;
    Ok(())
}
