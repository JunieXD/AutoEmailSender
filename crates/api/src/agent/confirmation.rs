//! Rechecking compares exactly the description shown to the user.

use std::collections::BTreeSet;

use serde::{Serialize, de::DeserializeOwned};
use sqlx::{SqliteConnection, types::Json};
use ts_rs::TS;

use crate::{CallError, Context};

#[derive(Clone, PartialEq, Eq, Serialize, TS)]
pub struct Description {
    /// Only application-authored text and counts; object content belongs in the other fields.
    pub action: String,
    pub targets: Vec<String>,
    pub letters: Vec<ConfirmationLetter>,
}

impl Description {
    fn new(action: String, targets: Vec<String>) -> Self {
        Self {
            action,
            targets,
            letters: vec![],
        }
    }
}

fn parse<T: DeserializeOwned>(input: &serde_json::Value) -> Result<T, CallError> {
    serde_path_to_error::deserialize(input.clone()).map_err(CallError::bad_input)
}

#[derive(Clone, PartialEq, Eq, Serialize, TS)]
#[serde(tag = "kind", rename_all = "camelCase")]
pub enum SendAt {
    Now,
    At {
        at: i64,
    },
    Task {
        #[serde(rename = "taskId")]
        task_id: i64,
        schedule: email::schedule::Schedule,
    },
}

#[derive(Clone, PartialEq, Eq, Serialize, TS)]
#[serde(rename_all = "camelCase")]
pub struct QuotedMessage {
    pub message_id: Option<String>,
    pub subject: Option<String>,
    pub sent_at: i64,
    pub from_address: String,
    pub from_name: Option<String>,
    pub body_text: Option<String>,
    pub refs: Option<String>,
}

#[derive(Clone, PartialEq, Eq, Serialize, TS)]
#[serde(rename_all = "camelCase")]
pub struct ConfirmationLetter {
    pub mentor_id: i64,
    pub mentor_name: String,
    pub address: Option<String>,
    pub mailbox_id: Option<i64>,
    pub mailbox_address: Option<String>,
    pub send_at: SendAt,
    pub subject: String,
    pub body_md: String,
    pub attachments: Vec<email::SelectedAttachment>,
    /// The core appends this quote after rendering the new Markdown body.
    pub previous: Option<QuotedMessage>,
}

async fn mentor_targets(
    conn: &mut SqliteConnection,
    ids: &[i64],
) -> Result<Vec<String>, CallError> {
    let rows = sqlx::query!(
        r#"SELECT COALESCE(m.name, '找不到的导师（可能已被删除）') AS "name!: String",
            (SELECT address FROM mentor_email
                WHERE mentor_id = m.id ORDER BY position LIMIT 1) AS "address?: String"
        FROM json_each(?) selected
        LEFT JOIN mentor m ON m.id = selected.value
        ORDER BY CAST(selected.key AS INTEGER)"#,
        Json(ids)
    )
    .fetch_all(conn)
    .await?;
    Ok(rows
        .into_iter()
        .map(|row| {
            let address = row.address.as_deref().unwrap_or("未填写邮箱");
            format!("{}：{address}", row.name)
        })
        .collect())
}

async fn email_targets(conn: &mut SqliteConnection, ids: &[i64]) -> Result<Vec<String>, CallError> {
    let rows = sqlx::query!(
        r#"SELECT m.name,
            (SELECT address FROM mentor_email
                WHERE mentor_id = m.id ORDER BY position LIMIT 1) AS "address?: String",
            count(*) AS "count!: i64",
            json_group_array(e.planned_at ORDER BY e.id) AS "times!: Json<Vec<Option<i64>>>"
        FROM email e JOIN mentor m ON m.id = e.mentor_id
        WHERE e.id IN (SELECT value FROM json_each(?))
        GROUP BY m.id ORDER BY m.id"#,
        Json(ids)
    )
    .fetch_all(conn)
    .await?;
    Ok(rows
        .into_iter()
        .map(|row| {
            let address = row.address.as_deref().unwrap_or("未填写邮箱");
            format!(
                "{}：{address}（{} 封）；原定发送时间：{}",
                row.name,
                row.count,
                sending_times(row.times.0)
            )
        })
        .collect())
}

async fn task_target(conn: &mut SqliteConnection, id: i64) -> Result<String, CallError> {
    Ok(
        sqlx::query_scalar!("SELECT name FROM task WHERE id = ?", id)
            .fetch_optional(conn)
            .await?
            .unwrap_or_else(|| "找不到的任务（可能已被删除）".into()),
    )
}

async fn mailbox_target(conn: &mut SqliteConnection, id: i64) -> Result<String, CallError> {
    Ok(
        sqlx::query_scalar!("SELECT address FROM mailbox WHERE id = ?", id)
            .fetch_optional(conn)
            .await?
            .unwrap_or_else(|| "找不到的邮箱（可能已被删除）".into()),
    )
}

async fn model_target(conn: &mut SqliteConnection, id: i64) -> Result<String, CallError> {
    Ok(sqlx::query_scalar!(
        r#"SELECT coalesce(name, model) AS "name!: String" FROM model_config WHERE id = ?"#,
        id
    )
    .fetch_optional(conn)
    .await?
    .unwrap_or_else(|| "找不到的模型配置（可能已被删除）".into()))
}

async fn mailbox_emails(conn: &mut SqliteConnection, id: i64) -> Result<Vec<i64>, CallError> {
    Ok(sqlx::query_scalar!(
        r#"SELECT e.id FROM email e
        JOIN email_own_mailbox own ON own.email_id = e.id
        WHERE e.state = 'approved' AND (own.mailbox_id = ?
            OR (own.mailbox_id IS NULL AND e.kind = 'first'))
            AND (e.task_id IS NULL OR e.task_id IN
                (SELECT id FROM task WHERE state = 'active' AND generation_paused_at IS NULL))
        ORDER BY e.id"#,
        id
    )
    .fetch_all(conn)
    .await?)
}

async fn sending(
    conn: &mut SqliteConnection,
    action: &str,
    target: String,
    ids: &[i64],
) -> Result<Description, CallError> {
    let mut targets = email_targets(conn, ids).await?;
    let action = format!(
        "{action}：会发出 {} 封邮件，给 {} 位导师",
        ids.len(),
        targets.len()
    );
    targets.insert(0, target);
    Ok(Description::new(action, targets))
}

fn display_time(at: i64) -> String {
    chrono::DateTime::from_timestamp_millis(at)
        .map(|at| {
            at.with_timezone(&chrono::Local)
                .format("%Y年%-m月%-d日 %H:%M")
                .to_string()
        })
        .unwrap_or_else(|| format!("无法识别的时间：{at}"))
}

fn sending_times(times: Vec<Option<i64>>) -> String {
    times
        .into_iter()
        .map(|at| at.map_or_else(|| "按任务的发送安排".into(), display_time))
        .collect::<Vec<_>>()
        .join("、")
}

async fn letter(
    conn: &mut SqliteConnection,
    mentor_id: i64,
    subject: String,
    body_md: String,
    attachments: &[i64],
    send_at: SendAt,
    previous: Option<QuotedMessage>,
) -> Result<ConfirmationLetter, CallError> {
    let mentor = sqlx::query!(
        r#"SELECT name, mailbox_id,
            (SELECT address FROM mailbox WHERE id = mentor.mailbox_id) AS "mailbox_address?: String",
            (SELECT address FROM mentor_email WHERE mentor_id = mentor.id
                ORDER BY position LIMIT 1) AS "address?: String"
        FROM mentor WHERE id = ?"#,
        mentor_id
    )
    .fetch_one(&mut *conn)
    .await?;
    let files = email::select_attachments(conn, attachments).await?;
    Ok(ConfirmationLetter {
        mentor_id,
        mentor_name: mentor.name,
        address: mentor.address,
        mailbox_id: mentor.mailbox_id,
        mailbox_address: mentor.mailbox_address,
        send_at,
        subject,
        body_md,
        attachments: files,
        previous,
    })
}

async fn previous(
    conn: &mut SqliteConnection,
    mentor_id: i64,
    reply_to: &str,
    follow_up: bool,
) -> Result<Option<QuotedMessage>, CallError> {
    if follow_up {
        Ok(
            email::thread::previous(conn, mentor_id, Some(reply_to), true)
                .await?
                .map(|row| QuotedMessage {
                    message_id: row.message_id,
                    subject: row.subject,
                    sent_at: row.sent_at,
                    from_address: row.from_address,
                    from_name: row.from_name,
                    body_text: row.body_text,
                    refs: row.refs,
                }),
        )
    } else {
        Ok(sqlx::query_as!(
            QuotedMessage,
            r#"SELECT m.message_id, m.subject, m.sent_at, m.from_address, m.from_name,
                m.body_text, m.refs
            FROM message m JOIN message_mentor mm ON mm.message_id = m.id
            WHERE mm.mentor_id = ? AND m.message_id = ? AND m.kind = 'reply'"#,
            mentor_id,
            reply_to
        )
        .fetch_optional(conn)
        .await?)
    }
}

fn object(action: &str, target: String) -> Description {
    Description::new(action.into(), vec![target])
}

pub(crate) async fn confirmation(
    ctx: &Context,
    op: &str,
    input: &serde_json::Value,
) -> Result<Option<Description>, CallError> {
    let mut tx = ctx.db.begin().await?;
    describe(&mut tx, op, input).await
}

async fn describe(
    conn: &mut SqliteConnection,
    op: &str,
    input: &serde_json::Value,
) -> Result<Option<Description>, CallError> {
    let description = match op {
        "approveTaskEmails" => {
            let input: task::TaskApprovals = parse(input)?;
            let ids = input
                .emails
                .iter()
                .map(|email| email.id)
                .collect::<Vec<_>>();
            if ids.iter().collect::<BTreeSet<_>>().len() != ids.len() {
                return Err(CallError::invalid("同一封邮件不能重复批准。".into()));
            }
            let schedule = email::schedule::get(conn, input.task_id).await?.form();
            let mut description = Description::new(
                format!("发出 {} 封邮件", input.emails.len()),
                email_targets(conn, &ids).await?,
            );
            description
                .targets
                .push(task_target(conn, input.task_id).await?);
            for approval in input.emails {
                let current = sqlx::query!(
                    r#"SELECT mentor_id, kind, reply_to FROM email
                    WHERE id = ? AND task_id = ? AND state = 'review'"#,
                    approval.id,
                    input.task_id
                )
                .fetch_optional(&mut *conn)
                .await?
                .ok_or_else(|| CallError::invalid("草稿已不在任务里，请刷新后重新批准。".into()))?;
                let previous = if current.kind == "follow_up" {
                    previous(
                        conn,
                        current.mentor_id,
                        current
                            .reply_to
                            .as_deref()
                            .ok_or_else(|| CallError::internal("follow-up without parent"))?,
                        true,
                    )
                    .await?
                } else {
                    None
                };
                let send_at = match &schedule {
                    email::schedule::Schedule::Now => SendAt::Now,
                    _ => SendAt::Task {
                        task_id: input.task_id,
                        schedule: schedule.clone(),
                    },
                };
                let subject = match &previous {
                    Some(parent) => email::reply_subject(parent.subject.as_deref().unwrap_or("")),
                    None => approval.subject,
                };
                description.letters.push(
                    letter(
                        conn,
                        current.mentor_id,
                        subject,
                        approval.body_md,
                        &approval.attachments,
                        send_at,
                        previous,
                    )
                    .await?,
                );
            }
            description
        }
        "approveFirstEmail" => {
            let input: crate::FirstEmail = parse(input)?;
            let mut description = Description::new(
                "发出 1 封首封".into(),
                mentor_targets(conn, &[input.mentor_id]).await?,
            );
            description.letters.push(
                letter(
                    conn,
                    input.mentor_id,
                    input.subject.trim().into(),
                    input.body_md,
                    &input.attachments,
                    input.planned_at.map_or(SendAt::Now, |at| SendAt::At { at }),
                    None,
                )
                .await?,
            );
            description
        }
        "approveFollowUp" | "approveReply" => {
            let input: crate::ReplyEmail = parse(input)?;
            let previous = previous(
                conn,
                input.mentor_id,
                &input.reply_to,
                op == "approveFollowUp",
            )
            .await?;
            let subject = previous
                .as_ref()
                .map(|p| email::reply_subject(p.subject.as_deref().unwrap_or("")))
                .ok_or_else(|| {
                    CallError::invalid("回的那封信已不在往来里，请刷新后重新选择。".into())
                })?;
            let mut description = Description::new(
                if op == "approveFollowUp" {
                    "发出 1 封追问"
                } else {
                    "发出 1 封回复"
                }
                .into(),
                mentor_targets(conn, &[input.mentor_id]).await?,
            );
            description.letters.push(
                letter(
                    conn,
                    input.mentor_id,
                    subject,
                    input.body_md,
                    &input.attachments,
                    SendAt::Now,
                    previous,
                )
                .await?,
            );
            description
        }
        "sendEmailNow" | "resendEmail" | "retryEmail" => {
            let input: crate::EmailId = parse(input)?;
            let action = match op {
                "sendEmailNow" => "立即发出 1 封邮件",
                "resendEmail" => "立即重新发出 1 封邮件",
                _ => "重试发出 1 封邮件",
            };
            let mut targets = email_targets(conn, &[input.id]).await?;
            if op == "retryEmail" {
                let task = sqlx::query_scalar!("SELECT task_id FROM email WHERE id = ?", input.id)
                    .fetch_optional(&mut *conn)
                    .await?
                    .flatten();
                let schedule = match task {
                    Some(task) => email::schedule::get(conn, task).await?.form(),
                    None => email::schedule::Schedule::Now,
                };
                let timing = match schedule {
                    email::schedule::Schedule::Now => "立即发送".into(),
                    email::schedule::Schedule::Window(window) => format!(
                        "按任务的发送安排：{} 的 {}–{}，每个窗口最多 {} 封",
                        window.dates.join("、"),
                        window.start,
                        window.end,
                        window.per_window
                    ),
                };
                targets = targets
                    .into_iter()
                    .map(|target| format!("{target}；本次重试：{timing}"))
                    .collect();
            }
            Description::new(action.into(), targets)
        }
        "resumeTask" | "changeTaskSchedule" => {
            let id = parse::<crate::TaskId>(input)?.id;
            let ids = sqlx::query_scalar!(
                "SELECT id FROM email WHERE task_id = ? AND state = 'approved' ORDER BY id",
                id
            )
            .fetch_all(&mut *conn)
            .await?;
            let target = task_target(conn, id).await?;
            if op == "resumeTask" {
                sending(conn, "恢复 1 个任务的发信", target, &ids).await?
            } else {
                let input: task::ScheduleEdit = parse(input)?;
                let target = match input.schedule {
                    email::schedule::Schedule::Now => format!("{target}：改为立即发送"),
                    email::schedule::Schedule::Window(window) => format!(
                        "{target}：改为 {} 的 {}–{}，每个窗口最多 {} 封",
                        window.dates.join("、"),
                        window.start,
                        window.end,
                        window.per_window
                    ),
                };
                sending(conn, "更改 1 个任务的发送时间", target, &ids).await?
            }
        }
        "deleteTask" => {
            let input: crate::TaskId = parse(input)?;
            let count = sqlx::query_scalar!(
                r#"SELECT count(*) AS "count!: i64" FROM email
                WHERE task_id = ? AND NOT (
                    state IN ('sending', 'cloud', 'maybe_sent', 'sent', 'bounced')
                    OR (state = 'canceled' AND sent_at IS NOT NULL))"#,
                input.id
            )
            .fetch_one(&mut *conn)
            .await?;
            object(
                &format!("永久删除 1 个任务和其中 {count} 封没发出的邮件（包括已批准的）"),
                task_target(conn, input.id).await?,
            )
        }
        "resumeMailbox" => {
            let input: crate::MailboxId = parse(input)?;
            let ids = mailbox_emails(conn, input.id).await?;
            let target = mailbox_target(conn, input.id).await?;
            sending(conn, "恢复 1 个邮箱的发信", target, &ids).await?
        }
        "updateMailbox" => {
            let input: crate::MailboxEdit = parse(input)?;
            let fields = input.form.check().map_err(CallError::invalid)?;
            let row = sqlx::query!(
                "SELECT address, paused_until FROM mailbox WHERE id = ?",
                input.id
            )
            .fetch_optional(&mut *conn)
            .await?;
            let Some(row) = row else {
                return Ok(None);
            };
            let after: &str = fields.address.as_ref();
            let resumes = row.paused_until.is_some_and(|until| until > crate::now());
            let changed = row.address != after;
            if !resumes && !changed {
                return Ok(None);
            }
            let ids = mailbox_emails(conn, input.id).await?;
            if !resumes && ids.is_empty() {
                return Ok(None);
            }
            let target = if changed {
                format!("{} → {after}", row.address)
            } else {
                row.address
            };
            let action = if resumes {
                "改 1 个邮箱的设置并恢复发信"
            } else {
                "更改 1 个邮箱的发件地址"
            };
            sending(conn, action, target, &ids).await?
        }
        "deleteMailbox" => {
            let input: crate::MailboxId = parse(input)?;
            let counts = sqlx::query!(
                r#"SELECT
                    (SELECT count(*) FROM message WHERE mailbox_id = ?1) AS "messages!: i64",
                    (SELECT count(*) FROM reply_processing WHERE message_id IN
                        (SELECT id FROM message WHERE mailbox_id = ?1)) AS "processing!: i64",
                    (SELECT count(*) FROM mentor WHERE mailbox_id = ?1) AS "mentors!: i64""#,
                input.id
            )
            .fetch_one(&mut *conn)
            .await?;
            object(
                &format!(
                    "永久删除 1 个邮箱和其中 {} 封已收取邮件、{} 项回信处理；{} 位导师改为自动选择发件邮箱",
                    counts.messages, counts.processing, counts.mentors
                ),
                mailbox_target(conn, input.id).await?,
            )
        }
        "rescheduleEmail" => {
            let input: crate::RescheduleEmail = parse(input)?;
            let at = display_time(input.planned_at);
            let targets = email_targets(conn, &[input.id])
                .await?
                .into_iter()
                .map(|target| format!("{target}；改为：{at}"))
                .collect();
            Description::new("更改 1 封邮件的发送时间".into(), targets)
        }
        "deleteMentors" => {
            let input: crate::MentorIds = parse(input)?;
            let ids = sqlx::query_scalar!(
                r#"SELECT id FROM mentor
                WHERE id IN (SELECT value FROM json_each(?)) AND trashed_at IS NOT NULL
                ORDER BY id"#,
                Json(&input.ids)
            )
            .fetch_all(&mut *conn)
            .await?;
            let counts = sqlx::query!(
                r#"WITH doomed AS (SELECT value AS id FROM json_each(?1))
                SELECT
                    (SELECT count(*) FROM email WHERE mentor_id IN doomed) AS "emails!: i64",
                    (SELECT count(*) FROM todo WHERE mentor_id IN doomed) AS "todos!: i64",
                    (SELECT count(*) FROM mentor_tag WHERE mentor_id IN doomed) AS "tags!: i64",
                    (SELECT count(*) FROM library_update WHERE mentor_id IN doomed) AS "updates!: i64",
                    (SELECT count(*) FROM message
                        WHERE id IN (SELECT message_id FROM message_mentor WHERE mentor_id IN doomed)
                        AND NOT EXISTS (SELECT 1 FROM message_mentor mm
                            WHERE mm.message_id = message.id AND mm.mentor_id NOT IN doomed))
                        AS "messages!: i64""#,
                Json(&ids)
            )
            .fetch_one(&mut *conn)
            .await?;
            Description::new(
                format!(
                    "永久删除 {} 位导师、{} 封发件、{} 封已收取邮件、{} 条待办和 {} 条资料更新，移除 {} 项标签关联",
                    ids.len(),
                    counts.emails,
                    counts.messages,
                    counts.todos,
                    counts.updates,
                    counts.tags
                ),
                mentor_targets(conn, &ids).await?,
            )
        }
        "mergeMentors" => {
            let input: crate::MentorMerge = parse(input)?;
            let drafts = mentor::merge::conflicting_drafts(conn, input.keep, input.other).await?;
            let canceled = sqlx::query_scalar!(
                r#"SELECT count(*) AS "count!: i64" FROM email
                WHERE mentor_id IN (?1, ?2) AND task_id IS NOT NULL
                    AND state = 'canceled' AND sent_at IS NULL
                    AND EXISTS (SELECT 1 FROM email k WHERE k.task_id = email.task_id
                        AND k.mentor_id IN (?1, ?2) AND k.mentor_id <> email.mentor_id
                        AND (email.mentor_id = ?2 OR k.state IN ('maybe_sent', 'sent', 'bounced')
                            OR (k.state = 'canceled' AND k.sent_at IS NOT NULL)))"#,
                input.keep,
                input.other
            )
            .fetch_one(&mut *conn)
            .await?;
            Description::new(
                format!(
                    "合并 2 位导师：第 1 位并入第 2 位，丢弃 {} 封重复草稿和 {canceled} 条重复的已取消任务邮件记录",
                    drafts.len()
                ),
                mentor_targets(conn, &[input.other, input.keep]).await?,
            )
        }
        "deleteTemplate" => {
            let input: crate::TemplateId = parse(input)?;
            let name = sqlx::query_scalar!("SELECT name FROM template WHERE id = ?", input.id)
                .fetch_optional(conn)
                .await?
                .unwrap_or_else(|| "找不到的模板（可能已被删除）".into());
            object("永久删除 1 个模板", name)
        }
        "deleteMaterial" => {
            let input: crate::MaterialId = parse(input)?;
            let row = sqlx::query!(
                r#"SELECT file_name,
                    (SELECT count(*) FROM email_attachment a JOIN email e ON e.id = a.email_id
                        WHERE a.material_id = material.id
                        AND e.state IN ('generating', 'generation_failed', 'review')) AS "drafts!: i64",
                    (SELECT count(*) FROM task_material WHERE material_id = material.id) AS "tasks!: i64"
                FROM material WHERE id = ?"#,
                input.id
            )
            .fetch_optional(conn)
            .await?;
            match row {
                Some(row) => object(
                    &format!(
                        "永久删除 1 份材料，移除 {} 封草稿的附件和 {} 项任务材料关联",
                        row.drafts, row.tasks
                    ),
                    row.file_name,
                ),
                None => object("永久删除 1 份材料", "找不到的材料（可能已被删除）".into()),
            }
        }
        "deleteCrawl" => {
            let input: crate::CrawlId = parse(input)?;
            let row = sqlx::query!(
                r#"SELECT university, school,
                    (SELECT count(*) FROM candidate WHERE crawl_id = crawl.id
                        AND review = 'pending' AND mentor_id IS NULL) AS "candidates!: i64",
                    (SELECT count(*) FROM crawl_page WHERE crawl_id = crawl.id
                        OR candidate_id IN (SELECT id FROM candidate WHERE crawl_id = crawl.id
                            AND review = 'pending' AND mentor_id IS NULL)) AS "pages!: i64",
                    (SELECT count(*) FROM crawl_run WHERE crawl_id = crawl.id) AS "runs!: i64"
                FROM crawl WHERE id = ?"#,
                input.id
            )
            .fetch_optional(conn)
            .await?;
            match row {
                Some(row) => {
                    let parts = row
                        .university
                        .into_iter()
                        .chain(row.school)
                        .collect::<Vec<_>>();
                    let name = if parts.is_empty() {
                        "导师抓取".into()
                    } else {
                        parts.join(" ")
                    };
                    object(
                        &format!(
                            "永久删除 1 项抓取记录、{} 位未审核候选、{} 个抓取页面和 {} 项运行记录",
                            row.candidates, row.pages, row.runs
                        ),
                        name,
                    )
                }
                None => object(
                    "永久删除 1 项抓取记录",
                    "找不到的抓取（可能已被删除）".into(),
                ),
            }
        }
        "deleteTag" => {
            let input: crate::TagId = parse(input)?;
            let row = sqlx::query!(
                r#"SELECT name,
                    (SELECT count(*) FROM mentor_tag WHERE tag_id = tag.id) AS "mentors!: i64"
                FROM tag WHERE id = ?"#,
                input.id
            )
            .fetch_optional(conn)
            .await?;
            match row {
                Some(row) => object(
                    &format!("永久删除 1 个标签，移除 {} 位导师的标签关联", row.mentors),
                    row.name,
                ),
                None => object("永久删除 1 个标签", "找不到的标签（可能已被删除）".into()),
            }
        }
        "deleteTodo" => {
            let input: crate::TodoId = parse(input)?;
            let name = sqlx::query_scalar!(
                r#"SELECT m.name || '：' || t.text AS "name!: String"
                FROM todo t JOIN mentor m ON m.id = t.mentor_id WHERE t.id = ?"#,
                input.id
            )
            .fetch_optional(conn)
            .await?
            .unwrap_or_else(|| "找不到的待办（可能已被删除）".into());
            object("永久删除 1 条待办", name)
        }
        "deleteModelConfig" => {
            let input: crate::ModelConfigId = parse(input)?;
            object("永久删除 1 个模型配置", model_target(conn, input.id).await?)
        }
        "agreeModel" => {
            let input: crate::ModelAgreement = parse(input)?;
            let model = model_target(conn, input.id).await?;
            let (provider, address) = if input.provider == model::providers::CUSTOM {
                (
                    "自定义模型服务",
                    input.base_url.unwrap_or_else(|| "未填写服务地址".into()),
                )
            } else {
                match model::providers::find(&input.provider) {
                    Some(provider) => (provider.name, provider.base_url.into()),
                    None => ("找不到的模型服务商", input.provider),
                }
            };
            object(
                "同意把数据发给这个模型服务",
                format!("{provider}（{model}）：{address}"),
            )
        }
        "setMentorMailbox" => {
            let input: crate::MentorMailbox = parse(input)?;
            let row = sqlx::query!("SELECT mailbox_id FROM mentor WHERE id = ?", input.id)
                .fetch_optional(&mut *conn)
                .await?;
            let Some(row) = row else { return Ok(None) };
            if row.mailbox_id == input.mailbox_id {
                return Ok(None);
            }
            let ids = sqlx::query_scalar!(
                "SELECT id FROM email WHERE mentor_id = ? AND state = 'approved' ORDER BY id",
                input.id
            )
            .fetch_all(&mut *conn)
            .await?;
            if ids.is_empty() {
                return Ok(None);
            }
            let before = match row.mailbox_id {
                Some(id) => mailbox_target(conn, id).await?,
                None => "自动选择邮箱".into(),
            };
            let after = match input.mailbox_id {
                Some(id) => mailbox_target(conn, id).await?,
                None => "自动选择邮箱".into(),
            };
            let targets = email_targets(conn, &ids)
                .await?
                .into_iter()
                .map(|target| format!("{target}；发件邮箱：{before} → {after}"))
                .collect();
            Description::new(
                format!(
                    "更改 1 位导师的发件邮箱：影响已批准还没发出的 {} 封邮件",
                    ids.len()
                ),
                targets,
            )
        }
        "saveFirstDraft" | "saveReplyDraft" => {
            let (mentor, kind) = if op == "saveFirstDraft" {
                let input: crate::FirstDraftEdit = parse(input)?;
                if !input.subject.trim().is_empty() || !input.body_md.trim().is_empty() {
                    return Ok(None);
                }
                (input.mentor_id, "first")
            } else {
                let input: crate::ReplyDraftEdit = parse(input)?;
                if !input.body_md.trim().is_empty() || !input.attachments.is_empty() {
                    return Ok(None);
                }
                (input.mentor_id, "reply")
            };
            // The draft is shown whole: an edit within the same minute changes its content, not its
            // saved time as shown.
            let rows = sqlx::query!(
                r#"SELECT m.name, e.subject, e.body_md, e.updated_at, (
                    SELECT group_concat('附件：' || file_name || '（' || size || ' 字节）', char(10))
                    FROM email_attachment
                    WHERE email_id = e.id
                ) AS "attachments?: String"
                FROM email e JOIN mentor m ON m.id = e.mentor_id
                WHERE e.mentor_id = ? AND e.kind = ? AND e.state = 'review'
                    AND (? <> 'first' OR e.task_id IS NULL)
                ORDER BY e.id"#,
                mentor,
                kind,
                kind
            )
            .fetch_all(conn)
            .await?;
            if rows.is_empty() {
                return Ok(None);
            }
            let kind = if kind == "first" { "首封" } else { "回复" };
            let targets = rows
                .into_iter()
                .map(|row| {
                    let subject = row.subject.as_deref().unwrap_or("（无主题）");
                    // One attachment a line: a file name may hold any separator.
                    let attachments = row.attachments.as_deref().unwrap_or("附件：无");
                    format!(
                        "{}的{kind}草稿《{subject}》，{} 保存\n{attachments}\n{}",
                        row.name,
                        display_time(row.updated_at),
                        row.body_md.as_deref().unwrap_or_default()
                    )
                })
                .collect();
            Description::new(format!("删除 1 封{kind}草稿"), targets)
        }
        "updateMentor" => {
            let input: crate::MentorEdit = parse(input)?;
            let fields = input.form.check().map_err(CallError::invalid)?;
            return changed_recipient(conn, input.id, fields.emails.first().map(String::as_str))
                .await;
        }
        "addMentorEmail" => {
            let input: crate::MentorAddress = parse(input)?;
            let address = input.address.trim();
            if !mentor::is_email(address) {
                return Err(CallError::invalid(format!("邮箱地址“{address}”格式不正确")));
            }
            let addresses = sqlx::query_scalar!(
                "SELECT address FROM mentor_email WHERE mentor_id = ? ORDER BY position",
                input.id
            )
            .fetch_all(&mut *conn)
            .await?;
            if addresses
                .iter()
                .any(|old| old.eq_ignore_ascii_case(address))
                || mentor::place(&addresses, address) != 0
            {
                return Ok(None);
            }
            return changed_recipient(conn, input.id, Some(address)).await;
        }
        "importMentors" => {
            let input: crate::ImportMentors = parse(input)?;
            let sheets = crate::read_import(&input.file).await?;
            let (_, changes) =
                mentor::import::calculate(conn, &sheets, &input.file.setup, &input.decisions)
                    .await?;
            return imported_recipients(conn, &changes).await;
        }
        "acceptCandidates" => {
            let input: crawl::review::ReviewInput = parse(input)?;
            let plan = crawl::review::plan(conn, &input).await?;
            return imported_recipients(conn, &plan.changes).await;
        }
        _ => return Ok(None),
    };
    Ok(Some(description))
}

async fn changed_recipient(
    conn: &mut SqliteConnection,
    mentor: i64,
    address: Option<&str>,
) -> Result<Option<Description>, CallError> {
    let row = sqlx::query!(
        r#"SELECT m.name,
            (SELECT address FROM mentor_email
                WHERE mentor_id = m.id ORDER BY position LIMIT 1) AS "old_address?: String",
            COUNT(*) AS "count!: i64",
            json_group_array(e.planned_at ORDER BY e.id) AS "times!: Json<Vec<Option<i64>>>"
        FROM mentor m
        JOIN email e ON e.mentor_id = m.id
        WHERE m.id = ?1 AND e.state = 'approved'
            AND (SELECT address FROM mentor_email
                WHERE mentor_id = ?1 ORDER BY position LIMIT 1) IS NOT ?2 COLLATE NOCASE
        GROUP BY m.id"#,
        mentor,
        address
    )
    .fetch_optional(&mut *conn)
    .await?;
    let Some(row) = row else { return Ok(None) };
    let action = if address.is_some() {
        format!(
            "改 1 位导师的发信地址：已批准还没发出的 {} 封会发到新地址",
            row.count
        )
    } else {
        format!(
            "移除 1 位导师的发信地址：已批准还没发出的 {} 封将没有收件地址",
            row.count
        )
    };
    let before = row.old_address.as_deref().unwrap_or("未填写邮箱");
    let after = address.unwrap_or("未填写邮箱");
    Ok(Some(object(
        &action,
        format!(
            "{}：{before} → {after}（{} 封）；原定发送时间：{}",
            row.name,
            row.count,
            sending_times(row.times.0)
        ),
    )))
}

async fn imported_recipients(
    conn: &mut sqlx::SqliteConnection,
    changes: &[mentor::intake::Change],
) -> Result<Option<Description>, CallError> {
    let mentors = Json(
        changes
            .iter()
            .filter_map(mentor::intake::Change::changed_recipient)
            .collect::<Vec<_>>(),
    );
    let rows = sqlx::query!(
        r#"SELECT m.name, json_extract(selected.value, '$[1]') AS "address?: String",
            (SELECT address FROM mentor_email
                WHERE mentor_id = m.id ORDER BY position LIMIT 1) AS "old_address?: String",
            COUNT(*) AS "count!: i64",
            json_group_array(e.planned_at ORDER BY e.id) AS "times!: Json<Vec<Option<i64>>>"
        FROM json_each(?) selected
        JOIN mentor m ON m.id = json_extract(selected.value, '$[0]')
        JOIN email e ON e.mentor_id = m.id
        WHERE e.state = 'approved'
        GROUP BY m.id
        ORDER BY m.id"#,
        mentors
    )
    .fetch_all(&mut *conn)
    .await?;
    if rows.is_empty() {
        return Ok(None);
    }
    let count: i64 = rows.iter().map(|row| row.count).sum();
    let action = format!(
        "改 {} 位导师的发信地址：影响已批准还没发出的 {count} 封邮件",
        rows.len()
    );
    let targets = rows
        .into_iter()
        .map(|row| {
            let before = row.old_address.as_deref().unwrap_or("未填写邮箱");
            let after = row.address.as_deref().unwrap_or("未填写邮箱");
            format!(
                "{}：{before} → {after}（{} 封）；原定发送时间：{}",
                row.name,
                row.count,
                sending_times(row.times.0)
            )
        })
        .collect();
    Ok(Some(Description::new(action, targets)))
}

#[cfg(test)]
mod tests {
    use std::collections::BTreeSet;

    use crate::agent::OperationConfirmation;

    #[test]
    fn catalog_confirmation_names_match_fixed_and_effect_based_rules() {
        let catalog = crate::operation_catalog();
        let always: BTreeSet<_> = catalog
            .iter()
            .filter(|op| matches!(op.confirmation, OperationConfirmation::Always))
            .map(|op| op.name.as_str())
            .collect();
        // These sets track the fixed and effect-based branches of `confirmation` above.
        assert_eq!(
            always,
            BTreeSet::from([
                "agreeModel",
                "approveFirstEmail",
                "approveFollowUp",
                "approveReply",
                "approveTaskEmails",
                "changeTaskSchedule",
                "deleteCrawl",
                "deleteMailbox",
                "deleteMaterial",
                "deleteMentors",
                "deleteModelConfig",
                "deleteTag",
                "deleteTask",
                "deleteTemplate",
                "deleteTodo",
                "mergeMentors",
                "rescheduleEmail",
                "resendEmail",
                "resumeMailbox",
                "resumeTask",
                "retryEmail",
                "sendEmailNow",
            ])
        );
        let conditional: BTreeSet<_> = catalog
            .iter()
            .filter(|op| matches!(op.confirmation, OperationConfirmation::Conditional(_)))
            .map(|op| op.name.as_str())
            .collect();
        assert_eq!(
            conditional,
            BTreeSet::from([
                "acceptCandidates",
                "addMentorEmail",
                "importMentors",
                "saveFirstDraft",
                "saveReplyDraft",
                "setMentorMailbox",
                "updateMailbox",
                "updateMentor",
            ])
        );
    }
}
