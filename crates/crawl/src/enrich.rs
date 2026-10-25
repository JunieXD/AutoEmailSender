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
    Failed,
}
#[derive(Debug, Clone, Serialize, Deserialize, TS)]
pub struct EnrichChange {
    pub field: String,
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
        r#"SELECT id AS "id!: i64",crawl_id,profile_url,review,enrich,
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
            Some(r) if matches!(r.enrich.as_str(), "queued" | "running") => {
                Some(EnrichSkipReason::Active)
            }
            Some(r) if r.crawl_id.is_none() || r.profile_url.is_none() => {
                Some(EnrichSkipReason::MissingProfile)
            }
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
pub async fn start(db: &SqlitePool, ids: &[i64], at: i64) -> Result<EnrichPlan, sqlx::Error> {
    let mut tx = db.begin_with("BEGIN IMMEDIATE").await?;
    let result = plan(&mut tx, ids).await?;
    for id in &result.ids {
        let r = sqlx::query!(r#"SELECT crawl_id AS "crawl!: i64",profile_url AS "url!: PageUrl" FROM candidate WHERE id=?"#,id).fetch_one(&mut *tx).await?;
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
        let resumed = sqlx::query!("UPDATE crawl SET state='running',finished_at=NULL,error=NULL WHERE id=? AND state NOT IN ('running','paused')",r.crawl).execute(&mut *tx).await?.rows_affected();
        if resumed > 0 {
            queue::open_run(&mut tx, r.crawl, at).await?;
        }
    }
    tx.commit().await?;
    Ok(result)
}
pub(crate) async fn enqueue(
    conn: &mut SqliteConnection,
    crawl: i64,
    candidate: i64,
    url: &PageUrl,
    scope: &PageUrl,
    kind: PageKind,
) -> Result<(), sqlx::Error> {
    sqlx::query!("INSERT OR IGNORE INTO crawl_page(crawl_id,candidate_id,url,scope_url,kind,mode,status,error,work_state) VALUES(?,?,?,?,?,'http','failed','尚未读取','queued')",crawl,candidate,url,scope,kind).execute(conn).await?;
    Ok(())
}
pub(crate) fn matches(
    fields: &MentorFields,
    incoming: &MentorFields,
    material: &crate::Material,
) -> bool {
    let text: String = comparable(&material.text())
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
    field: &str,
    value: impl Serialize,
) -> Result<(), sqlx::Error> {
    changes.push(EnrichChange {
        field: field.into(),
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
    let Some(mut old) = review::read(conn, &[id]).await?.pop() else {
        return Ok(());
    };
    if old.review != "pending"
        || !matches!(
            old.enrichment.enrich,
            EnrichState::Queued | EnrichState::Running
        )
    {
        return Ok(());
    }
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
    fill_list!(f.emails, incoming.emails, "邮箱");
    fill_list!(f.aliases, incoming.aliases, "别名");
    fill_list!(f.homepages, incoming.homepages, "主页");
    fill_list!(old.papers, person.papers, "代表论文");
    if f.research.as_deref().is_none_or(|s| s.trim().is_empty()) && incoming.research.is_some() {
        f.research = incoming.research.clone();
        change(changes, "研究方向", &f.research)?;
    }
    if f.affiliations.is_empty() {
        fill_list!(f.affiliations, incoming.affiliations, "单位");
    } else {
        let single = f.affiliations.len() == 1 && incoming.affiliations.len() == 1;
        for a in &mut f.affiliations {
            if let Some(b) = incoming
                .affiliations
                .iter()
                .find(|b| compatible(a, b, single))
            {
                for (target, value, label) in [
                    (&mut a.university, &b.university, "学校"),
                    (&mut a.school, &b.school, "学院"),
                    (&mut a.department, &b.department, "系所"),
                    (&mut a.title, &b.title, "职称"),
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
    sqlx::query!("UPDATE candidate SET aliases=?,emails=?,homepages=?,research=?,affiliations=?,papers=?,enrich_changes=?,match_names=?,match_schools=?,evidence=? WHERE id=?",Json(&f.aliases),Json(&f.emails),Json(&f.homepages),f.research,Json(&f.affiliations),Json(&old.papers),Json(changes),names,schools,Json(evidence),id).execute(conn).await?;
    Ok(())
}
pub(crate) async fn complete(conn: &mut SqliteConnection, id: i64) -> Result<(), sqlx::Error> {
    sqlx::query!("UPDATE candidate SET enrich='done',enrich_error=CASE WHEN json_array_length(enrich_changes)>0 THEN NULL ELSE coalesce((SELECT CASE WHEN status='needs_browser' THEN '需要浏览器：' || error ELSE '页面读取失败：' || error END FROM crawl_page WHERE candidate_id=?1 AND status!='success' ORDER BY status='needs_browser' DESC,kind='enrich_profile' DESC,id LIMIT 1),'页面上没有可补充的资料') END WHERE id=?1 AND review='pending' AND enrich IN ('queued','running') AND NOT EXISTS(SELECT 1 FROM crawl_page WHERE candidate_id=?1 AND work_state!='done')",id).execute(conn).await?;
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
    sqlx::query!("UPDATE crawl_page SET work_state='done',claim=claim+1,error=CASE WHEN status='success' THEN NULL ELSE '候选已处理，停止补全' END WHERE work_state!='done' AND candidate_id IN (SELECT value FROM json_each(?))",Json(ids)).execute(&mut *conn).await?;
    sqlx::query!("UPDATE candidate SET enrich='none' WHERE id IN (SELECT value FROM json_each(?)) AND enrich IN ('queued','running')",Json(ids)).execute(conn).await?;
    Ok(())
}
