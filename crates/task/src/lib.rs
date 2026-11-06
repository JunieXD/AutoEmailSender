//! Batch first-letter and follow-up tasks: fixed inputs, explicit approvals and persisted
//! generation.
pub mod generation;
mod review;
use email::tasks as emails;
pub use review::*;
use serde::{Deserialize, Serialize};
use sqlx::{SqliteConnection, SqlitePool, types::Json};
use ts_rs::TS;

#[derive(Debug)]
pub enum Error {
    Invalid(String),
    Db(sqlx::Error),
}
impl From<sqlx::Error> for Error {
    fn from(e: sqlx::Error) -> Self {
        Self::Db(e)
    }
}
pub type Result<T> = std::result::Result<T, Error>;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Deserialize, Serialize, TS, sqlx::Type)]
#[serde(rename_all = "snake_case")]
#[sqlx(rename_all = "snake_case")]
pub enum Writer {
    Ai,
    Template,
}
#[derive(Debug, Default, Clone, Copy, PartialEq, Eq, Deserialize, Serialize, TS, sqlx::Type)]
#[serde(rename_all = "snake_case")]
#[sqlx(rename_all = "snake_case")]
pub enum Kind {
    #[default]
    First,
    FollowUp,
}

#[derive(Debug, Deserialize, Serialize, TS)]
#[serde(rename_all = "camelCase")]
pub struct NewTask {
    pub source_task_id: Option<i64>,
    pub kind: Kind,
    pub name: String,
    pub schedule: email::schedule::Schedule,
    pub ids: Vec<i64>,
    pub writer: Writer,
    pub template_id: Option<i64>,
    pub template_source_id: Option<i64>,
    pub model_config_id: Option<i64>,
    pub references: Vec<i64>,
    pub attachments: Vec<i64>,
}
#[derive(Debug, Serialize, TS)]
#[serde(rename_all = "camelCase")]
pub struct Candidate {
    pub id: i64,
    pub name: String,
    pub reason: Option<String>,
    pub contact_count: i64,
    pub last_contact_at: Option<i64>,
    pub library_status: Option<cloud_api::library::Status>,
    pub previous: Option<PreviousLetter>,
}
#[derive(Debug, Serialize, TS)]
#[serde(rename_all = "camelCase")]
pub struct TaskPlan {
    pub mentors: Vec<Candidate>,
    pub model: Option<String>,
    pub model_config_id: Option<i64>,
    pub input_tokens: i64,
    pub output_tokens: i64,
    pub cost: Option<f64>,
}

#[derive(Debug, Serialize, TS)]
#[serde(rename_all = "camelCase")]
pub struct PreviousLetter {
    pub subject: Option<String>,
    pub sent_at: i64,
    /// Whether it was opened, when it went with a tracking image.
    pub opened: Option<bool>,
}

async fn validate_source(conn: &mut SqliteConnection, form: &NewTask) -> Result<()> {
    if let Some(source) = form.source_task_id {
        let valid = sqlx::query_scalar!(
            r#"SELECT EXISTS (SELECT 1 FROM task WHERE id = ?1)
               AND NOT EXISTS (
                 SELECT 1 FROM json_each(?2) selected
                 WHERE NOT EXISTS (SELECT 1 FROM email
                   WHERE task_id = ?1 AND mentor_id = selected.value)
               ) AS "valid!: bool""#,
            source,
            Json(&form.ids),
        )
        .fetch_one(conn)
        .await?;
        if !valid {
            return Err(Error::Invalid(
                "原任务已删除或已变化，请重新打开“重新发起”。".into(),
            ));
        }
    }
    Ok(())
}

async fn candidates(
    conn: &mut SqliteConnection,
    ids: &[i64],
    kind: Kind,
    source_task: Option<i64>,
) -> std::result::Result<Vec<Candidate>, sqlx::Error> {
    let rows = sqlx::query!(
        r#"SELECT m.id, m.name,
             CASE WHEN m.trashed_at IS NOT NULL THEN '导师在回收站里'
               WHEN NOT EXISTS (SELECT 1 FROM mentor_email WHERE mentor_id = m.id)
                 THEN '导师尚未填写邮箱'
               WHEN EXISTS (SELECT 1 FROM email WHERE mentor_id = m.id
                 AND task_id = ?2 AND state = 'sent')
                 THEN '原任务中的邮件已发出'
               WHEN EXISTS (SELECT 1 FROM email WHERE mentor_id = m.id
                 AND kind IN ('first', 'follow_up')
                 AND state IN ('generating', 'generation_failed', 'review', 'approved',
                   'cloud', 'sending', 'maybe_sent', 'failed')
                 AND NOT (task_id IS ?2 AND ?2 IS NOT NULL
                   AND state IN ('failed', 'generation_failed')))
                 THEN '还有一封没发出的首封或追问'
               ELSE NULL END AS "reason: String",
             coalesce(c.contact_count, 0) AS "contact_count!: i64",
             c.last_out_at AS last_contact_at, c.opened AS "opened?: bool",
             (SELECT json_quote(status) FROM mentor_library WHERE mentor_id = m.id
                AND status IN ('retired', 'departed', 'deceased')
                ORDER BY CASE status WHEN 'deceased' THEN 0 WHEN 'departed' THEN 1 ELSE 2 END,
                    library_id LIMIT 1) AS "library_status?: Json<cloud_api::library::Status>"
           FROM mentor m LEFT JOIN mentor_contact c ON c.mentor_id = m.id
           WHERE m.id IN (SELECT value FROM json_each(?1))
           ORDER BY m.name, m.id"#,
        Json(ids),
        source_task,
    )
    .fetch_all(&mut *conn)
    .await?;
    let mut candidates = Vec::new();
    for row in rows {
        let (previous, reason) = if kind == Kind::FollowUp {
            let e = email::follow_up::eligibility(conn, row.id, source_task).await?;
            (e.previous, e.reason.or(row.reason))
        } else {
            (None, row.reason)
        };
        candidates.push(Candidate {
            id: row.id,
            name: row.name,
            reason,
            contact_count: row.contact_count,
            last_contact_at: row.last_contact_at,
            library_status: row.library_status.map(|s| s.0),
            // A mentor who can be followed up has not replied, so the latest letter out, which
            // the contact summary describes, is the one followed up.
            previous: previous.map(|p| PreviousLetter {
                subject: p.subject,
                sent_at: p.sent_at,
                opened: row.opened,
            }),
        });
    }
    Ok(candidates)
}

pub async fn references(
    conn: &mut SqliteConnection,
    ids: &[i64],
) -> std::result::Result<Vec<material::Reference>, sqlx::Error> {
    sqlx::query_as!(
        material::Reference,
        r#"SELECT file_name, extracted_text AS "text!"
        FROM material
        WHERE id IN (SELECT value FROM json_each(?)) AND extracted_text IS NOT NULL
        ORDER BY id"#,
        Json(ids)
    )
    .fetch_all(conn)
    .await
}

/// Newest sent letters first; the writing boundary caps their combined text.
pub async fn history(
    conn: &mut SqliteConnection,
    mentor: i64,
) -> std::result::Result<Vec<writing::Letter>, sqlx::Error> {
    sqlx::query_as!(
        writing::Letter,
        r#"SELECT coalesce(subject, '') AS "subject!: String",
             coalesce(body, '') AS "body_md!: String"
           FROM (
             SELECT subject, substr(text, 1, 10000) AS body, sent_at, id, 1 AS source
             FROM email
             WHERE mentor_id = ?1 AND kind IN ('first', 'follow_up')
               AND state IN ('sent', 'bounced')
             UNION ALL
             SELECT m.subject, substr(m.body_text, 1, 10000), m.sent_at, m.id, 0
             FROM message m JOIN message_mentor mm ON mm.message_id = m.id
             WHERE mm.mentor_id = ?1 AND m.kind = 'sent'
           ) ORDER BY sent_at DESC, source DESC, id DESC LIMIT 20"#,
        mentor
    )
    .fetch_all(conn)
    .await
}

struct TemplateContent {
    template_id: Option<i64>,
    subject: String,
    body_md: String,
}
async fn template_content(
    conn: &mut SqliteConnection,
    form: &NewTask,
) -> Result<Option<TemplateContent>> {
    if let Some(id) = form.template_source_id {
        let template = sqlx::query_as!(
            TemplateContent,
            r#"SELECT template_id, template_subject AS "subject!: String",
                template_body_md AS "body_md!: String"
            FROM task
            WHERE id = ? AND template_subject IS NOT NULL"#,
            id
        )
        .fetch_optional(&mut *conn)
        .await?;
        if template.is_none()
            && !sqlx::query_scalar!(
                r#"SELECT EXISTS (SELECT 1 FROM task WHERE id = ?) AS "exists!: bool""#,
                id
            )
            .fetch_one(conn)
            .await?
        {
            return Err(Error::Invalid("原任务已被删除，请重新选择模板。".into()));
        }
        return Ok(template);
    }
    match form.template_id {
        Some(id) => Ok(Some(
            sqlx::query_as!(
                TemplateContent,
                "SELECT id AS \"template_id?\",subject,body_md FROM template WHERE id=?",
                id
            )
            .fetch_optional(conn)
            .await?
            .ok_or_else(|| Error::Invalid("模板已被删除，请重新选择。".into()))?,
        )),
        None => Ok(None),
    }
}

pub async fn plan(db: &SqlitePool, form: &NewTask) -> Result<TaskPlan> {
    let mut tx = db.begin().await?;
    validate_source(&mut tx, form).await?;
    let mentors = candidates(&mut tx, &form.ids, form.kind, form.source_task_id).await?;
    let refs = references(&mut tx, &form.references).await?;
    let template = template_content(&mut tx, form).await?;
    let chosen = model::selected(&mut *tx, form.model_config_id).await?;
    tx.commit().await?;
    let me = profile::get(db).await?;
    let attached = material::list(db)
        .await?
        .into_iter()
        .filter(|m| form.attachments.contains(&m.id))
        .map(|m| m.file_name)
        .collect::<Vec<_>>();
    let fact_check = profile::ai_fact_check(db).await?;
    let (mut input_tokens, mut output_tokens) = (0, 0);
    if form.writer == Writer::Ai {
        for m in mentors.iter().filter(|m| m.reason.is_none()) {
            if let Some(m) = mentor::get(db, m.id).await? {
                let current = template.as_ref().map(|t| {
                    fill(
                        t.subject.as_str(),
                        t.body_md.as_str(),
                        &m,
                        me.name.as_deref(),
                    )
                });
                let previous = if form.kind == Kind::FollowUp {
                    Some(history(&mut *db.acquire().await?, m.id).await?)
                } else {
                    None
                };
                let (i, o) = writing::estimate(
                    &me,
                    &refs,
                    &attached,
                    &m,
                    current.as_ref(),
                    previous
                        .as_deref()
                        .map_or(writing::Kind::First, writing::Kind::FollowUp),
                );
                input_tokens += i;
                output_tokens += o;
                if fact_check {
                    let (i, o) = writing::fact_check::estimate(&me, &refs, o);
                    input_tokens += i;
                    output_tokens += o;
                }
            }
        }
    }
    let config = model::list(db)
        .await?
        .into_iter()
        .find(|c| chosen.as_ref().is_some_and(|m| c.id == m.id));
    let cost = config.and_then(|c| {
        Some(
            (c.input_price? * input_tokens as f64 + c.output_price? * output_tokens as f64)
                / 1_000_000.0,
        )
    });
    Ok(TaskPlan {
        mentors,
        model: chosen.as_ref().map(|c| c.model.clone()),
        model_config_id: chosen.map(|c| c.id),
        input_tokens,
        output_tokens,
        cost,
    })
}

pub fn fill(
    subject: &str,
    body: &str,
    m: &mentor::MentorDetail,
    my_name: Option<&str>,
) -> writing::Letter {
    let main = m.affiliations.first().cloned().unwrap_or_default();
    let f = template::Fill {
        mentor_name: &m.name,
        mentor_title: main.title.as_deref(),
        university: main.university.as_deref(),
        school: main.school.as_deref(),
        department: main.department.as_deref(),
        research: m.research.as_deref(),
        my_name,
    };
    writing::Letter {
        subject: f.subject(subject),
        body_md: f.body(body),
    }
}

pub fn schedule_capacity(
    window: &email::schedule::Window,
    now: i64,
) -> Result<email::schedule::Capacity> {
    let window = window
        .validate()
        .map_err(|message| Error::Invalid(message.into()))?;
    Ok(window.capacity(now, &chrono::Local))
}

pub async fn create(db: &SqlitePool, form: &NewTask, now: i64) -> Result<i64> {
    if form.name.trim().is_empty() {
        return Err(Error::Invalid("请填写任务名称".into()));
    }
    let schedule = form
        .schedule
        .validate()
        .map_err(|s| Error::Invalid(s.into()))?;
    if let email::schedule::ValidatedSchedule::Window(w) = &schedule {
        let count = w.capacity(now, &chrono::Local).emails;
        if count < form.ids.len() as u64 {
            return Err(Error::Invalid(format!(
                "发送容量不足，还差 {} 封",
                form.ids.len() as u64 - count
            )));
        }
    }
    let mut tx = db.begin_with("BEGIN IMMEDIATE").await?;
    validate_source(&mut tx, form).await?;
    let template = template_content(&mut tx, form).await?;
    let writing_template = match form.writer {
        Writer::Template => Some(
            template
                .as_ref()
                .ok_or_else(|| Error::Invalid("请选择模板".into()))?,
        ),
        Writer::Ai => None,
    };
    let (subject, body) = (
        template.as_ref().map(|t| t.subject.as_str()),
        template.as_ref().map(|t| t.body_md.as_str()),
    );
    let template_id = template.as_ref().and_then(|t| t.template_id);
    let chosen = if form.writer == Writer::Ai || form.model_config_id.is_some() {
        model::selected(&mut *tx, form.model_config_id).await?
    } else {
        None
    };
    if form.writer == Writer::Ai {
        let chosen = chosen
            .as_ref()
            .ok_or_else(|| Error::Invalid("尚未添加模型，请先在设置中添加。".into()))?;
        if !chosen.agreed {
            return Err(Error::Invalid("请先确认发给模型服务商的内容。".into()));
        }
    }
    let model_config_id = chosen.as_ref().map(|c| c.id);
    if let Some(source) = form.source_task_id {
        let failures = sqlx::query_scalar!(
            r#"SELECT id
            FROM email
            WHERE task_id = ? AND mentor_id IN (SELECT value FROM json_each(?))
            AND state IN ('failed', 'generation_failed')
            ORDER BY id"#,
            source,
            Json(&form.ids),
        )
        .fetch_all(&mut *tx)
        .await?;
        for failure in failures {
            email::cancel(
                &mut tx,
                email::CancelScope::Task {
                    task: source,
                    email: Some(failure),
                },
                "已在新任务重新发起",
                now,
            )
            .await?;
        }
    }
    let mentors = candidates(&mut tx, &form.ids, form.kind, form.source_task_id).await?;
    let mut ids = form.ids.clone();
    ids.sort_unstable();
    ids.dedup();
    if ids.is_empty() || ids.len() != mentors.len() || mentors.iter().any(|m| m.reason.is_some()) {
        return Err(Error::Invalid(
            "有导师无法加入任务，请重新核对导师列表。".into(),
        ));
    }
    let materials = [
        ("reference", &form.references),
        ("attachment", &form.attachments),
    ];
    for (role, ids) in materials {
        for material in ids {
            let valid = sqlx::query_scalar!(
                r#"SELECT id
                FROM material
                WHERE id = ?1 AND (?2 = 'attachment' OR extracted_text IS NOT NULL)"#,
                material,
                role
            )
            .fetch_optional(&mut *tx)
            .await?
            .is_some();
            if !valid {
                return Err(Error::Invalid(
                    "所选材料已被删除或没有可供写信的文字，请重新选择。".into(),
                ));
            }
        }
    }
    let (schedule_kind, window) = match &form.schedule {
        email::schedule::Schedule::Now => ("now", None),
        email::schedule::Schedule::Window(window) => ("window", Some(window)),
    };
    let dates = window.map(|w| Json(&w.dates));
    let start = window.map(|w| w.start.as_str());
    let end = window.map(|w| w.end.as_str());
    let per_window = window.map(|w| i64::from(w.per_window));
    let id = sqlx::query_scalar!(
        r#"INSERT INTO task (name, kind, writer, schedule, window_dates, window_start,
            window_end, per_window, template_id, template_subject, template_body_md,
            model_config_id, state, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?)
        RETURNING id AS "id!""#,
        form.name.trim(),
        form.kind,
        form.writer,
        schedule_kind,
        dates,
        start,
        end,
        per_window,
        template_id,
        subject,
        body,
        model_config_id,
        now
    )
    .fetch_one(&mut *tx)
    .await?;
    for (role, ids) in materials {
        for material in ids {
            sqlx::query!(
                r#"INSERT INTO task_material (task_id, material_id, role)
                VALUES (?, ?, ?)
                ON CONFLICT DO NOTHING"#,
                id,
                material,
                role
            )
            .execute(&mut *tx)
            .await?;
        }
    }
    // Material and mentor details are read after the write lock, without holding it across a
    // network call.
    let my_name = sqlx::query_scalar!("SELECT name FROM profile")
        .fetch_one(&mut *tx)
        .await?;
    for candidate in mentors {
        let letter = if let Some(template) = writing_template {
            let m = mentor::detail(&mut tx, candidate.id).await?;
            m.map(|m| fill(&template.subject, &template.body_md, &m, my_name.as_deref()))
        } else {
            None
        };
        let parent = if form.kind == Kind::FollowUp {
            email::thread::previous(&mut tx, candidate.id, None, false).await?
        } else {
            None
        };
        let reply_to = parent.as_ref().and_then(|p| p.message_id.as_deref());
        let email = emails::create(
            &mut tx,
            id,
            candidate.id,
            reply_to,
            letter
                .as_ref()
                .map(|l| (l.subject.as_str(), l.body_md.as_str())),
            &form.attachments,
            now,
        )
        .await?;
        if let Some(parent) = parent {
            let subject = email::reply_subject(parent.subject.as_deref().unwrap_or_default());
            sqlx::query!("UPDATE email SET subject=? WHERE id=?", subject, email)
                .execute(&mut *tx)
                .await?;
        }
    }
    tx.commit().await?;
    Ok(id)
}

pub async fn pause(db: &SqlitePool, id: i64, now: i64) -> Result<()> {
    sqlx::query!(
        r#"UPDATE task
        SET state = 'paused', generation_paused_at = ?, generation_pause_reason = NULL
        WHERE id = ? AND state <> 'stopped'"#,
        now,
        id
    )
    .execute(db)
    .await?;
    Ok(())
}
pub async fn resume(db: &SqlitePool, id: i64) -> Result<()> {
    sqlx::query!(
        r#"UPDATE task
        SET state = 'active', generation_paused_at = NULL,
            generation_pause_reason = NULL
        WHERE id = ? AND state <> 'stopped'"#,
        id
    )
    .execute(db)
    .await?;
    Ok(())
}
pub async fn stop(db: &SqlitePool, id: i64, now: i64) -> Result<()> {
    let mut tx = db.begin().await?;
    sqlx::query!("UPDATE task SET state = 'stopped' WHERE id = ?", id)
        .execute(&mut *tx)
        .await?;
    email::cancel(
        &mut tx,
        email::CancelScope::Task {
            task: id,
            email: None,
        },
        "任务停止",
        now,
    )
    .await?;
    tx.commit().await?;
    Ok(())
}
pub async fn delete(db: &SqlitePool, id: i64, now: i64) -> Result<()> {
    let mut tx = db.begin_with("BEGIN IMMEDIATE").await?;
    let busy = sqlx::query!(
        r#"SELECT count(*) FILTER (WHERE state = 'sending') AS "sending!: i64",
             count(*) FILTER (WHERE state = 'cloud') AS "cloud!: i64"
           FROM email WHERE task_id = ?"#,
        id
    )
    .fetch_one(&mut *tx)
    .await?;
    if busy.cloud > 0 {
        return Err(Error::Invalid(format!(
            "有 {} 封邮件已交给云端，请先撤回到本地再删除。",
            busy.cloud
        )));
    }
    if busy.sending > 0 {
        return Err(Error::Invalid(
            "有正在发送的邮件，请发送结束后再删除。".into(),
        ));
    }
    // A retry waiting to go keeps an earlier attempt's delivery evidence; canceled, it stays.
    email::cancel(
        &mut tx,
        email::CancelScope::Task {
            task: id,
            email: None,
        },
        "任务删除",
        now,
    )
    .await?;
    emails::delete(&mut tx, id).await?;
    sqlx::query!("DELETE FROM task WHERE id = ?", id)
        .execute(&mut *tx)
        .await?;
    tx.commit().await?;
    Ok(())
}

#[derive(Debug, Deserialize, TS)]
#[serde(rename_all = "camelCase")]
pub struct ScheduleEdit {
    pub id: i64,
    pub schedule: email::schedule::Schedule,
}

pub async fn change_schedule(db: &SqlitePool, edit: &ScheduleEdit, now: i64) -> Result<u64> {
    let schedule = edit
        .schedule
        .validate()
        .map_err(|s| Error::Invalid(s.into()))?;
    let mut tx = db.begin_with("BEGIN IMMEDIATE").await?;
    if sqlx::query_scalar!(
        r#"SELECT id FROM task
        WHERE id = ? AND state <> 'stopped'"#,
        edit.id
    )
    .fetch_optional(&mut *tx)
    .await?
    .is_none()
    {
        return Err(Error::Invalid("任务已停止或已删除。".into()));
    }
    email::schedule::save(&mut tx, edit.id, &schedule).await?;
    let count = sqlx::query!(
        r#"UPDATE email SET planned_at = NULL, latest_at = NULL, manual_schedule = 0
           WHERE task_id = ?
             AND state = 'approved'"#,
        edit.id
    )
    .execute(&mut *tx)
    .await?
    .rows_affected();
    let ordered = sqlx::query_scalar!(
        r#"SELECT id
           FROM email
           WHERE task_id = ?
             AND state = 'approved'
           ORDER BY approval_order, id"#,
        edit.id
    )
    .fetch_all(&mut *tx)
    .await?;
    for id in ordered {
        email::schedule::assign(&mut tx, id, edit.id, now, &chrono::Local, &mut |n| {
            fastrand::i64(0..n)
        })
        .await?;
    }
    tx.commit().await?;
    Ok(count)
}
