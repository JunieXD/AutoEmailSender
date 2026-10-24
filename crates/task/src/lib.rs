//! Batch first-letter tasks: fixed inputs, explicit approvals and persisted generation.
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
#[derive(Debug, Deserialize, TS)]
#[serde(rename_all = "camelCase")]
pub struct NewTask {
    pub name: String,
    pub ids: Vec<i64>,
    pub writer: Writer,
    pub template_id: Option<i64>,
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

async fn candidates(
    conn: &mut SqliteConnection,
    ids: &[i64],
) -> std::result::Result<Vec<Candidate>, sqlx::Error> {
    sqlx::query_as!(Candidate, r#"SELECT m.id, m.name,
        CASE WHEN m.trashed_at IS NOT NULL THEN '导师在回收站里'
          WHEN NOT EXISTS (SELECT 1 FROM mentor_email WHERE mentor_id = m.id) THEN '导师尚未填写邮箱'
          WHEN EXISTS (SELECT 1 FROM email WHERE mentor_id = m.id AND kind = 'first' AND task_id IS NOT NULL AND state IN ('generating','generation_failed','review','approved','cloud','sending','failed')) THEN '别的任务里有未发出的首封'
          WHEN EXISTS (SELECT 1 FROM email WHERE mentor_id = m.id AND kind = 'first' AND task_id IS NULL AND state = 'review') THEN '导师详情里有未完成的首封草稿'
          ELSE NULL END AS "reason: String",
        coalesce(c.contact_count, 0) AS "contact_count!: i64", c.last_out_at AS last_contact_at
        FROM mentor m LEFT JOIN mentor_contact c ON c.mentor_id = m.id
        WHERE m.id IN (SELECT value FROM json_each(?)) ORDER BY m.name, m.id"#, Json(ids)).fetch_all(conn).await
}

pub async fn references(
    conn: &mut SqliteConnection,
    ids: &[i64],
) -> std::result::Result<Vec<material::Reference>, sqlx::Error> {
    sqlx::query_as!(material::Reference, r#"SELECT file_name, extracted_text AS "text!" FROM material WHERE id IN (SELECT value FROM json_each(?)) AND extracted_text IS NOT NULL ORDER BY id"#, Json(ids)).fetch_all(conn).await
}

pub async fn plan(db: &SqlitePool, form: &NewTask) -> Result<TaskPlan> {
    let mut tx = db.begin().await?;
    let mentors = candidates(&mut tx, &form.ids).await?;
    let refs = references(&mut tx, &form.references).await?;
    let chosen = model::selected(&mut *tx, form.model_config_id).await?;
    tx.commit().await?;
    let me = profile::get(db).await?;
    let attached = material::list(db)
        .await?
        .into_iter()
        .filter(|m| form.attachments.contains(&m.id))
        .map(|m| m.file_name)
        .collect::<Vec<_>>();
    let template = match form.template_id {
        Some(id) => template::get(db, id).await?,
        None => None,
    };
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
                let (i, o) = writing::estimate(&me, &refs, &attached, &m, current.as_ref());
                input_tokens += i;
                output_tokens += o;
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

pub async fn create(db: &SqlitePool, form: &NewTask, now: i64) -> Result<i64> {
    if form.name.trim().is_empty() {
        return Err(Error::Invalid("请填写任务名称".into()));
    }
    let writer = match form.writer {
        Writer::Ai => "ai",
        Writer::Template => "template",
    };
    let mut tx = db.begin().await?;
    // Take the write lock before reading template content or checking membership.
    let id = sqlx::query_scalar!("INSERT INTO task(name,kind,writer,schedule,state,created_at) VALUES (?,'first','ai','now','active',?) RETURNING id AS \"id!\"",form.name.trim(),now).fetch_one(&mut *tx).await?;
    let template = match form.template_id {
        Some(id) => Some(
            sqlx::query!("SELECT subject,body_md FROM template WHERE id = ?", id)
                .fetch_optional(&mut *tx)
                .await?
                .ok_or_else(|| Error::Invalid("模板已被删除，请重新选择。".into()))?,
        ),
        None => None,
    };
    if form.writer == Writer::Template && template.is_none() {
        return Err(Error::Invalid("请选择模板".into()));
    }
    let (subject, body) = (
        template.as_ref().map(|t| t.subject.as_str()),
        template.as_ref().map(|t| t.body_md.as_str()),
    );
    sqlx::query!("UPDATE task SET writer=?,template_id=?,template_subject=?,template_body_md=?,model_config_id=? WHERE id=?",writer,form.template_id,subject,body,form.model_config_id,id).execute(&mut *tx).await?;
    let mentors = candidates(&mut tx, &form.ids).await?;
    let mut ids = form.ids.clone();
    ids.sort_unstable();
    ids.dedup();
    if ids.is_empty() || ids.len() != mentors.len() || mentors.iter().any(|m| m.reason.is_some()) {
        return Err(Error::Invalid(
            "有导师无法加入任务，请重新核对导师列表。".into(),
        ));
    }
    if form.writer == Writer::Ai {
        let chosen = model::selected(&mut *tx, form.model_config_id)
            .await?
            .ok_or_else(|| Error::Invalid("尚未添加模型，请先在设置中添加。".into()))?;
        if !chosen.agreed {
            return Err(Error::Invalid("请先确认发给模型服务商的内容。".into()));
        }
        sqlx::query!(
            "UPDATE task SET model_config_id = ? WHERE id = ?",
            chosen.id,
            id
        )
        .execute(&mut *tx)
        .await?;
    }
    for (role, materials) in [
        ("reference", &form.references),
        ("attachment", &form.attachments),
    ] {
        for material in materials {
            let valid = sqlx::query_scalar!("SELECT id FROM material WHERE id = ?1 AND (?2 = 'attachment' OR extracted_text IS NOT NULL)", material,role).fetch_optional(&mut *tx).await?.is_some();
            if !valid {
                return Err(Error::Invalid(
                    "所选材料已被删除或没有可供写信的文字，请重新选择。".into(),
                ));
            }
            sqlx::query!("INSERT INTO task_material (task_id,material_id,role) VALUES (?,?,?) ON CONFLICT DO NOTHING",id,material,role).execute(&mut *tx).await?;
        }
    }
    // Material and mentor details are read after the write lock, without holding it across a network call.
    let my_name = sqlx::query_scalar!("SELECT name FROM profile")
        .fetch_one(&mut *tx)
        .await?;
    for candidate in mentors {
        let letter = if form.writer == Writer::Template {
            let m = mentor::detail(&mut tx, candidate.id).await?;
            m.map(|m| {
                fill(
                    subject.unwrap_or_default(),
                    body.unwrap_or_default(),
                    &m,
                    my_name.as_deref(),
                )
            })
        } else {
            None
        };
        emails::create(
            &mut tx,
            id,
            candidate.id,
            letter
                .as_ref()
                .map(|l| (l.subject.as_str(), l.body_md.as_str())),
            &form.attachments,
            now,
        )
        .await?;
    }
    tx.commit().await?;
    Ok(id)
}

pub async fn pause(db: &SqlitePool, id: i64, now: i64) -> Result<()> {
    sqlx::query!("UPDATE task SET state = 'paused', generation_paused_at = ?, generation_pause_reason = NULL WHERE id = ? AND state <> 'stopped'", now,id).execute(db).await?;
    Ok(())
}
pub async fn resume(db: &SqlitePool, id: i64) -> Result<()> {
    sqlx::query!("UPDATE task SET state = 'active', generation_paused_at = NULL, generation_pause_reason = NULL WHERE id = ? AND state <> 'stopped'",id).execute(db).await?;
    Ok(())
}
pub async fn stop(db: &SqlitePool, id: i64, now: i64) -> Result<()> {
    let mut tx = db.begin().await?;
    sqlx::query!("UPDATE task SET state = 'stopped' WHERE id = ?", id)
        .execute(&mut *tx)
        .await?;
    emails::cancel(&mut tx, id, None, "任务停止", now).await?;
    tx.commit().await?;
    Ok(())
}
pub async fn delete(db: &SqlitePool, id: i64) -> Result<()> {
    let mut tx = db.begin().await?;
    // Acquire the write lock before checking again; no handover can slip through this transaction.
    sqlx::query!("UPDATE task SET name = name WHERE id = ?", id)
        .execute(&mut *tx)
        .await?;
    if sqlx::query_scalar!(
        "SELECT id FROM email WHERE task_id = ? AND state IN ('sending','cloud') LIMIT 1",
        id
    )
    .fetch_optional(&mut *tx)
    .await?
    .is_some()
    {
        return Err(Error::Invalid(
            "有正在发送的邮件，请发送结束后再删除。".into(),
        ));
    }
    emails::delete(&mut tx, id).await?;
    sqlx::query!("DELETE FROM task WHERE id = ?", id)
        .execute(&mut *tx)
        .await?;
    tx.commit().await?;
    Ok(())
}
