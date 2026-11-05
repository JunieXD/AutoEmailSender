//! Candidate review uses the very same intake calculation and writes as table imports.
use crate::{PageUrl, extract::Person, queue, store::PageKind};
use mentor::{
    MentorFields,
    export::Profile,
    import::Decisions,
    intake::{self, Row},
};
use serde::{Deserialize, Serialize};
use sqlx::{SqliteConnection, SqlitePool, types::Json};
use ts_rs::TS;

#[derive(Debug, Clone, Serialize, Deserialize, TS, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub struct Evidence {
    pub url: String,
    pub name: String,
    pub emails: Vec<String>,
    pub research: Option<String>,
}
#[derive(Debug, Serialize, TS)]
#[serde(rename_all = "camelCase")]
#[ts(rename = "CrawlCandidate")]
pub struct Candidate {
    pub id: i64,
    pub crawl_id: Option<i64>,
    pub review: String,
    pub mentor_id: Option<i64>,
    pub fields: MentorFields,
    pub papers: Vec<String>,
    pub source_url: Option<String>,
    pub note: Option<String>,
    #[serde(flatten)]
    pub enrichment: crate::enrich::Enrichment,
}
#[derive(Debug, Serialize, TS)]
pub struct CandidateDetail {
    #[serde(flatten)]
    pub candidate: Candidate,
    pub evidence: Vec<Evidence>,
}

#[derive(Debug, Deserialize, TS)]
#[serde(rename_all = "camelCase")]
pub struct CandidateQuery {
    pub crawl_id: Option<i64>,
    pub processed: bool,
    pub filter: CandidateFilter,
    pub page: u32,
}
#[derive(Debug, Default, Clone, Copy, Deserialize, Serialize, TS, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum CandidateFilter {
    #[default]
    All,
    New,
    Supplement,
    Maybe,
    MissingEmail,
}
#[derive(Debug, Serialize, TS)]
#[serde(rename_all = "camelCase")]
pub struct CandidateRow {
    pub id: i64,
    pub crawl_id: Option<i64>,
    pub name: String,
    pub emails: Vec<String>,
    pub affiliation: Option<mentor::Affiliation>,
    pub research: Option<String>,
    pub source_url: Option<String>,
    #[serde(flatten)]
    pub enrichment: crate::enrich::Enrichment,
    pub review: String,
    pub mentor_id: Option<i64>,
    pub category: CandidateFilter,
    pub issue: Option<String>,
}
#[derive(Debug, Serialize, TS)]
pub struct CandidatePage {
    pub candidates: Vec<CandidateRow>,
    pub total: i64,
}
#[derive(Debug, Deserialize, TS)]
pub struct ReviewInput {
    pub ids: Vec<i64>,
    pub decisions: Decisions,
}
#[derive(Debug, Serialize, TS)]
pub struct Reviewed {
    pub accepted: u32,
    pub skipped: u32,
}

pub(crate) async fn read(
    conn: &mut SqliteConnection,
    ids: &[i64],
) -> Result<Vec<Candidate>, sqlx::Error> {
    let ids = Json(ids);
    Ok(sqlx::query!(
        r#"SELECT id, crawl_id, review, mentor_id,
            name, aliases AS "aliases: Json<Vec<String>>",
            emails AS "emails: Json<Vec<String>>",
            affiliations AS "affiliations: Json<Vec<mentor::Affiliation>>",
            homepages AS "homepages: Json<Vec<String>>",
            research, papers AS "papers: Json<Vec<String>>",
            source_url, note, enrich AS "enrich: crate::enrich::EnrichState",
            enrich_changes AS "changes: Json<Vec<crate::enrich::EnrichChange>>",
            enrich_error
        FROM candidate
        WHERE id IN (SELECT value FROM json_each(?))
        ORDER BY id"#,
        ids
    )
    .fetch_all(conn)
    .await?
    .into_iter()
    .map(|r| Candidate {
        id: r.id,
        crawl_id: r.crawl_id,
        review: r.review,
        mentor_id: r.mentor_id,
        fields: MentorFields {
            name: r.name,
            aliases: r.aliases.map(|Json(v)| v).unwrap_or_default(),
            emails: r.emails.map(|Json(v)| v).unwrap_or_default(),
            affiliations: r.affiliations.map(|Json(v)| v).unwrap_or_default(),
            homepages: r.homepages.map(|Json(v)| v).unwrap_or_default(),
            research: r.research,
        },
        papers: r.papers.map(|Json(v)| v).unwrap_or_default(),
        source_url: r.source_url,
        note: r.note,
        enrichment: crate::enrich::Enrichment {
            enrich: r.enrich,
            enrich_changes: r.changes.map(|Json(v)| v).unwrap_or_default(),
            enrich_error: r.enrich_error,
        },
    })
    .collect())
}
async fn rows(
    conn: &mut SqliteConnection,
    candidates: &[Candidate],
    ids: &[i64],
) -> Result<Vec<Row>, sqlx::Error> {
    let related = sqlx::query!(
        r#"SELECT c.id, p.mentor_id
        FROM candidate c
        JOIN candidate p ON p.crawl_id = c.crawl_id AND p.mentor_id IS NOT NULL
        WHERE c.id IN (SELECT value FROM json_each(?)) AND (EXISTS (
            SELECT 1
            FROM json_each(c.emails) a
            JOIN json_each(p.emails) b ON a.value = b.value COLLATE NOCASE
        ) OR EXISTS (
            SELECT 1
            FROM candidate_profile a
            JOIN candidate_profile b ON a.crawl_id = b.crawl_id
            AND a.url = b.url
            WHERE a.candidate_id = c.id AND b.candidate_id = p.id
            AND a.mismatched_at IS NULL AND b.mismatched_at IS NULL
        ))"#,
        Json(ids)
    )
    .fetch_all(conn)
    .await?;
    Ok(candidates
        .iter()
        .filter(|c| c.review == "pending")
        .map(|c| Row {
            targets: c
                .mentor_id
                .into_iter()
                .chain(
                    related
                        .iter()
                        .filter(|p| p.id == c.id)
                        .filter_map(|p| p.mentor_id),
                )
                .collect(),
            number: (ids
                .binary_search(&c.id)
                .unwrap_or_else(|_| unreachable!("candidate comes from these ids"))
                + 1) as u32,
            profile: Profile {
                id: 0,
                fields: c.fields.clone(),
                papers: c.papers.clone(),
                source_url: c.source_url.clone(),
                note: None,
                tags: vec![],
                trashed: false,
            },
        })
        .collect())
}
pub async fn detail(db: &SqlitePool, id: i64) -> Result<Option<CandidateDetail>, sqlx::Error> {
    let mut tx = db.begin().await?;
    let Some(candidate) = read(&mut tx, &[id]).await?.pop() else {
        return Ok(None);
    };
    let evidence = sqlx::query_scalar!(
        r#"SELECT evidence AS "evidence: Json<Vec<Evidence>>" FROM candidate WHERE id=?"#,
        id
    )
    .fetch_one(&mut *tx)
    .await?
    .0;
    Ok(Some(CandidateDetail {
        candidate,
        evidence,
    }))
}

use mentor::intake::Preview;

pub struct ReviewPlan {
    pub preview: Preview,
    pub changes: Vec<intake::Change>,
    ids: Vec<i64>,
    pending: u32,
}

pub async fn plan(
    conn: &mut SqliteConnection,
    input: &ReviewInput,
) -> Result<ReviewPlan, sqlx::Error> {
    let mut ids = input.ids.clone();
    ids.sort_unstable();
    ids.dedup();
    let candidates = read(conn, &ids).await?;
    let rows = rows(conn, &candidates, &ids).await?;
    let (preview, changes) = intake::calculate(conn, rows, &input.decisions).await?;
    Ok(ReviewPlan {
        preview,
        changes,
        ids,
        pending: candidates.iter().filter(|c| c.review == "pending").count() as u32,
    })
}

pub async fn preview(db: &SqlitePool, input: &ReviewInput) -> Result<Preview, sqlx::Error> {
    let mut tx = db.begin().await?;
    Ok(plan(&mut tx, input).await?.preview)
}
pub async fn accept(
    db: &SqlitePool,
    input: &ReviewInput,
    at: i64,
) -> Result<Reviewed, sqlx::Error> {
    let mut tx = db.begin_with("BEGIN IMMEDIATE").await?;
    let plan = plan(&mut tx, input).await?;
    let (_, accepted) = intake::apply(&mut tx, plan.changes, at).await?;
    for (row, id) in &accepted {
        let candidate = plan.ids[(*row - 1) as usize];
        sqlx::query!(
            r#"UPDATE candidate
            SET review = 'accepted', mentor_id = ?
            WHERE id = ? AND review = 'pending'"#,
            id,
            candidate
        )
        .execute(&mut *tx)
        .await?;
    }
    crate::enrich::retire(&mut tx).await?;
    tx.commit().await?;
    Ok(Reviewed {
        accepted: accepted.len() as u32,
        skipped: plan.pending - accepted.len() as u32,
    })
}
pub async fn reject(db: &SqlitePool, ids: &[i64]) -> Result<u64, sqlx::Error> {
    let mut tx = db.begin_with("BEGIN IMMEDIATE").await?;
    let n = sqlx::query!(
        r#"UPDATE candidate
        SET review = 'rejected'
        WHERE review = 'pending' AND id IN (SELECT value FROM json_each(?))"#,
        Json(ids)
    )
    .execute(&mut *tx)
    .await?
    .rows_affected();
    crate::enrich::retire(&mut tx).await?;
    tx.commit().await?;
    Ok(n)
}
#[derive(Debug, Deserialize)]
struct Classified {
    id: i64,
    category: CandidateFilter,
}
pub async fn list(db: &SqlitePool, q: &CandidateQuery) -> Result<CandidatePage, sqlx::Error> {
    let mut tx = db.begin().await?;
    let missing = q.filter == CandidateFilter::MissingEmail;
    let category = match q.filter {
        CandidateFilter::New => Some("new"),
        CandidateFilter::Supplement => Some("supplement"),
        CandidateFilter::Maybe => Some("maybe"),
        _ => None,
    };
    let offset = i64::from(q.page.saturating_sub(1)) * 50;
    let selection = sqlx::query!(
        r#"WITH classified AS MATERIALIZED(
            SELECT c.id, CASE WHEN c.mentor_id IS NOT NULL
            OR EXISTS (
                SELECT 1
                FROM json_each(c.emails) e
                JOIN mentor_email m ON m.address = e.value
            ) THEN 'supplement' WHEN EXISTS (
                SELECT 1
                FROM json_each(c.match_names) n
                JOIN mentor_name mn ON mn.name = n.value
                WHERE (coalesce(json_array_length(c.emails), 0) = 0
                OR NOT EXISTS (SELECT 1 FROM mentor_email WHERE mentor_id =
                    mn.mentor_id)) AND (json_array_length(c.match_schools) = 0
                OR EXISTS (
                    SELECT 1
                    FROM mentor_affiliation a
                    WHERE a.mentor_id = mn.mentor_id AND a.university_match IN (SELECT
                        value FROM json_each(c.match_schools))
                ))
            ) THEN 'maybe' ELSE 'new' END AS category
            FROM candidate c
            WHERE (?1 IS NULL OR c.crawl_id = ?1) AND (c.review != 'pending') = ?2
            AND (NOT ?4 OR coalesce(json_array_length(c.emails), 0) = 0)
        ), filtered AS MATERIALIZED(
            SELECT id, category
            FROM classified
            WHERE ?3 IS NULL OR category = ?3
        )
        SELECT(
            SELECT json_group_array(json_object('id', id, 'category', category))
            FROM (
                SELECT id, category
                FROM filtered
                ORDER BY id
                LIMIT 50
                OFFSET ?5
            )
        ) AS "ids!: Json<Vec<Classified>>", count(*) AS "total!: i64"
        FROM filtered"#,
        q.crawl_id,
        q.processed,
        category,
        missing,
        offset
    )
    .fetch_one(&mut *tx)
    .await?;
    let selected = selection.ids.0;
    let ids: Vec<_> = selected.iter().map(|c| c.id).collect();
    let candidates = read(&mut tx, &ids).await?;
    let rows = rows(&mut tx, &candidates, &ids).await?;
    let preview = intake::calculate(&mut tx, rows, &Decisions::default())
        .await?
        .0;
    let mut matching = Vec::new();
    for (c, classification) in candidates.into_iter().zip(selected) {
        let row = (ids
            .binary_search(&c.id)
            .unwrap_or_else(|_| unreachable!("selected candidate"))
            + 1) as u32;
        let issue = preview
            .errors
            .iter()
            .find(|e| e.row == row)
            .map(|e| e.reason.clone());
        matching.push(CandidateRow {
            id: c.id,
            crawl_id: c.crawl_id,
            name: c.fields.name,
            emails: c.fields.emails,
            affiliation: c.fields.affiliations.into_iter().next(),
            research: c.fields.research.map(|s| s.chars().take(100).collect()),
            source_url: c.source_url,
            enrichment: c.enrichment,
            review: c.review,
            mentor_id: c.mentor_id,
            category: classification.category,
            issue,
        });
    }
    Ok(CandidatePage {
        candidates: matching,
        total: selection.total,
    })
}

pub(crate) enum Source {
    List {
        id: i64,
    },
    /// `own` says the page proved to be the person's own, so where it ended up identifies them;
    /// `target` is whom a list's profile page was read for, which it adds to even when its link
    /// proved not to be theirs before.
    Profile {
        id: i64,
        own: bool,
        target: Option<i64>,
    },
    Agent {
        note: Option<String>,
    },
}

/// Returns whether this person added a candidate rather than merging into existing candidates.
pub(crate) async fn save(
    conn: &mut SqliteConnection,
    crawl: i64,
    source: Source,
    url: &str,
    person: Person,
) -> Result<bool, sqlx::Error> {
    let f = &person.fields;
    let profile = person.profile.as_ref().map(crate::PageUrl::as_str);
    let (list_page, profile_page, is_profile, reached, note) = match &source {
        Source::List { id } => (Some(*id), None, false, None, None),
        Source::Profile { id, own, .. } => (None, Some(*id), true, own.then_some(url), None),
        Source::Agent { note } => (None, None, profile == Some(url), None, note.as_deref()),
    };
    let identities: Vec<_> = profile.into_iter().chain(reached).collect();
    let (target, proved) = match &source {
        Source::Profile { own, target, .. } => (*target, *own),
        _ => (None, false),
    };
    let names: Vec<_> = std::iter::once(&f.name)
        .chain(&f.aliases)
        .map(|name| intake::comparable(name))
        .collect();
    let ids = sqlx::query_scalar!(
        r#"SELECT id
        FROM candidate
        WHERE crawl_id = ?1 AND (id = ?5 OR id IN (
            SELECT candidate_id
            FROM candidate_profile
            WHERE crawl_id = ?1 AND url IN (SELECT value FROM json_each(?2))
            AND mismatched_at IS NULL
        ) OR (EXISTS (
            SELECT 1
            FROM json_each(match_names) a
            JOIN json_each(?3) b ON a.value = b.value
        ) AND EXISTS (
            SELECT 1
            FROM json_each(emails) a
            JOIN json_each(?4) b ON a.value = b.value COLLATE NOCASE
        )))
        ORDER BY id"#,
        crawl,
        Json(&identities),
        Json(&names),
        Json(&f.emails),
        target
    )
    .fetch_all(&mut *conn)
    .await?;
    let mut old = read(conn, &ids).await?;
    old.sort_by_key(|c| (c.mentor_id.is_none(), c.id));
    let targets: std::collections::BTreeSet<_> = old.iter().filter_map(|c| c.mentor_id).collect();
    if targets.len() > 1 {
        let primary = match target {
            Some(id) => Some(id),
            None => {
                sqlx::query_scalar!(
                    r#"SELECT candidate_id AS "candidate_id!: i64"
                    FROM candidate_profile
                    WHERE crawl_id = ?1 AND url IN (SELECT value FROM json_each(?2))
                    AND mismatched_at IS NULL
                    ORDER BY url IS ?3 DESC, candidate_id
                    LIMIT 1"#,
                    crawl,
                    Json(&identities),
                    profile
                )
                .fetch_optional(&mut *conn)
                .await?
            }
        };
        let keep = primary.or_else(|| old.first().map(|c| c.id));
        old.retain(|c| Some(c.id) == keep);
    }
    let ids: Vec<_> = old.iter().map(|c| c.id).collect();
    if proved {
        clear_mismatch(conn, &ids, &identities).await?;
    }
    if ids.len() > 1 {
        crate::enrich::stop(conn, &ids).await?;
    }
    let added = old.is_empty();
    if added
        && !matches!(source, Source::Agent { .. })
        && sqlx::query_scalar!("SELECT count(*) FROM candidate WHERE crawl_id = ?", crawl)
            .fetch_one(&mut *conn)
            .await?
            >= queue::PERSON_LIMIT
    {
        queue::limit(conn, crawl, "已达到 1000 位导师上限").await?;
        return Ok(false);
    }
    let evidence = Evidence {
        url: url.into(),
        name: f.name.clone(),
        emails: f.emails.clone(),
        research: f.research.clone(),
    };
    let mut sources: Vec<Evidence> = sqlx::query_scalar!(
        r#"SELECT evidence AS "evidence: Json<Vec<Evidence>>"
        FROM candidate
        WHERE id IN (SELECT value FROM json_each(?))
        ORDER BY id"#,
        Json(&ids)
    )
    .fetch_all(&mut *conn)
    .await?
    .into_iter()
    .flat_map(|Json(v)| v)
    .collect();
    if !sources.contains(&evidence) {
        sources.push(evidence);
    }
    let profiles = sqlx::query_scalar!(
        r#"SELECT profile_fields AS "profile_fields: Json<MentorFields>"
        FROM candidate
        WHERE id IN (SELECT value FROM json_each(?))
        ORDER BY id"#,
        Json(&ids)
    )
    .fetch_all(&mut *conn)
    .await?;
    let mut profile_fields: Option<MentorFields> = None;
    for incoming in profiles
        .iter()
        .flatten()
        .map(|Json(f)| f)
        .chain(is_profile.then_some(f))
    {
        if let Some(fields) = &mut profile_fields {
            intake::add_candidate(fields, &mut vec![], incoming, &[]);
        } else {
            profile_fields = Some(incoming.clone());
        }
    }
    let mut fields = profile_fields
        .clone()
        .or_else(|| old.iter().min_by_key(|c| c.id).map(|c| c.fields.clone()))
        .unwrap_or_else(|| f.clone());
    let mut papers = vec![];
    for (incoming, extra) in old
        .iter()
        .map(|c| (&c.fields, &c.papers))
        .chain(std::iter::once((f, &person.papers)))
    {
        intake::add_candidate(&mut fields, &mut papers, incoming, extra);
    }
    // A link whose page proved not to be one of theirs is no homepage or profile of theirs.
    let mismatched = sqlx::query_scalar!(
        r#"SELECT url AS "url!: String"
        FROM candidate_profile
        WHERE candidate_id IN (SELECT value FROM json_each(?)) AND mismatched_at IS NOT NULL"#,
        Json(&ids)
    )
    .fetch_all(&mut *conn)
    .await?;
    fields.homepages.retain(|url| !mismatched.contains(url));
    let names = Json(
        std::iter::once(&fields.name)
            .chain(&fields.aliases)
            .map(|n| intake::comparable(n))
            .collect::<Vec<_>>(),
    );
    let schools = Json(
        fields
            .affiliations
            .iter()
            .filter_map(|a| a.university.as_deref())
            .map(intake::comparable)
            .collect::<Vec<_>>(),
    );
    let profile_fields = profile_fields.map(Json);
    let known_profiles = sqlx::query!(
        r#"SELECT url AS "url!: String", mismatched_at
        FROM candidate_profile
        WHERE candidate_id IN (SELECT value FROM json_each(?))
        ORDER BY candidate_id"#,
        Json(&ids)
    )
    .fetch_all(&mut *conn)
    .await?;
    let id = if let Some(first) = old.first() {
        let id = first.id;
        let review = if matches!(first.review.as_str(), "accepted" | "merged")
            && (fields != first.fields || papers != first.papers)
        {
            "pending"
        } else {
            &first.review
        };
        let duplicates = Json(old.iter().skip(1).map(|c| c.id).collect::<Vec<_>>());
        // A page being read for a duplicate counts for whom it merges into.
        sqlx::query!(
            r#"UPDATE crawl_page
            SET target_id = ?
            WHERE target_id IN (SELECT value FROM json_each(?))"#,
            id,
            duplicates
        )
        .execute(&mut *conn)
        .await?;
        sqlx::query!(
            "DELETE FROM candidate WHERE id IN (SELECT value FROM json_each(?))",
            duplicates
        )
        .execute(&mut *conn)
        .await?;
        sqlx::query!(
            r#"UPDATE candidate
            SET review = ?, name = ?, aliases = ?, emails = ?, affiliations = ?,
                homepages = ?, research = ?, papers = ?, evidence = ?,
                list_page_id = coalesce(list_page_id, ?),
                profile_page_id = coalesce(?, profile_page_id), profile_fields = ?,
                match_names = ?, match_schools = ?, note = coalesce(?, note)
            WHERE id = ?"#,
            review,
            fields.name,
            Json(&fields.aliases),
            Json(&fields.emails),
            Json(&fields.affiliations),
            Json(&fields.homepages),
            fields.research,
            Json(&papers),
            Json(&sources),
            list_page,
            profile_page,
            profile_fields,
            names,
            schools,
            note.or_else(|| old.iter().find_map(|c| c.note.as_deref())),
            id
        )
        .execute(&mut *conn)
        .await?;
        id
    } else {
        sqlx::query_scalar!(
            r#"INSERT INTO candidate (crawl_id, list_page_id, profile_page_id,
                review, enrich, name, aliases, emails, affiliations,
                homepages, research, papers, source_url, evidence, profile_fields,
                match_names, match_schools, note)
            VALUES (?, ?, ?, 'pending', 'none', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            RETURNING id"#,
            crawl,
            list_page,
            profile_page,
            fields.name,
            Json(&fields.aliases),
            Json(&fields.emails),
            Json(&fields.affiliations),
            Json(&fields.homepages),
            fields.research,
            Json(&papers),
            url,
            Json(&sources),
            profile_fields,
            names,
            schools,
            note
        )
        .fetch_one(&mut *conn)
        .await?
    };
    // A link whose page proved not to be one of the merged candidates' stays so, as of the latest
    // time, and a list giving it again changes nothing.
    for (url, mismatched_at) in known_profiles
        .iter()
        .map(|p| (p.url.as_str(), p.mismatched_at))
        .chain(identities.into_iter().map(|url| (url, None)))
    {
        sqlx::query!(
            r#"INSERT INTO candidate_profile(candidate_id, crawl_id, url, mismatched_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT (candidate_id, url)
            DO UPDATE SET mismatched_at = coalesce(
                max(mismatched_at, excluded.mismatched_at),
                mismatched_at,
                excluded.mismatched_at
            )"#,
            id,
            crawl,
            url,
            mismatched_at
        )
        .execute(&mut *conn)
        .await?;
    }
    let canonical_profile = choose_profile(conn, id, profile).await?;
    sqlx::query!(
        r#"DELETE
        FROM crawl_page
        WHERE crawl_id = ?1 AND kind = 'profile' AND work_state = 'queued'
        AND fetched_at IS NULL AND url IN (SELECT url FROM candidate_profile WHERE
            candidate_id = ?2 AND mismatched_at IS NULL) AND url IS NOT ?3"#,
        crawl,
        id,
        canonical_profile
    )
    .execute(&mut *conn)
    .await?;
    // Profile work is scheduled only once, directly from the validated list link.
    if let Some(page) = list_page
        && let Some(link) = person.profile
        && canonical_profile.as_deref() == Some(link.as_str())
    {
        let scope = sqlx::query_scalar!(
            r#"SELECT scope_url AS "scope!: PageUrl" FROM crawl_page WHERE id = ?"#,
            page
        )
        .fetch_one(&mut *conn)
        .await?;
        queue::enqueue(conn, crawl, &link, &scope, 0, PageKind::Profile).await?;
        // Read last for someone it proved not to be, the page is read again for this one.
        sqlx::query!(
            r#"UPDATE crawl_page
            SET work_state = 'queued', claim = claim + 1
            WHERE crawl_id = ?1 AND url = ?2 AND steps = '[]' AND candidate_id IS NULL
            AND work_state = 'done' AND target_id IS NOT ?3 AND EXISTS (
                SELECT 1
                FROM candidate_profile
                WHERE candidate_id = crawl_page.target_id AND url = ?2
                AND mismatched_at IS NOT NULL
            )"#,
            crawl,
            link,
            id
        )
        .execute(&mut *conn)
        .await?;
    }
    Ok(added)
}

/// Pages at `urls`, read for the candidate, proved not to be theirs: the links no longer identify
/// them or stand as their profile or homepages. Only the candidate changes, not the mentor it was
/// taken into.
pub(crate) async fn mismatched(
    conn: &mut SqliteConnection,
    candidate: i64,
    urls: &[&str],
    at: i64,
) -> Result<(), sqlx::Error> {
    sqlx::query!(
        r#"UPDATE candidate_profile
        SET mismatched_at = ?
        WHERE candidate_id = ? AND url IN (SELECT value FROM json_each(?))"#,
        at,
        candidate,
        Json(urls)
    )
    .execute(&mut *conn)
    .await?;
    let homepages = sqlx::query_scalar!(
        r#"SELECT homepages AS "homepages: Json<Vec<String>>" FROM candidate WHERE id = ?"#,
        candidate
    )
    .fetch_one(&mut *conn)
    .await?
    .map(|Json(homepages)| {
        homepages
            .into_iter()
            .filter(|h| !urls.contains(&h.as_str()))
            .collect::<Vec<_>>()
    });
    sqlx::query!(
        "UPDATE candidate SET homepages = ? WHERE id = ?",
        homepages.map(Json),
        candidate
    )
    .execute(&mut *conn)
    .await?;
    choose_profile(conn, candidate, None).await?;
    Ok(())
}

/// Pages at `urls`, read for the candidate, proved to be theirs: the links identify them again,
/// and the first stands as their profile if they have none.
pub(crate) async fn matched(
    conn: &mut SqliteConnection,
    candidate: i64,
    urls: &[&str],
) -> Result<(), sqlx::Error> {
    clear_mismatch(conn, &[candidate], urls).await?;
    choose_profile(conn, candidate, urls.first().copied()).await?;
    Ok(())
}

async fn clear_mismatch(
    conn: &mut SqliteConnection,
    candidates: &[i64],
    urls: &[&str],
) -> Result<(), sqlx::Error> {
    sqlx::query!(
        r#"UPDATE candidate_profile
        SET mismatched_at = NULL
        WHERE candidate_id IN (SELECT value FROM json_each(?))
        AND url IN (SELECT value FROM json_each(?))"#,
        Json(candidates),
        Json(urls)
    )
    .execute(conn)
    .await?;
    Ok(())
}

/// Keeps the candidate's profile unless its page proved to be someone else's; otherwise one of
/// their links no page proved so and no other candidate has as profile, `preferred` first, or none.
async fn choose_profile(
    conn: &mut SqliteConnection,
    candidate: i64,
    preferred: Option<&str>,
) -> Result<Option<String>, sqlx::Error> {
    sqlx::query_scalar!(
        r#"UPDATE candidate
        SET profile_url = coalesce(CASE WHEN NOT EXISTS (
            SELECT 1
            FROM candidate_profile
            WHERE candidate_id = candidate.id AND url = candidate.profile_url
            AND mismatched_at IS NOT NULL
        ) THEN profile_url END, (
            SELECT p.url
            FROM candidate_profile p
            WHERE p.candidate_id = candidate.id AND p.mismatched_at IS NULL
            AND NOT EXISTS (
                SELECT 1
                FROM candidate o
                WHERE o.crawl_id = candidate.crawl_id AND o.profile_url = p.url
            )
            ORDER BY p.url IS ?2 DESC, p.url
            LIMIT 1
        ))
        WHERE id = ?1
        RETURNING profile_url"#,
        candidate,
        preferred
    )
    .fetch_one(conn)
    .await
}
