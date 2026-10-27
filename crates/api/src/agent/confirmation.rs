//! Confirmation effects remain complete for rechecking; presentation belongs to the caller.

use std::collections::BTreeSet;

use serde::{Serialize, de::DeserializeOwned};
use sqlx::{SqliteConnection, types::Json};
use ts_rs::TS;

use crate::{CallError, Context};

mod originals;
use originals::Original;

#[derive(Clone, PartialEq, Serialize, TS)]
pub struct Description {
    /// Only application-authored text and counts; object content belongs in the other fields.
    pub action: String,
    pub targets: Vec<String>,
    pub letters: Vec<ConfirmationLetter>,
    pub emails: Vec<EmailOriginal>,
    pub originals: Vec<Original>,
}

impl Description {
    fn new(action: String, targets: Vec<String>) -> Self {
        Self {
            action,
            targets,
            letters: vec![],
            emails: vec![],
            originals: vec![],
        }
    }
}

fn parse<T: DeserializeOwned>(input: &serde_json::Value) -> Result<T, CallError> {
    serde_path_to_error::deserialize(input.clone()).map_err(CallError::bad_input)
}

#[derive(Clone, PartialEq, Eq, Serialize, TS)]
#[serde(rename_all = "camelCase")]
pub struct ConfirmationAttachment {
    pub material_id: Option<i64>,
    pub file_name: String,
    pub size: i64,
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
    pub attachments: Vec<ConfirmationAttachment>,
    /// The core appends this quote after rendering the new Markdown body.
    pub previous: Option<QuotedMessage>,
}

#[derive(Clone, PartialEq, Eq, Serialize, TS)]
#[serde(rename_all = "camelCase")]
pub struct EmailOriginal {
    pub id: i64,
    pub mentor_id: i64,
    pub mentor_name: String,
    pub address: Option<String>,
    pub trashed_at: Option<i64>,
    pub task_id: Option<i64>,
    pub state: String,
    pub kind: String,
    pub reply_to: Option<String>,
    pub subject: Option<String>,
    pub body_md: Option<String>,
    pub html: Option<String>,
    pub text: Option<String>,
    pub refs: Option<String>,
    pub planned_at: Option<i64>,
    pub latest_at: Option<i64>,
    pub manual_schedule: bool,
    pub approval_order: Option<i64>,
    pub updated_at: i64,
    pub sent_at: Option<i64>,
    pub mailbox_id: Option<i64>,
    pub mailbox_address: Option<String>,
    pub attachments: Vec<ConfirmationAttachment>,
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

async fn emails(conn: &mut SqliteConnection, ids: &[i64]) -> Result<Vec<EmailOriginal>, CallError> {
    let rows = sqlx::query!(
        r#"SELECT e.id, e.mentor_id, m.name AS mentor_name, m.trashed_at,
            (SELECT address FROM mentor_email WHERE mentor_id = m.id
                ORDER BY position LIMIT 1) AS "address?: String",
            e.task_id, e.state, e.kind, e.reply_to, e.subject, e.body_md, e.html, e.text, e.refs,
            e.planned_at, e.latest_at, e.manual_schedule AS "manual_schedule: bool",
            e.approval_order, e.updated_at, e.sent_at, own.mailbox_id, b.address AS mailbox_address
        FROM email e
        JOIN mentor m ON m.id = e.mentor_id
        JOIN email_own_mailbox own ON own.email_id = e.id
        LEFT JOIN mailbox b ON b.id = own.mailbox_id
        WHERE e.id IN (SELECT value FROM json_each(?))
        ORDER BY e.id"#,
        Json(ids)
    )
    .fetch_all(&mut *conn)
    .await?;
    let mut originals = Vec::new();
    for row in rows {
        let attachments = sqlx::query_as!(
            ConfirmationAttachment,
            r#"SELECT material_id, file_name AS "file_name!", size FROM email_attachment
            WHERE email_id = ? ORDER BY file_name"#,
            row.id
        )
        .fetch_all(&mut *conn)
        .await?;
        originals.push(EmailOriginal {
            id: row.id,
            mentor_id: row.mentor_id,
            mentor_name: row.mentor_name,
            address: row.address,
            trashed_at: row.trashed_at,
            task_id: row.task_id,
            state: row.state,
            kind: row.kind,
            reply_to: row.reply_to,
            subject: row.subject,
            body_md: row.body_md,
            html: row.html,
            text: row.text,
            refs: row.refs,
            planned_at: row.planned_at,
            latest_at: row.latest_at,
            manual_schedule: row.manual_schedule,
            approval_order: row.approval_order,
            updated_at: row.updated_at,
            sent_at: row.sent_at,
            mailbox_id: row.mailbox_id,
            mailbox_address: row.mailbox_address,
            attachments,
        });
    }
    Ok(originals)
}

fn email_targets(emails: &[EmailOriginal]) -> Vec<String> {
    emails
        .iter()
        .map(|email| {
            let address = email.address.as_deref().unwrap_or("未填写邮箱");
            format!("{}：{address}", email.mentor_name)
        })
        .collect()
}

async fn task_target(
    conn: &mut SqliteConnection,
    id: i64,
    originals: &mut Vec<Original>,
) -> Result<String, CallError> {
    let row = sqlx::query!(
        r#"SELECT name, state, kind, writer, model_config_id, template_id,
            template_subject, template_body_md, generation_paused_at, generation_pause_reason,
            schedule, window_dates, window_start, window_end, per_window,
            (SELECT json_group_array(json_array(material_id, role)) FROM
                (SELECT material_id, role FROM task_material WHERE task_id = task.id
                    ORDER BY material_id, role)) AS "materials!: String"
        FROM task WHERE id = ?"#,
        id
    )
    .fetch_optional(conn)
    .await?;
    Ok(match row {
        Some(row) => {
            let name = row.name.clone();
            originals.push(Original::Task {
                id,
                name: row.name,
                state: row.state,
                kind: row.kind,
                writer: row.writer,
                model_config_id: row.model_config_id,
                template_id: row.template_id,
                template_subject: row.template_subject,
                template_body_md: row.template_body_md,
                generation_paused_at: row.generation_paused_at,
                generation_pause_reason: row.generation_pause_reason,
                materials: serde_json::from_str(&row.materials).map_err(CallError::internal)?,
                schedule: row.schedule,
                window_dates: row.window_dates,
                window_start: row.window_start,
                window_end: row.window_end,
                per_window: row.per_window,
            });
            name
        }
        None => "找不到的任务（可能已被删除）".into(),
    })
}

async fn mailbox_target(
    conn: &mut SqliteConnection,
    id: i64,
    originals: &mut Vec<Original>,
) -> Result<String, CallError> {
    let row = sqlx::query!(
        r#"SELECT address, sender_name, provider, smtp_host, smtp_port, smtp_security,
            imap_host, imap_port, imap_security, daily_limit, min_interval_s, rotation_order,
            paused_until, pause_reason
        FROM mailbox WHERE id = ?"#,
        id
    )
    .fetch_optional(conn)
    .await?;
    Ok(match row {
        Some(row) => {
            let address = row.address.clone();
            originals.push(Original::Mailbox {
                id,
                address: row.address,
                sender_name: row.sender_name,
                provider: row.provider,
                smtp_host: row.smtp_host,
                smtp_port: row.smtp_port,
                smtp_security: row.smtp_security,
                imap_host: row.imap_host,
                imap_port: row.imap_port,
                imap_security: row.imap_security,
                daily_limit: row.daily_limit,
                min_interval_s: row.min_interval_s,
                rotation_order: row.rotation_order,
                paused_until: row.paused_until,
                pause_reason: row.pause_reason,
            });
            address
        }
        None => "找不到的邮箱（可能已被删除）".into(),
    })
}

async fn model_target(
    conn: &mut SqliteConnection,
    id: i64,
    originals: &mut Vec<Original>,
) -> Result<String, CallError> {
    let row = sqlx::query!(
        r#"SELECT name, provider, base_url, model, agreed, input_price, output_price,
            (SELECT count(*) FROM task WHERE model_config_id = model_config.id) AS "tasks!: i64",
            (SELECT count(*) FROM crawl WHERE model_config_id = model_config.id) AS "crawls!: i64"
        FROM model_config WHERE id = ?"#,
        id
    )
    .fetch_optional(conn)
    .await?;
    Ok(match row {
        Some(row) => {
            let name = row.name.clone().unwrap_or_else(|| row.model.clone());
            originals.push(Original::Model {
                id,
                name: row.name,
                model: row.model,
                provider: row.provider,
                base_url: row.base_url,
                agreed: row.agreed,
                input_price: row.input_price,
                output_price: row.output_price,
                tasks: row.tasks,
                crawls: row.crawls,
            });
            name
        }
        None => "找不到的模型配置（可能已被删除）".into(),
    })
}

async fn task_emails(
    conn: &mut SqliteConnection,
    id: i64,
) -> Result<Vec<EmailOriginal>, CallError> {
    let ids = sqlx::query_scalar!("SELECT id FROM email WHERE task_id = ? ORDER BY id", id)
        .fetch_all(&mut *conn)
        .await?;
    emails(conn, &ids).await
}

async fn mailbox_emails(
    conn: &mut SqliteConnection,
    id: i64,
) -> Result<Vec<EmailOriginal>, CallError> {
    let ids = sqlx::query_scalar!(
        r#"SELECT e.id FROM email e
        JOIN email_own_mailbox own ON own.email_id = e.id
        WHERE e.state = 'approved' AND (own.mailbox_id = ?
            OR (own.mailbox_id IS NULL AND e.kind = 'first'))
        ORDER BY e.id"#,
        id
    )
    .fetch_all(&mut *conn)
    .await?;
    emails(conn, &ids).await
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
    let mut files = Vec::new();
    for id in attachments {
        files.push(
            sqlx::query_as!(
                ConfirmationAttachment,
                r#"SELECT id AS material_id, file_name, size FROM material WHERE id = ?"#,
                id
            )
            .fetch_optional(&mut *conn)
            .await?
            .ok_or_else(|| {
                CallError::invalid("有附件的材料已被删除。重新选择附件后再发送。".into())
            })?,
        );
    }
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
    let mut description = describe(&mut tx, op, input).await?;
    if let Some(description) = &mut description {
        for task in description
            .emails
            .iter()
            .filter_map(|e| e.task_id)
            .collect::<BTreeSet<_>>()
        {
            if !description.originals.iter().any(|original| {
                matches!(original,
                Original::Task { id, .. } if *id == task)
            }) {
                description
                    .targets
                    .push(task_target(&mut tx, task, &mut description.originals).await?);
            }
        }
    }
    Ok(description)
}

async fn describe(
    conn: &mut SqliteConnection,
    op: &str,
    input: &serde_json::Value,
) -> Result<Option<Description>, CallError> {
    let mut originals = Vec::new();
    let mut description = match op {
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
            let email_originals = emails(conn, &ids).await?;
            let schedule = email::schedule::get(conn, input.task_id).await?.form();
            let mut description = Description::new(
                format!("发出 {} 封邮件", input.emails.len()),
                email_targets(&email_originals),
            );
            description
                .targets
                .push(task_target(conn, input.task_id, &mut originals).await?);
            for approval in input.emails {
                let current = email_originals
                    .iter()
                    .find(|email| {
                        email.id == approval.id
                            && email.task_id == Some(input.task_id)
                            && email.state == "review"
                    })
                    .ok_or_else(|| {
                        CallError::invalid("草稿已不在任务里，请刷新后重新批准。".into())
                    })?;
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
            description.emails = task_emails(conn, input.task_id).await?;
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
            let ids = sqlx::query_scalar!(
                r#"SELECT id FROM email WHERE mentor_id = ? AND kind = 'first'
                AND task_id IS NULL AND state = 'review' ORDER BY id"#,
                input.mentor_id
            )
            .fetch_all(&mut *conn)
            .await?;
            description.emails = emails(conn, &ids).await?;
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
            description.emails = emails(conn, &[input.draft_id]).await?;
            description
        }
        "sendEmailNow" | "resendEmail" | "retryEmail" => {
            let input: crate::EmailId = parse(input)?;
            let email_originals = emails(conn, &[input.id]).await?;
            let action = match op {
                "sendEmailNow" => "立即发出 1 封邮件",
                "resendEmail" => "重新发出 1 封邮件",
                _ => "重试发出 1 封邮件",
            };
            let mut description = Description::new(action.into(), email_targets(&email_originals));
            let tasks = email_originals
                .iter()
                .filter_map(|e| e.task_id)
                .collect::<BTreeSet<_>>();
            description.emails = email_originals;
            for task in tasks {
                description
                    .targets
                    .push(task_target(conn, task, &mut originals).await?);
                description.emails = task_emails(conn, task).await?;
            }
            description
        }
        "resumeTask" | "deleteTask" | "changeTaskSchedule" => {
            let id = parse::<crate::TaskId>(input)?.id;
            let email_originals = task_emails(conn, id).await?;
            let target = task_target(conn, id, &mut originals).await?;
            let mut description = match op {
                "resumeTask" => object("恢复 1 个任务的发信", target),
                "deleteTask" => {
                    let count = email_originals
                        .iter()
                        .filter(|e| {
                            !matches!(
                                e.state.as_str(),
                                "sending" | "cloud" | "maybe_sent" | "sent" | "bounced"
                            ) && !(e.state == "canceled" && e.sent_at.is_some())
                        })
                        .count();
                    object(
                        &format!("永久删除 1 个任务和其中 {count} 封没发出的邮件（包括已批准的）"),
                        target,
                    )
                }
                _ => {
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
                    object("更改 1 个任务的发送时间", target)
                }
            };
            description.emails = email_originals;
            description
        }
        "resumeMailbox" | "deleteMailbox" => {
            let input: crate::MailboxId = parse(input)?;
            let mut description = object(
                if op == "resumeMailbox" {
                    "恢复 1 个邮箱的发信"
                } else {
                    "永久删除 1 个邮箱"
                },
                mailbox_target(conn, input.id, &mut originals).await?,
            );
            description.emails = mailbox_emails(conn, input.id).await?;
            if op == "deleteMailbox" {
                let counts = sqlx::query!(
                    r#"SELECT
                        (SELECT count(*) FROM message WHERE mailbox_id = ?1) AS "messages!: i64",
                        (SELECT count(*) FROM reply_processing WHERE message_id IN
                            (SELECT id FROM message WHERE mailbox_id = ?1)) AS "processing!: i64",
                        (SELECT count(*) FROM mailbox_folder WHERE mailbox_id = ?1) AS "folders!: i64",
                        (SELECT count(*) FROM mentor_scan WHERE mailbox_id = ?1) AS "scans!: i64",
                        (SELECT count(*) FROM mentor WHERE mailbox_id = ?1) AS "mentors!: i64""#,
                    input.id
                )
                .fetch_one(&mut *conn)
                .await?;
                description.targets.push(format!(
                    "同时删除：{} 封已收取邮件、{} 项回信处理、{} 个文件夹进度、{} 项历史查找；{} 位导师改为自动选邮箱",
                    counts.messages, counts.processing, counts.folders, counts.scans, counts.mentors
                ));
            }
            description
        }
        "rescheduleEmail" => {
            let input: crate::RescheduleEmail = parse(input)?;
            let email_originals = emails(conn, &[input.id]).await?;
            let mut description = Description::new(
                "更改 1 封邮件的发送时间".into(),
                email_targets(&email_originals),
            );
            let at = chrono::DateTime::from_timestamp_millis(input.planned_at)
                .map(|at| {
                    at.with_timezone(&chrono::Local)
                        .to_rfc3339_opts(chrono::SecondsFormat::Millis, false)
                })
                .unwrap_or_else(|| format!("无法识别的时间：{}", input.planned_at));
            description.targets.push(format!("发送时间：{at}"));
            description.emails = email_originals;
            description
        }
        "deleteMentors" | "mergeMentors" => {
            let ids = if op == "deleteMentors" {
                parse::<crate::MentorIds>(input)?
                    .ids
                    .into_iter()
                    .collect::<BTreeSet<_>>()
                    .into_iter()
                    .collect::<Vec<_>>()
            } else {
                let input: crate::MentorMerge = parse(input)?;
                vec![input.other, input.keep]
            };
            let mut description = Description::new(
                if op == "deleteMentors" {
                    format!("永久删除 {} 位导师", ids.len())
                } else {
                    "合并 2 位导师：第 1 位并入第 2 位".into()
                },
                mentor_targets(conn, &ids).await?,
            );
            for id in &ids {
                let row = sqlx::query!(
                    r#"SELECT updated_at, trashed_at, mailbox_id,
                        (SELECT count(*) FROM email WHERE mentor_id = mentor.id) AS "emails!: i64",
                        (SELECT count(*) FROM message_mentor WHERE mentor_id = mentor.id) AS "messages!: i64",
                        (SELECT count(*) FROM todo WHERE mentor_id = mentor.id) AS "todos!: i64",
                        (SELECT count(*) FROM mentor_tag WHERE mentor_id = mentor.id) AS "tags!: i64",
                        (SELECT count(*) FROM library_update WHERE mentor_id = mentor.id) AS "updates!: i64"
                    FROM mentor WHERE id = ?"#,
                    id
                )
                .fetch_optional(&mut *conn)
                .await?;
                if let Some(row) = row {
                    description.targets.push(format!(
                        "关联 {} 封发件、{} 封已收取邮件、{} 条待办、{} 个标签、{} 条资料更新",
                        row.emails, row.messages, row.todos, row.tags, row.updates
                    ));
                    originals.push(Original::Mentor {
                        id: *id,
                        updated_at: row.updated_at,
                        trashed_at: row.trashed_at,
                        mailbox_id: row.mailbox_id,
                        emails: row.emails,
                        messages: row.messages,
                        todos: row.todos,
                        tags: row.tags,
                        updates: row.updates,
                    });
                }
            }
            let email_ids = sqlx::query_scalar!(
                r#"SELECT id FROM email
                WHERE mentor_id IN (SELECT value FROM json_each(?))
                ORDER BY id"#,
                Json(&ids)
            )
            .fetch_all(&mut *conn)
            .await?;
            description.emails = emails(conn, &email_ids).await?;
            if op == "deleteMentors" {
                let count = sqlx::query_scalar!(
                    r#"WITH doomed AS (SELECT id FROM mentor
                        WHERE id IN (SELECT value FROM json_each(?)) AND trashed_at IS NOT NULL)
                    SELECT count(*) AS "count!: i64" FROM message
                    WHERE id IN (SELECT message_id FROM message_mentor WHERE mentor_id IN doomed)
                        AND NOT EXISTS (SELECT 1 FROM message_mentor mm
                            WHERE mm.message_id = message.id AND mm.mentor_id NOT IN doomed)"#,
                    Json(&ids)
                )
                .fetch_one(&mut *conn)
                .await?;
                description
                    .targets
                    .push(format!("同时永久删除 {count} 封不涉及其他导师的已收取邮件"));
            }
            description
        }
        "deleteTemplate" => {
            let input: crate::TemplateId = parse(input)?;
            let row = sqlx::query!(
                "SELECT name, subject, body_md, updated_at FROM template WHERE id = ?",
                input.id
            )
            .fetch_optional(&mut *conn)
            .await?;
            let target = match row {
                Some(row) => {
                    let name = row.name.clone();
                    originals.push(Original::Template {
                        id: input.id,
                        name: row.name,
                        subject: row.subject,
                        body_md: row.body_md,
                        updated_at: row.updated_at,
                    });
                    name
                }
                None => "找不到的模板（可能已被删除）".into(),
            };
            object("永久删除 1 个模板", target)
        }
        "deleteMaterial" => {
            let input: crate::MaterialId = parse(input)?;
            let row = sqlx::query!(
                r#"SELECT file_name, size, created_at, for_matching, for_writing, attach_by_default,
                    (SELECT count(*) FROM email_attachment WHERE material_id = material.id) AS "emails!: i64",
                    (SELECT count(*) FROM task_material WHERE material_id = material.id) AS "tasks!: i64"
                FROM material WHERE id = ?"#,
                input.id
            )
            .fetch_optional(&mut *conn)
            .await?;
            let target = match row {
                Some(row) => {
                    let target = format!(
                        "{}（{} 字节，关联 {} 份邮件附件、{} 项任务材料）",
                        row.file_name, row.size, row.emails, row.tasks
                    );
                    originals.push(Original::Material {
                        id: input.id,
                        file_name: row.file_name,
                        size: row.size,
                        created_at: row.created_at,
                        for_matching: row.for_matching,
                        for_writing: row.for_writing,
                        attach_by_default: row.attach_by_default,
                        emails: row.emails,
                        tasks: row.tasks,
                    });
                    target
                }
                None => "找不到的材料（可能已被删除）".into(),
            };
            object("永久删除 1 份材料", target)
        }
        "deleteCrawl" => {
            let input: crate::CrawlId = parse(input)?;
            let row = sqlx::query!(
                r#"SELECT university, school, state, start_urls, limits,
                    (SELECT count(*) FROM candidate WHERE crawl_id = crawl.id
                        AND review = 'pending' AND mentor_id IS NULL) AS "candidates!: i64",
                    (SELECT count(*) FROM crawl_page WHERE crawl_id = crawl.id) AS "pages!: i64",
                    (SELECT count(*) FROM crawl_run WHERE crawl_id = crawl.id) AS "runs!: i64"
                FROM crawl WHERE id = ?"#,
                input.id
            )
            .fetch_optional(&mut *conn)
            .await?;
            let target = match row {
                Some(row) => {
                    let target = format!(
                        "{} {}：同时删除 {} 位未审核候选、{} 个抓取页面、{} 项运行记录",
                        row.university.as_deref().unwrap_or(""),
                        row.school.as_deref().unwrap_or(""),
                        row.candidates,
                        row.pages,
                        row.runs
                    );
                    originals.push(Original::Crawl {
                        id: input.id,
                        university: row.university,
                        school: row.school,
                        state: row.state,
                        start_urls: row.start_urls,
                        limits: row.limits,
                        candidates: row.candidates,
                        pages: row.pages,
                        runs: row.runs,
                    });
                    target
                }
                None => "找不到的抓取（可能已被删除）".into(),
            };
            object("永久删除 1 项抓取记录", target)
        }
        "deleteTag" => {
            let input: crate::TagId = parse(input)?;
            let row = sqlx::query!(
                r#"SELECT name, color,
                    (SELECT count(*) FROM mentor_tag WHERE tag_id = tag.id) AS "mentors!: i64"
                FROM tag WHERE id = ?"#,
                input.id
            )
            .fetch_optional(&mut *conn)
            .await?;
            let target = match row {
                Some(row) => {
                    let target =
                        format!("{}（同时移除 {} 位导师的标签关联）", row.name, row.mentors);
                    originals.push(Original::Tag {
                        id: input.id,
                        name: row.name,
                        color: row.color,
                        mentors: row.mentors,
                    });
                    target
                }
                None => "找不到的标签（可能已被删除）".into(),
            };
            object("永久删除 1 个标签", target)
        }
        "deleteTodo" => {
            let input: crate::TodoId = parse(input)?;
            let row = sqlx::query!(
                r#"SELECT m.name, t.mentor_id, t.message_id, t.text, t.due_date, t.due_time, t.done_at
                FROM todo t JOIN mentor m ON m.id = t.mentor_id WHERE t.id = ?"#,
                input.id
            )
            .fetch_optional(&mut *conn)
            .await?;
            let target = match row {
                Some(row) => {
                    let target = format!("{}：{}", row.name, row.text);
                    originals.push(Original::Todo {
                        id: input.id,
                        mentor_id: row.mentor_id,
                        message_id: row.message_id,
                        text: row.text,
                        due_date: row.due_date,
                        due_time: row.due_time,
                        done_at: row.done_at,
                    });
                    target
                }
                None => "找不到的待办（可能已被删除）".into(),
            };
            object("永久删除 1 条待办", target)
        }
        "deleteModelConfig" => {
            let input: crate::ModelConfigId = parse(input)?;
            object(
                "永久删除 1 个模型配置",
                model_target(conn, input.id, &mut originals).await?,
            )
        }
        "agreeModel" => {
            let input: crate::ModelAgreement = parse(input)?;
            let model = model_target(conn, input.id, &mut originals).await?;
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
            let row = sqlx::query!("SELECT name, mailbox_id FROM mentor WHERE id = ?", input.id)
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
                Some(id) => mailbox_target(conn, id, &mut originals).await?,
                None => "自动选择邮箱".into(),
            };
            let after = match input.mailbox_id {
                Some(id) => mailbox_target(conn, id, &mut originals).await?,
                None => "自动选择邮箱".into(),
            };
            let mut description = object(
                &format!(
                    "更改 1 位导师的发件邮箱：影响已批准还没发出的 {} 封邮件",
                    ids.len()
                ),
                format!("{}：{before} → {after}", row.name),
            );
            description.emails = emails(conn, &ids).await?;
            description
        }
        "updateMailbox" => {
            let input: crate::MailboxEdit = parse(input)?;
            let at = crate::now();
            let address = sqlx::query_scalar!(
                r#"SELECT address
                FROM mailbox
                WHERE id = ? AND paused_until > ?"#,
                input.id,
                at
            )
            .fetch_optional(&mut *conn)
            .await?;
            let Some(_) = address else {
                return Ok(None);
            };
            let mut description = object(
                "改邮箱的设置：会马上恢复发信",
                mailbox_target(conn, input.id, &mut originals).await?,
            );
            let form = input.form;
            description.targets.push(format!(
                "改为：{}（发件人：{}；SMTP {}:{}；IMAP {}:{}；每天最多 {} 封、间隔 {} 秒）",
                form.address,
                form.sender_name,
                form.smtp.host,
                form.smtp.port,
                form.imap.host,
                form.imap.port,
                form.daily_limit,
                form.min_interval_s
            ));
            description.emails = mailbox_emails(conn, input.id).await?;
            description
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
            let ids = sqlx::query_scalar!(
                r#"SELECT id FROM email
                WHERE mentor_id = ? AND kind = ? AND state = 'review'
                    AND (? <> 'first' OR task_id IS NULL)
                ORDER BY id"#,
                mentor,
                kind,
                kind
            )
            .fetch_all(&mut *conn)
            .await?;
            if ids.is_empty() {
                return Ok(None);
            }
            let email_originals = emails(conn, &ids).await?;
            let mut description = Description::new(
                if kind == "first" {
                    "删除 1 封首封草稿"
                } else {
                    "删除 1 封回复草稿"
                }
                .into(),
                email_targets(&email_originals),
            );
            description.emails = email_originals;
            description
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
    description.originals.extend(originals);
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
            COUNT(*) AS "count!: i64"
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
    let ids = sqlx::query_scalar!(
        "SELECT id FROM email WHERE mentor_id = ? AND state = 'approved' ORDER BY id",
        mentor
    )
    .fetch_all(&mut *conn)
    .await?;
    let mut description = {
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
        object(
            &action,
            format!("{}：{before} → {after}（{} 封）", row.name, row.count),
        )
    };
    description.emails = emails(conn, &ids).await?;
    Ok(Some(description))
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
            COUNT(*) AS "count!: i64"
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
            format!("{}：{before} → {after}（{} 封）", row.name, row.count)
        })
        .collect();
    let changed = changes
        .iter()
        .filter_map(mentor::intake::Change::changed_recipient)
        .map(|(id, _)| id)
        .collect::<Vec<_>>();
    let ids = sqlx::query_scalar!(
        r#"SELECT id FROM email WHERE mentor_id IN (SELECT value FROM json_each(?))
        AND state = 'approved' ORDER BY id"#,
        Json(changed)
    )
    .fetch_all(&mut *conn)
    .await?;
    let mut description = Description::new(action, targets);
    description.emails = emails(conn, &ids).await?;
    Ok(Some(description))
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
