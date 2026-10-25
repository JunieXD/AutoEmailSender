//! Candidate review uses the very same intake calculation and writes as table imports.
use crate::{extract::Person, queue};
use mentor::{
    MentorFields,
    export::Profile,
    import::{Decisions, Preview},
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

async fn read(conn: &mut SqliteConnection, ids: &[i64]) -> Result<Vec<Candidate>, sqlx::Error> {
    let ids = Json(ids);
    Ok(sqlx::query!(r#"SELECT id,crawl_id,review,mentor_id,name,aliases AS "aliases: Json<Vec<String>>",emails AS "emails: Json<Vec<String>>",affiliations AS "affiliations: Json<Vec<mentor::Affiliation>>",homepages AS "homepages: Json<Vec<String>>",research,papers AS "papers: Json<Vec<String>>",source_url FROM candidate WHERE id IN (SELECT value FROM json_each(?)) ORDER BY id"#,ids).fetch_all(conn).await?.into_iter().map(|r|Candidate{id:r.id,crawl_id:r.crawl_id,review:r.review,mentor_id:r.mentor_id,fields:MentorFields{name:r.name,aliases:r.aliases.map(|Json(v)|v).unwrap_or_default(),emails:r.emails.map(|Json(v)|v).unwrap_or_default(),affiliations:r.affiliations.map(|Json(v)|v).unwrap_or_default(),homepages:r.homepages.map(|Json(v)|v).unwrap_or_default(),research:r.research},papers:r.papers.map(|Json(v)|v).unwrap_or_default(),source_url:r.source_url}).collect())
}
fn rows(candidates: &[Candidate], ids: &[i64]) -> Vec<Row> {
    candidates
        .iter()
        .filter(|c| c.review == "pending")
        .map(|c| Row {
            target: c.mentor_id,
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
        .collect()
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

pub async fn preview(db: &SqlitePool, input: &ReviewInput) -> Result<Preview, sqlx::Error> {
    let mut tx = db.begin().await?;
    let mut ids = input.ids.clone();
    ids.sort_unstable();
    ids.dedup();
    let candidates = read(&mut tx, &ids).await?;
    Ok(
        intake::calculate(&mut tx, rows(&candidates, &ids), &input.decisions)
            .await?
            .0,
    )
}
pub async fn accept(
    db: &SqlitePool,
    input: &ReviewInput,
    at: i64,
) -> Result<Reviewed, sqlx::Error> {
    let mut tx = db.begin_with("BEGIN IMMEDIATE").await?;
    let mut ids = input.ids.clone();
    ids.sort_unstable();
    ids.dedup();
    let candidates = read(&mut tx, &ids).await?;
    let (_, changes) =
        intake::calculate(&mut tx, rows(&candidates, &ids), &input.decisions).await?;
    let (_, accepted) = intake::apply(&mut tx, changes, at).await?;
    for (row, id) in &accepted {
        let candidate = ids[(*row - 1) as usize];
        sqlx::query!("UPDATE candidate SET review = 'accepted',mentor_id = ? WHERE id = ? AND review = 'pending'",id,candidate).execute(&mut *tx).await?;
    }
    tx.commit().await?;
    Ok(Reviewed {
        accepted: accepted.len() as u32,
        skipped: candidates.iter().filter(|c| c.review == "pending").count() as u32
            - accepted.len() as u32,
    })
}
pub async fn reject(db: &SqlitePool, ids: &[i64]) -> Result<u64, sqlx::Error> {
    Ok(sqlx::query!("UPDATE candidate SET review = 'rejected' WHERE review = 'pending' AND id IN (SELECT value FROM json_each(?))",Json(ids)).execute(db).await?.rows_affected())
}
pub async fn list(db: &SqlitePool, q: &CandidateQuery) -> Result<CandidatePage, sqlx::Error> {
    let mut tx = db.begin().await?;
    let simple = matches!(
        q.filter,
        CandidateFilter::All | CandidateFilter::MissingEmail
    );
    let missing = q.filter == CandidateFilter::MissingEmail;
    let count=sqlx::query_scalar!("SELECT count(*) FROM candidate WHERE (?1 IS NULL OR crawl_id = ?1) AND (review != 'pending') = ?2 AND (NOT ?3 OR coalesce(json_array_length(emails),0)=0)",q.crawl_id,q.processed,missing).fetch_one(&mut *tx).await?;
    let limit = if simple { 50 } else { -1 };
    let skip = if simple {
        i64::from(q.page.saturating_sub(1)) * 50
    } else {
        0
    };
    let ids=sqlx::query_scalar!("SELECT id FROM candidate WHERE (?1 IS NULL OR crawl_id = ?1) AND (review != 'pending') = ?2 AND (NOT ?3 OR coalesce(json_array_length(emails),0)=0) ORDER BY id LIMIT ?4 OFFSET ?5",q.crawl_id,q.processed,missing,limit,skip).fetch_all(&mut *tx).await?;
    // Only small summaries for the current batch are retained, never all page texts.
    let mut matching = Vec::new();
    let mut total = 0;
    let offset = if simple {
        0
    } else {
        q.page.saturating_sub(1) as usize * 50
    };
    for batch in ids.chunks(100) {
        let candidates = read(&mut tx, batch).await?;
        let preview = intake::calculate(&mut tx, rows(&candidates, batch), &Decisions::default())
            .await?
            .0;
        for (n, c) in candidates.into_iter().enumerate() {
            let row = (n + 1) as u32;
            let issue = preview.errors.iter().find(|e| e.row == row).map(|e| {
                e.reason
                    .replace("这些行的邮箱", "这位候选的邮箱")
                    .replace("或修改表格", "后再接受")
            });
            let category = if preview.maybe_same.iter().any(|m| m.rows.contains(&row)) {
                CandidateFilter::Maybe
            } else {
                let emails = Json(&c.fields.emails);
                if c.mentor_id.is_some() || sqlx::query_scalar!("SELECT EXISTS(SELECT 1 FROM mentor_email WHERE address IN (SELECT value FROM json_each(?))) AS \"found!: bool\"",emails).fetch_one(&mut *tx).await?{CandidateFilter::Supplement}else{CandidateFilter::New}
            };
            if q.filter != CandidateFilter::All
                && q.filter != category
                && !(q.filter == CandidateFilter::MissingEmail && c.fields.emails.is_empty())
            {
                continue;
            }
            if total >= offset && matching.len() < 50 {
                matching.push(CandidateRow {
                    id: c.id,
                    crawl_id: c.crawl_id,
                    name: c.fields.name,
                    emails: c.fields.emails,
                    affiliation: c.fields.affiliations.into_iter().next(),
                    research: c.fields.research.map(|s| s.chars().take(100).collect()),
                    source_url: c.source_url,
                    review: c.review,
                    mentor_id: c.mentor_id,
                    category,
                    issue,
                });
            }
            total += 1;
        }
    }
    Ok(CandidatePage {
        candidates: matching,
        total: if simple { count } else { total as i64 },
    })
}

pub(crate) async fn save(
    conn: &mut SqliteConnection,
    crawl: i64,
    page: i64,
    kind: &str,
    url: &str,
    person: Person,
) -> Result<(), sqlx::Error> {
    let f = &person.fields;
    let profile = person.profile.as_ref().map(crate::PageUrl::as_str);
    let ids=sqlx::query_scalar!(r#"SELECT id FROM candidate WHERE crawl_id = ?1 AND ((?2 IS NOT NULL AND profile_url = ?2) OR (name = ?3 AND EXISTS(SELECT 1 FROM json_each(emails) a JOIN json_each(?4) b ON a.value = b.value COLLATE NOCASE))) ORDER BY id"#,crawl,profile,f.name,Json(&f.emails)).fetch_all(&mut *conn).await?;
    let mut old = read(conn, &ids).await?;
    old.sort_by_key(|c| (c.mentor_id.is_none(), c.id));
    if old.is_empty()
        && sqlx::query_scalar!("SELECT count(*) FROM candidate WHERE crawl_id = ?", crawl)
            .fetch_one(&mut *conn)
            .await?
            >= queue::PERSON_LIMIT
    {
        queue::limit(conn, crawl, "已达到 1000 位导师上限").await?;
        return Ok(());
    }
    let evidence = Evidence {
        url: url.into(),
        name: f.name.clone(),
        emails: f.emails.clone(),
        research: f.research.clone(),
    };
    let mut fields = old
        .first()
        .map(|c| c.fields.clone())
        .unwrap_or_else(|| f.clone());
    let mut papers = old
        .first()
        .map(|c| c.papers.clone())
        .unwrap_or_else(|| person.papers.clone());
    let mut sources: Vec<Evidence> = if ids.is_empty() {
        vec![]
    } else {
        sqlx::query_scalar!(r#"SELECT evidence AS "evidence: Json<Vec<Evidence>>" FROM candidate WHERE id IN (SELECT value FROM json_each(?)) ORDER BY id"#,Json(&ids)).fetch_all(&mut *conn).await?.into_iter().flat_map(|Json(v)|v).collect()
    };
    if !sources.contains(&evidence) {
        sources.push(evidence);
    }
    for (incoming, extra) in old
        .iter()
        .skip(1)
        .map(|c| (&c.fields, &c.papers))
        .chain(std::iter::once((f, &person.papers)))
    {
        mentor::intake::add_candidate(&mut fields, &mut papers, incoming, extra);
    }
    let canonical_profile = if let Some(first) = old.first() {
        let id = first.id;
        let review = if matches!(first.review.as_str(), "accepted" | "merged")
            && (fields != first.fields || papers != first.papers)
        {
            "pending"
        } else {
            &first.review
        };
        for duplicate in old.iter().skip(1) {
            sqlx::query!("DELETE FROM candidate WHERE id = ?", duplicate.id)
                .execute(&mut *conn)
                .await?;
        }
        sqlx::query_scalar!("UPDATE candidate SET review=?,name=?,aliases=?,emails=?,affiliations=?,homepages=?,research=?,papers=?,evidence=?,profile_url=coalesce(profile_url,?),list_page_id=coalesce(list_page_id,?),profile_page_id=coalesce(?,profile_page_id) WHERE id=? RETURNING profile_url",review,fields.name,Json(&fields.aliases),Json(&fields.emails),Json(&fields.affiliations),Json(&fields.homepages),fields.research,Json(&papers),Json(&sources),profile,if kind=="list"{Some(page)}else{None},if kind=="profile"{Some(page)}else{None},id).fetch_one(&mut *conn).await?
    } else {
        sqlx::query_scalar!("INSERT INTO candidate (crawl_id,list_page_id,profile_page_id,profile_url,review,enrich,name,aliases,emails,affiliations,homepages,research,papers,source_url,evidence) VALUES (?,?,?,?,'pending','none',?,?,?,?,?,?,?,?,?) RETURNING profile_url",crawl,if kind=="list"{Some(page)}else{None},if kind=="profile"{Some(page)}else{None},profile,fields.name,Json(&fields.aliases),Json(&fields.emails),Json(&fields.affiliations),Json(&fields.homepages),fields.research,Json(&papers),url,Json(&sources)).fetch_one(&mut *conn).await?
    };
    // Profile work is scheduled only once, directly from the validated list link.
    if kind == "list"
        && let Some(target) = person.profile
        && canonical_profile.as_deref() == Some(target.as_str())
    {
        let scope = sqlx::query_scalar!("SELECT scope_url FROM crawl_page WHERE id = ?", page)
            .fetch_one(&mut *conn)
            .await?
            .ok_or_else(|| sqlx::Error::Protocol("list page scope missing".into()))?;
        let scope =
            crate::PageUrl::parse(&scope).map_err(|e| sqlx::Error::Protocol(e.to_string()))?;
        queue::enqueue(conn, crawl, &target, &scope, 0, "profile").await?;
    }
    Ok(())
}
