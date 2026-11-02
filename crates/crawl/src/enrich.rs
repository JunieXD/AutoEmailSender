//! Candidate enrichment uses the crawl page queue without candidate identity merging.
use crate::{PageUrl, extract::Person, queue, review, store::PageKind};
use mentor::{Affiliation, MentorFields, intake::comparable};
use serde::{Deserialize, Serialize};
use serde_json::Value;
use sqlx::{SqliteConnection, SqlitePool, types::Json};
use ts_rs::TS;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, TS, sqlx::Type)]
#[serde(rename_all = "snake_case")]
#[sqlx(rename_all = "snake_case")]
pub enum EnrichState {
    None,
    Queued,
    Running,
    Done,
}
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, TS)]
#[serde(rename_all = "snake_case")]
pub enum EnrichField {
    Email,
    Alias,
    Homepage,
    Paper,
    Research,
    Affiliation,
    University,
    School,
    Department,
    Title,
}
#[derive(Debug, Clone, Serialize, Deserialize, TS)]
pub struct EnrichChange {
    pub field: EnrichField,
    #[ts(type = "unknown")]
    pub value: Value,
}
#[derive(Debug, Serialize, TS)]
#[serde(rename_all = "camelCase")]
pub struct Enrichment {
    pub enrich: EnrichState,
    pub enrich_changes: Vec<EnrichChange>,
    pub enrich_error: Option<String>,
}
#[derive(Debug, Serialize, TS, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum EnrichSkipReason {
    MissingProfile,
    DeletedCrawl,
    Complete,
    Active,
    Reviewed,
}
#[derive(Debug, Serialize, TS)]
pub struct EnrichSkip {
    pub reason: EnrichSkipReason,
    pub ids: Vec<i64>,
}
#[derive(Debug, Serialize, TS)]
pub struct EnrichPlan {
    pub ids: Vec<i64>,
    pub skipped: Vec<EnrichSkip>,
}
async fn plan(conn: &mut SqliteConnection, ids: &[i64]) -> Result<EnrichPlan, sqlx::Error> {
    let rows = sqlx::query!(
        r#"SELECT id AS "id!: i64",crawl_id,profile_url,review,enrich AS "enrich: EnrichState",
        coalesce(json_array_length(emails),0) AS "emails!: i64",research
        FROM candidate WHERE id IN (SELECT value FROM json_each(?)) ORDER BY id"#,
        Json(ids)
    )
    .fetch_all(conn)
    .await?;
    let mut result = EnrichPlan {
        ids: vec![],
        skipped: vec![],
    };
    for id in ids
        .iter()
        .copied()
        .collect::<std::collections::BTreeSet<_>>()
    {
        let row = rows.iter().find(|r| r.id == id);
        let reason = match row {
            None => Some(EnrichSkipReason::Reviewed),
            Some(r) if r.review != "pending" => Some(EnrichSkipReason::Reviewed),
            Some(r) if matches!(r.enrich, EnrichState::Queued | EnrichState::Running) => {
                Some(EnrichSkipReason::Active)
            }
            Some(r) if r.crawl_id.is_none() => Some(EnrichSkipReason::DeletedCrawl),
            Some(r) if r.profile_url.is_none() => Some(EnrichSkipReason::MissingProfile),
            Some(r)
                if r.emails > 0 && r.research.as_deref().is_some_and(|s| !s.trim().is_empty()) =>
            {
                Some(EnrichSkipReason::Complete)
            }
            Some(_) => None,
        };
        if let Some(reason) = reason {
            if let Some(group) = result.skipped.iter_mut().find(|g| g.reason == reason) {
                group.ids.push(id);
            } else {
                result.skipped.push(EnrichSkip {
                    reason,
                    ids: vec![id],
                });
            }
        } else {
            result.ids.push(id);
        }
    }
    Ok(result)
}
pub async fn preview(db: &SqlitePool, ids: &[i64]) -> Result<EnrichPlan, sqlx::Error> {
    plan(&mut *db.acquire().await?, ids).await
}
/// Queues the candidates' enrichment, also saying which ended crawls it started again.
pub async fn start(
    db: &SqlitePool,
    ids: &[i64],
    at: i64,
) -> Result<(EnrichPlan, Vec<i64>), sqlx::Error> {
    let mut tx = db.begin_with("BEGIN IMMEDIATE").await?;
    let result = plan(&mut tx, ids).await?;
    let mut crawls = std::collections::BTreeMap::<i64, usize>::new();
    let mut reopened = Vec::new();
    for id in &result.ids {
        let r = sqlx::query!(
            r#"SELECT crawl_id AS "crawl!: i64", profile_url AS "url!: PageUrl"
            FROM candidate
            WHERE id = ?"#,
            id
        )
        .fetch_one(&mut *tx)
        .await?;
        *crawls.entry(r.crawl).or_default() += 1;
        sqlx::query!("DELETE FROM crawl_page WHERE candidate_id=?", id)
            .execute(&mut *tx)
            .await?;
        sqlx::query!(
            "UPDATE candidate SET enrich='queued',enrich_changes='[]',enrich_error=NULL WHERE id=?",
            id
        )
        .execute(&mut *tx)
        .await?;
        enqueue(
            &mut tx,
            r.crawl,
            *id,
            &r.url,
            &r.url,
            PageKind::EnrichProfile,
        )
        .await?;
        let resumed = sqlx::query!(
            r#"UPDATE crawl
            SET state = 'running', finished_at = NULL, error = NULL
            WHERE id = ? AND state NOT IN ('running', 'paused')"#,
            r.crawl
        )
        .execute(&mut *tx)
        .await?
        .rows_affected();
        if resumed > 0 {
            queue::open_run(&mut tx, r.crawl, at).await?;
            reopened.push(r.crawl);
        }
    }
    tx.commit().await?;
    for (id, candidates) in crawls {
        crate::span(id).in_scope(|| tracing::info!(candidates, "enrichment queued"));
    }
    Ok((result, reopened))
}
pub(crate) async fn enqueue(
    conn: &mut SqliteConnection,
    crawl: i64,
    candidate: i64,
    url: &PageUrl,
    scope: &PageUrl,
    kind: PageKind,
) -> Result<(), sqlx::Error> {
    sqlx::query!(
        r#"INSERT OR IGNORE INTO crawl_page (crawl_id, candidate_id, url, scope_url, kind,
            mode, status, error, work_state)
        VALUES (?, ?, ?, ?, ?, 'http', 'failed', '尚未读取', 'queued')"#,
        crawl,
        candidate,
        url,
        scope,
        kind
    )
    .execute(conn)
    .await?;
    Ok(())
}
pub(crate) fn matches(
    fields: &MentorFields,
    incoming: &MentorFields,
    material: &crate::Material,
) -> bool {
    let text: String = comparable(&format!(
        "{}\n{}",
        material.title.as_deref().unwrap_or_default(),
        material.text()
    ))
    .chars()
    .filter(|c| !c.is_whitespace())
    .collect();
    std::iter::once(&fields.name)
        .chain(&fields.aliases)
        .any(|a| {
            std::iter::once(&incoming.name)
                .chain(&incoming.aliases)
                .any(|b| {
                    comparable(a) == comparable(b)
                        && text.contains(
                            &comparable(b)
                                .chars()
                                .filter(|c| !c.is_whitespace())
                                .collect::<String>(),
                        )
                })
        })
}
fn compatible(a: &Affiliation, b: &Affiliation, single: bool) -> bool {
    let pairs = [
        (&a.university, &b.university),
        (&a.school, &b.school),
        (&a.department, &b.department),
    ];
    let overlap = pairs.iter().any(|(a, b)| a.is_some() && b.is_some());
    (single || overlap)
        && pairs.iter().all(|(a, b)| {
            a.as_ref()
                .zip(b.as_ref())
                .is_none_or(|(a, b)| comparable(a) == comparable(b))
        })
}

fn change(
    changes: &mut Vec<EnrichChange>,
    field: EnrichField,
    value: impl Serialize,
) -> Result<(), sqlx::Error> {
    changes.push(EnrichChange {
        field,
        value: serde_json::to_value(value).map_err(|e| sqlx::Error::Encode(e.into()))?,
    });
    Ok(())
}
pub(crate) async fn apply(
    conn: &mut SqliteConnection,
    id: i64,
    url: &str,
    person: &Person,
) -> Result<(), sqlx::Error> {
    let mut old = review::read(conn, &[id])
        .await?
        .pop()
        .ok_or(sqlx::Error::RowNotFound)?;
    let f = &mut old.fields;
    let incoming = &person.fields;
    let changes = &mut old.enrichment.enrich_changes;
    macro_rules! fill_list {
        ($target:expr,$incoming:expr,$label:expr) => {
            if $target.is_empty() && !$incoming.is_empty() {
                $target = $incoming.clone();
                change(changes, $label, &$target)?;
            }
        };
    }
    fill_list!(f.emails, incoming.emails, EnrichField::Email);
    fill_list!(f.aliases, incoming.aliases, EnrichField::Alias);
    fill_list!(f.homepages, incoming.homepages, EnrichField::Homepage);
    fill_list!(old.papers, person.papers, EnrichField::Paper);
    if f.research.as_deref().is_none_or(|s| s.trim().is_empty()) && incoming.research.is_some() {
        f.research = incoming.research.clone();
        change(changes, EnrichField::Research, &f.research)?;
    }
    if f.affiliations.is_empty() {
        fill_list!(
            f.affiliations,
            incoming.affiliations,
            EnrichField::Affiliation
        );
    } else {
        let single = f.affiliations.len() == 1 && incoming.affiliations.len() == 1;
        let pairs: Vec<Vec<usize>> = f
            .affiliations
            .iter()
            .map(|a| {
                incoming
                    .affiliations
                    .iter()
                    .enumerate()
                    .filter(|(_, b)| compatible(a, b, single))
                    .map(|(index, _)| index)
                    .collect()
            })
            .collect();
        for (index, a) in f.affiliations.iter_mut().enumerate() {
            if let [matched] = pairs[index].as_slice()
                && pairs.iter().filter(|p| p.contains(matched)).count() == 1
            {
                let b = &incoming.affiliations[*matched];
                for (target, value, label) in [
                    (&mut a.university, &b.university, EnrichField::University),
                    (&mut a.school, &b.school, EnrichField::School),
                    (&mut a.department, &b.department, EnrichField::Department),
                    (&mut a.title, &b.title, EnrichField::Title),
                ] {
                    if target.is_none() && value.is_some() {
                        *target = value.clone();
                        change(changes, label, &target)?;
                    }
                }
            }
        }
    }
    let names = Json(
        std::iter::once(&f.name)
            .chain(&f.aliases)
            .map(|n| comparable(n))
            .collect::<Vec<_>>(),
    );
    let schools = Json(
        f.affiliations
            .iter()
            .filter_map(|a| a.university.as_deref())
            .map(comparable)
            .collect::<Vec<_>>(),
    );
    let Json(mut evidence) = sqlx::query_scalar!(
        r#"SELECT evidence AS "evidence: Json<Vec<review::Evidence>>" FROM candidate WHERE id=?"#,
        id
    )
    .fetch_one(&mut *conn)
    .await?;
    let source = review::Evidence {
        url: url.into(),
        name: incoming.name.clone(),
        emails: incoming.emails.clone(),
        research: incoming.research.clone(),
    };
    if !evidence.contains(&source) {
        evidence.push(source);
    }
    sqlx::query!(
        r#"UPDATE candidate
        SET aliases = ?, emails = ?, homepages = ?, research = ?, affiliations = ?,
            papers = ?, enrich_changes = ?, match_names = ?, match_schools = ?,
            evidence = ?
        WHERE id = ?"#,
        Json(&f.aliases),
        Json(&f.emails),
        Json(&f.homepages),
        f.research,
        Json(&f.affiliations),
        Json(&old.papers),
        Json(changes),
        names,
        schools,
        Json(evidence),
        id
    )
    .execute(conn)
    .await?;
    Ok(())
}
pub(crate) async fn complete(conn: &mut SqliteConnection, id: i64) -> Result<(), sqlx::Error> {
    sqlx::query!(
        r#"UPDATE candidate
        SET enrich = 'done', enrich_error = CASE WHEN json_array_length(enrich_changes)
            > 0 THEN NULL ELSE coalesce((
            SELECT CASE WHEN status = 'needs_browser' THEN '需要浏览器：' || error
            ELSE '页面读取失败：' || error END
            FROM crawl_page
            WHERE candidate_id = ?1 AND status != 'success'
            ORDER BY status = 'needs_browser' DESC, kind = 'enrich_profile' DESC, id
            LIMIT 1
        ), '页面上没有可补充的资料') END
        WHERE id = ?1 AND review = 'pending' AND enrich IN ('queued', 'running')
        AND NOT EXISTS (
            SELECT 1
            FROM crawl_page
            WHERE candidate_id = ?1 AND work_state != 'done'
        )"#,
        id
    )
    .execute(conn)
    .await?;
    Ok(())
}
/// Review invalidates work before another request can send or persist candidate data.
pub(crate) async fn retire(conn: &mut SqliteConnection) -> Result<(), sqlx::Error> {
    let ids = sqlx::query_scalar!(
        "SELECT id FROM candidate WHERE review!='pending' AND enrich IN ('queued','running')"
    )
    .fetch_all(&mut *conn)
    .await?;
    stop(conn, &ids).await
}
pub(crate) async fn stop(conn: &mut SqliteConnection, ids: &[i64]) -> Result<(), sqlx::Error> {
    sqlx::query!(
        r#"DELETE
        FROM crawl_page
        WHERE work_state != 'done' AND candidate_id IN (SELECT value FROM json_each(?))"#,
        Json(ids)
    )
    .execute(&mut *conn)
    .await?;
    sqlx::query!(
        r#"UPDATE candidate
        SET enrich = 'none'
        WHERE id IN (SELECT value FROM json_each(?)) AND enrich IN ('queued',
            'running')"#,
        Json(ids)
    )
    .execute(conn)
    .await?;
    Ok(())
}
