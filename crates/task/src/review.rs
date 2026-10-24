use super::*;
use emails::DraftState;
use std::{collections::HashMap, num::NonZeroU32};

#[derive(Debug, Default, Serialize, TS)]
#[serde(rename_all = "camelCase")]
pub struct Progress {
    pub generating: i64,
    pub review: i64,
    pub generation_failed: i64,
    pub approved: i64,
    pub sent: i64,
    pub failed: i64,
    pub canceled: i64,
}
impl Progress {
    pub fn total(&self) -> i64 {
        self.generating
            + self.review
            + self.generation_failed
            + self.approved
            + self.sent
            + self.failed
            + self.canceled
    }
}
#[derive(Debug, Serialize, TS)]
#[serde(rename_all = "camelCase")]
pub struct TaskSummary {
    pub id: i64,
    pub name: String,
    pub writer: Writer,
    pub state: TaskState,
    pub created_at: i64,
    pub pause_reason: Option<String>,
    pub model_config_id: Option<i64>,
    pub progress: Progress,
}
#[derive(Debug, PartialEq, Eq, Serialize, TS)]
#[serde(rename_all = "snake_case")]
pub enum TaskState {
    Active,
    Paused,
    Stopped,
    Completed,
}
#[derive(Debug, Deserialize, TS)]
pub struct TaskQuery {
    pub page: NonZeroU32,
}
#[derive(Debug, Serialize, TS)]
pub struct TaskPage {
    pub tasks: Vec<TaskSummary>,
    pub total: i64,
}

pub async fn list(
    db: &SqlitePool,
    query: &TaskQuery,
) -> std::result::Result<TaskPage, sqlx::Error> {
    list_page(db, query.page, 20).await
}

pub async fn work(db: &SqlitePool) -> std::result::Result<Vec<TaskSummary>, sqlx::Error> {
    let mut tasks = list_page(db, NonZeroU32::MIN, -1).await?.tasks;
    tasks.retain(|t| {
        t.state == TaskState::Paused
            || t.progress.generating > 0
            || t.progress.generation_failed > 0
            || t.progress.failed > 0
    });
    Ok(tasks)
}

async fn list_page(
    db: &SqlitePool,
    page: NonZeroU32,
    limit: i64,
) -> std::result::Result<TaskPage, sqlx::Error> {
    let mut tx = db.begin().await?;
    let total = sqlx::query_scalar!("SELECT count(*) FROM task")
        .fetch_one(&mut *tx)
        .await?;
    let offset = i64::from(page.get() - 1) * 20;
    let rows = sqlx::query!(r#"SELECT id,name,writer AS "writer: Writer",state,created_at,generation_paused_at,generation_pause_reason,model_config_id FROM task ORDER BY id DESC LIMIT ? OFFSET ?"#,limit,offset).fetch_all(&mut *tx).await?;
    let ids = rows.iter().map(|t| t.id).collect::<Vec<_>>();
    let mut counts = counts(&mut tx, &ids).await?;
    let tasks = rows
        .into_iter()
        .map(|r| {
            let progress = counts.remove(&r.id).unwrap_or_default();
            let state = state(&r.state, r.generation_paused_at.is_some(), &progress);
            TaskSummary {
                id: r.id,
                name: r.name,
                writer: r.writer,
                state,
                created_at: r.created_at,
                pause_reason: r.generation_pause_reason,
                model_config_id: r.model_config_id,
                progress,
            }
        })
        .collect();
    Ok(TaskPage { tasks, total })
}
fn state(state: &str, paused: bool, p: &Progress) -> TaskState {
    if state == "stopped" {
        TaskState::Stopped
    } else if state == "paused" || paused {
        TaskState::Paused
    } else if p.generating + p.review + p.approved == 0 {
        TaskState::Completed
    } else {
        TaskState::Active
    }
}
async fn counts(
    conn: &mut SqliteConnection,
    ids: &[i64],
) -> std::result::Result<HashMap<i64, Progress>, sqlx::Error> {
    let rows = sqlx::query!(r#"SELECT task_id AS "task_id!",state AS "state: DraftState",count(*) AS count FROM email WHERE task_id IN (SELECT value FROM json_each(?)) GROUP BY task_id,state"#,Json(ids)).fetch_all(conn).await?;
    let mut counts: HashMap<i64, Progress> = HashMap::new();
    for r in rows {
        let p = counts.entry(r.task_id).or_default();
        match r.state {
            DraftState::Generating => p.generating += r.count,
            DraftState::Review => p.review += r.count,
            DraftState::GenerationFailed => p.generation_failed += r.count,
            DraftState::Approved | DraftState::Sending | DraftState::Cloud => p.approved += r.count,
            DraftState::MaybeSent | DraftState::Sent | DraftState::Bounced => p.sent += r.count,
            DraftState::Failed => p.failed += r.count,
            DraftState::Canceled => p.canceled += r.count,
        }
    }
    Ok(counts)
}
pub async fn get(
    db: &SqlitePool,
    id: i64,
) -> std::result::Result<Option<TaskSummary>, sqlx::Error> {
    let mut tx = db.begin().await?;
    let Some(r) = sqlx::query!(r#"SELECT id,name,writer AS "writer: Writer",state,created_at,generation_paused_at,generation_pause_reason,model_config_id FROM task WHERE id = ?"#,id).fetch_optional(&mut *tx).await? else { return Ok(None); };
    let progress = counts(&mut tx, &[id])
        .await?
        .remove(&id)
        .unwrap_or_default();
    Ok(Some(TaskSummary {
        id: r.id,
        name: r.name,
        writer: r.writer,
        state: state(&r.state, r.generation_paused_at.is_some(), &progress),
        created_at: r.created_at,
        pause_reason: r.generation_pause_reason,
        model_config_id: r.model_config_id,
        progress,
    }))
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Deserialize, TS)]
#[serde(rename_all = "snake_case")]
pub enum DraftFilter {
    All,
    Review,
    Problems,
    GenerationFailed,
    Approved,
}
#[derive(Debug, Deserialize, TS)]
#[serde(rename_all = "camelCase")]
pub struct DraftQuery {
    pub task_id: i64,
    pub filter: DraftFilter,
    pub page: NonZeroU32,
}
#[derive(Debug, Serialize, TS)]
#[serde(rename_all = "camelCase")]
pub struct DraftSummary {
    pub id: i64,
    pub mentor_id: i64,
    pub name: String,
    pub state: DraftState,
    pub problems: usize,
}
#[derive(Debug, Serialize, TS)]
pub struct DraftPage {
    pub drafts: Vec<DraftSummary>,
    pub total: i64,
}

async fn other_names(
    conn: &mut SqliteConnection,
    task: i64,
) -> std::result::Result<Vec<String>, sqlx::Error> {
    sqlx::query_scalar!("SELECT DISTINCT m.name FROM mentor m JOIN email e ON e.mentor_id = m.id WHERE e.task_id = ?",task).fetch_all(conn).await
}

// This reads text only inside the core to calculate rules; the list sends short summaries.
// All rows are needed only for the problem filter. Other filters page before reading text.
pub async fn drafts(
    db: &SqlitePool,
    q: &DraftQuery,
    now: i64,
) -> std::result::Result<DraftPage, sqlx::Error> {
    let filter = match q.filter {
        DraftFilter::All | DraftFilter::Problems => None,
        DraftFilter::Review => Some("review"),
        DraftFilter::GenerationFailed => Some("generation_failed"),
        DraftFilter::Approved => Some("approved"),
    };
    let mut tx = db.begin().await?;
    let total = sqlx::query_scalar!(
        "SELECT count(*) FROM email WHERE task_id = ? AND (?2 IS NULL OR state = ?2)",
        q.task_id,
        filter
    )
    .fetch_one(&mut *tx)
    .await?;
    let names = other_names(&mut tx, q.task_id).await?;
    let limit = if q.filter == DraftFilter::Problems {
        -1
    } else {
        50
    };
    let offset = if q.filter == DraftFilter::Problems {
        0
    } else {
        i64::from(q.page.get() - 1) * 50
    };
    let rows = review_rows(&mut tx, q.task_id, filter, limit, offset).await?;
    let mut drafts = rows
        .into_iter()
        .map(|r| {
            let count = problems(&r, &names, now).len();
            DraftSummary {
                id: r.id,
                mentor_id: r.mentor_id,
                name: r.name,
                state: r.state,
                problems: count,
            }
        })
        .collect::<Vec<_>>();
    if q.filter == DraftFilter::Problems {
        drafts.retain(|d| d.problems > 0);
        let total = drafts.len() as i64;
        return Ok(DraftPage {
            drafts: drafts
                .into_iter()
                .skip((q.page.get() as usize - 1) * 50)
                .take(50)
                .collect(),
            total,
        });
    }
    Ok(DraftPage { drafts, total })
}
struct ReviewRow {
    id: i64,
    mentor_id: i64,
    name: String,
    state: DraftState,
    subject: Option<String>,
    body_md: Option<String>,
    aliases: Json<Vec<String>>,
    attachments: Json<Vec<i64>>,
    contact_count: Option<i64>,
    last_out_at: Option<i64>,
}
async fn review_rows(
    conn: &mut SqliteConnection,
    task: i64,
    filter: Option<&str>,
    limit: i64,
    offset: i64,
) -> std::result::Result<Vec<ReviewRow>, sqlx::Error> {
    sqlx::query_as!(ReviewRow, r#"SELECT e.id,e.mentor_id,m.name,e.state AS "state: DraftState",e.subject,e.body_md,
        (SELECT json_group_array(name) FROM mentor_alias WHERE mentor_id = m.id) AS "aliases!: Json<Vec<String>>",
        (SELECT json_group_array(material_id) FROM email_attachment WHERE email_id = e.id AND material_id IS NOT NULL) AS "attachments!: Json<Vec<i64>>",
        c.contact_count,c.last_out_at
        FROM email e JOIN mentor m ON m.id = e.mentor_id LEFT JOIN mentor_contact c ON c.mentor_id = m.id
        WHERE e.task_id = ?1 AND (?2 IS NULL OR e.state = ?2) ORDER BY m.name,e.id LIMIT ?3 OFFSET ?4"#,task,filter,limit,offset).fetch_all(conn).await
}
fn problems(r: &ReviewRow, names: &[String], now: i64) -> Vec<check::Problem> {
    if r.state != DraftState::Review {
        return vec![];
    }
    let (subject, body) = (
        r.subject.as_deref().unwrap_or_default(),
        r.body_md.as_deref().unwrap_or_default(),
    );
    let mut p = check::letter(
        &r.name,
        &r.aliases,
        subject,
        body,
        !r.attachments.is_empty(),
    );
    p.extend(check::other_mentors(
        &r.name, &r.aliases, names, subject, body,
    ));
    recent(&mut p, r.contact_count, r.last_out_at, now);
    p
}
fn recent(problems: &mut Vec<check::Problem>, count: Option<i64>, last: Option<i64>, now: i64) {
    if let Some(last_at) = last
        && now - last_at < 7 * 86_400_000
    {
        problems.push(check::Problem::RecentlyContacted {
            count: count.unwrap_or(0),
            last_at,
        });
    }
}
#[derive(Debug, Deserialize, Serialize, TS)]
#[serde(rename_all = "camelCase")]
pub struct TaskEmail {
    pub task_id: i64,
    pub id: i64,
}
#[derive(Debug, Serialize, TS)]
#[serde(rename_all = "camelCase")]
pub struct TaskDraft {
    pub id: i64,
    pub mentor_id: i64,
    pub subject: String,
    pub body_md: String,
    pub attachments: Vec<i64>,
    pub state: DraftState,
    pub error: Option<String>,
}
pub async fn draft(
    db: &SqlitePool,
    key: &TaskEmail,
) -> std::result::Result<Option<TaskDraft>, sqlx::Error> {
    let mut tx = db.begin().await?;
    let Some(r) = sqlx::query!(r#"SELECT id,mentor_id,coalesce(subject,'') AS "subject!: String",coalesce(body_md,'') AS "body_md!: String",state AS "state: DraftState",error FROM email WHERE id = ? AND task_id = ?"#,key.id,key.task_id).fetch_optional(&mut *tx).await? else { return Ok(None); };
    Ok(Some(TaskDraft {
        id: r.id,
        mentor_id: r.mentor_id,
        subject: r.subject,
        body_md: r.body_md,
        state: r.state,
        error: r.error,
        attachments: email::attached(&mut tx, r.id).await?,
    }))
}
#[derive(Debug, Deserialize, TS)]
#[serde(rename_all = "camelCase")]
pub struct DraftEdit {
    pub task_id: i64,
    pub id: i64,
    pub subject: String,
    pub body_md: String,
    pub attachments: Vec<i64>,
}
pub async fn save(db: &SqlitePool, edit: &DraftEdit, now: i64) -> Result<()> {
    let mut tx = db.begin().await?;
    if !emails::save(
        &mut tx,
        edit.task_id,
        edit.id,
        &edit.subject,
        &edit.body_md,
        &edit.attachments,
        now,
    )
    .await?
    {
        return Err(Error::Invalid(
            "这封邮件已不在待审核状态，请刷新后重试。".into(),
        ));
    }
    tx.commit().await?;
    Ok(())
}
pub async fn check(db: &SqlitePool, edit: &DraftEdit, now: i64) -> Result<Vec<check::Problem>> {
    let mut tx = db.begin().await?;
    let id = sqlx::query_scalar!(
        "SELECT mentor_id FROM email WHERE id = ? AND task_id = ?",
        edit.id,
        edit.task_id
    )
    .fetch_one(&mut *tx)
    .await?;
    let m = mentor::detail(&mut tx, id)
        .await?
        .ok_or_else(|| Error::Invalid("导师已被删除。".into()))?;
    let names = other_names(&mut tx, edit.task_id).await?;
    let mut p = check::letter(
        &m.name,
        &m.aliases,
        &edit.subject,
        &edit.body_md,
        !edit.attachments.is_empty(),
    );
    p.extend(check::other_mentors(
        &m.name,
        &m.aliases,
        &names,
        &edit.subject,
        &edit.body_md,
    ));
    let c = sqlx::query!(
        "SELECT contact_count,last_out_at FROM mentor_contact WHERE mentor_id = ?",
        id
    )
    .fetch_optional(&mut *tx)
    .await?;
    if let Some(c) = c {
        recent(&mut p, Some(c.contact_count), c.last_out_at, now);
    }
    Ok(p)
}
#[derive(Debug, Deserialize, TS)]
pub struct RenderedApproval {
    pub attachments: Vec<i64>,
    pub id: i64,
    pub html: String,
    pub text: String,
    pub subject: String,
    #[serde(rename = "bodyMd")]
    pub body_md: String,
}
#[derive(Debug, Deserialize, TS)]
#[serde(rename_all = "camelCase")]
pub struct TaskApprovals {
    pub task_id: i64,
    pub emails: Vec<RenderedApproval>,
}
pub async fn approve(db: &SqlitePool, request: &TaskApprovals, now: i64) -> Result<u64> {
    let mut tx = db.begin().await?;
    // First statement takes a write lock. Approval always uses the latest human draft.
    sqlx::query!("UPDATE task SET name = name WHERE id = ?", request.task_id)
        .execute(&mut *tx)
        .await?;
    let mut approved = 0;
    for r in &request.emails {
        let current = sqlx::query!(
            "SELECT subject,body_md,state FROM email WHERE id = ? AND task_id = ?",
            r.id,
            request.task_id
        )
        .fetch_optional(&mut *tx)
        .await?;
        let Some(current) = current.filter(|c| c.state == "review") else {
            continue;
        };
        if current.subject.as_deref() != Some(r.subject.as_str())
            || current.body_md.as_deref() != Some(r.body_md.as_str())
        {
            return Err(Error::Invalid("草稿刚被修改，请重新核对后批准。".into()));
        }
        let mut expected = r.attachments.clone();
        expected.sort_unstable();
        let mut attached = email::attached(&mut tx, r.id).await?;
        attached.sort_unstable();
        if attached != expected {
            return Err(Error::Invalid("附件刚被修改，请重新核对后批准。".into()));
        }
        approved += u64::from(
            emails::approve(&mut tx, request.task_id, r.id, &r.html, &r.text, now).await?,
        );
    }
    tx.commit().await?;
    Ok(approved)
}
pub async fn remove(db: &SqlitePool, key: &TaskEmail, now: i64) -> Result<()> {
    emails::cancel(
        &mut *db.acquire().await?,
        key.task_id,
        Some(key.id),
        "移出任务",
        now,
    )
    .await?;
    Ok(())
}
pub async fn regenerate(
    db: &SqlitePool,
    task: i64,
    id: Option<i64>,
    failed_only: bool,
    now: i64,
) -> Result<u64> {
    Ok(emails::regenerate(&mut *db.acquire().await?, task, id, failed_only, now).await?)
}
pub async fn reapply(db: &SqlitePool, key: &TaskEmail, now: i64) -> Result<()> {
    let mut tx = db.begin().await?;
    sqlx::query!("UPDATE task SET name = name WHERE id = ?", key.task_id)
        .execute(&mut *tx)
        .await?;
    let t = sqlx::query!(
        "SELECT template_subject,template_body_md FROM task WHERE id = ?",
        key.task_id
    )
    .fetch_one(&mut *tx)
    .await?;
    let id = sqlx::query_scalar!(
        "SELECT mentor_id FROM email WHERE id = ? AND task_id = ?",
        key.id,
        key.task_id
    )
    .fetch_one(&mut *tx)
    .await?;
    let m = mentor::detail(&mut tx, id)
        .await?
        .ok_or_else(|| Error::Invalid("导师已被删除。".into()))?;
    let name = sqlx::query_scalar!("SELECT name FROM profile")
        .fetch_one(&mut *tx)
        .await?;
    let letter = fill(
        t.template_subject.as_deref().unwrap_or_default(),
        t.template_body_md.as_deref().unwrap_or_default(),
        &m,
        name.as_deref(),
    );
    if !emails::reapply(
        &mut tx,
        key.task_id,
        key.id,
        &letter.subject,
        &letter.body_md,
        now,
    )
    .await?
    {
        return Err(Error::Invalid("这封邮件已不在待审核状态。".into()));
    }
    tx.commit().await?;
    Ok(())
}

/// Full content and rule counts come from one snapshot when a bulk approval is prepared.
#[derive(Debug, Serialize, TS)]
pub struct ApprovalCandidate {
    #[serde(flatten)]
    pub draft: TaskDraft,
    pub problems: usize,
}
pub async fn pending(
    db: &SqlitePool,
    task: i64,
    now: i64,
) -> std::result::Result<Vec<ApprovalCandidate>, sqlx::Error> {
    let mut tx = db.begin().await?;
    let names = other_names(&mut tx, task).await?;
    let rows = review_rows(&mut tx, task, Some("review"), -1, 0).await?;
    Ok(rows
        .into_iter()
        .map(|r| {
            let count = problems(&r, &names, now).len();
            ApprovalCandidate {
                draft: TaskDraft {
                    id: r.id,
                    mentor_id: r.mentor_id,
                    subject: r.subject.unwrap_or_default(),
                    body_md: r.body_md.unwrap_or_default(),
                    attachments: r.attachments.0,
                    state: r.state,
                    error: None,
                },
                problems: count,
            }
        })
        .collect())
}
