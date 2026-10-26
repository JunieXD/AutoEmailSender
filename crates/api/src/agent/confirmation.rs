//! Confirmation effects remain complete for rechecking; presentation belongs to the caller.

use std::collections::BTreeSet;

use serde::{Deserialize, Serialize, de::DeserializeOwned};
use sqlx::types::Json;
use ts_rs::TS;

use crate::{CallError, Context};

#[derive(Clone, PartialEq, Eq, Serialize, TS)]
pub struct Description {
    /// Only application-authored text and counts; object content belongs in targets.
    pub action: String,
    pub targets: Vec<String>,
}

impl Description {
    fn new(action: String, targets: Vec<String>) -> Self {
        Self { action, targets }
    }
}

fn parse<T: DeserializeOwned>(input: &serde_json::Value) -> Result<T, CallError> {
    serde_json::from_value(input.clone()).map_err(CallError::bad_input)
}

async fn mentor_targets(ctx: &Context, ids: &[i64]) -> Result<Vec<String>, CallError> {
    let ids = Json(ids);
    let rows = sqlx::query!(
        r#"SELECT COALESCE(m.name, '找不到的导师（可能已被删除）') AS "name!: String",
            (SELECT address FROM mentor_email
                WHERE mentor_id = m.id ORDER BY position LIMIT 1) AS "address?: String"
        FROM json_each(?) selected
        LEFT JOIN mentor m ON m.id = selected.value
        ORDER BY CAST(selected.key AS INTEGER)"#,
        ids
    )
    .fetch_all(&ctx.db)
    .await?;
    Ok(rows
        .into_iter()
        .map(|row| {
            let address = row.address.as_deref().unwrap_or("未填写邮箱");
            format!("{}：{address}", row.name)
        })
        .collect())
}

async fn email_targets(ctx: &Context, ids: &[i64]) -> Result<Vec<String>, CallError> {
    let ids = Json(ids);
    let rows = sqlx::query!(
        r#"SELECT COALESCE(m.name, '找不到邮件对应的导师（邮件可能已被删除）') AS "name!: String",
            (SELECT address FROM mentor_email
                WHERE mentor_id = m.id ORDER BY position LIMIT 1) AS "address?: String",
            COUNT(*) AS "count!: i64"
        FROM json_each(?) selected
        LEFT JOIN email e ON e.id = selected.value
        LEFT JOIN mentor m ON m.id = e.mentor_id
        GROUP BY m.id, CASE WHEN m.id IS NULL THEN selected.value END
        ORDER BY MIN(CAST(selected.key AS INTEGER))"#,
        ids
    )
    .fetch_all(&ctx.db)
    .await?;
    Ok(rows
        .into_iter()
        .map(|row| {
            let address = row.address.as_deref().unwrap_or("未填写邮箱");
            format!("{}：{address}（{} 封）", row.name, row.count)
        })
        .collect())
}

async fn task_name(ctx: &Context, id: i64) -> Result<String, CallError> {
    Ok(
        sqlx::query_scalar!("SELECT name FROM task WHERE id = ?", id)
            .fetch_optional(&ctx.db)
            .await?
            .unwrap_or_else(|| "找不到的任务（可能已被删除）".into()),
    )
}

async fn mailbox_address(ctx: &Context, id: i64) -> Result<String, CallError> {
    Ok(
        sqlx::query_scalar!("SELECT address FROM mailbox WHERE id = ?", id)
            .fetch_optional(&ctx.db)
            .await?
            .unwrap_or_else(|| "找不到的邮箱（可能已被删除）".into()),
    )
}

fn object(action: &str, target: String) -> Description {
    Description::new(action.into(), vec![target])
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase")]
struct MentorKey {
    mentor_id: i64,
}

pub(crate) async fn confirmation(
    ctx: &Context,
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
                .collect::<BTreeSet<_>>()
                .into_iter()
                .collect::<Vec<_>>();
            let targets = email_targets(ctx, &ids).await?;
            Description::new(
                format!("发出 {} 封邮件，给 {} 位导师", ids.len(), targets.len()),
                targets,
            )
        }
        "approveFirstEmail" | "approveFollowUp" | "approveReply" => {
            let input: MentorKey = parse(input)?;
            let targets = mentor_targets(ctx, &[input.mentor_id]).await?;
            let action = match op {
                "approveFirstEmail" => "发出 1 封首封邮件",
                "approveFollowUp" => "发出 1 封追问邮件",
                _ => "发出 1 封回复邮件",
            };
            Description::new(action.into(), targets)
        }
        "sendEmailNow" | "resendEmail" | "retryEmail" => {
            let input: crate::EmailId = parse(input)?;
            let targets = email_targets(ctx, &[input.id]).await?;
            let action = match op {
                "sendEmailNow" => "立即发出 1 封邮件",
                "resendEmail" => "重新发出 1 封邮件",
                _ => "重试发出 1 封邮件",
            };
            Description::new(action.into(), targets)
        }
        "resumeTask" | "deleteTask" => {
            let input: crate::TaskId = parse(input)?;
            let target = task_name(ctx, input.id).await?;
            let action = if op == "resumeTask" {
                "恢复 1 个任务的发信"
            } else {
                "永久删除 1 个任务及其中的草稿"
            };
            object(action, target)
        }
        "resumeMailbox" | "deleteMailbox" => {
            let input: crate::MailboxId = parse(input)?;
            let target = mailbox_address(ctx, input.id).await?;
            let action = if op == "resumeMailbox" {
                "恢复 1 个邮箱的发信"
            } else {
                "永久删除 1 个邮箱"
            };
            object(action, target)
        }
        "changeTaskSchedule" => {
            let input: task::ScheduleEdit = parse(input)?;
            let target = task_name(ctx, input.id).await?;
            match input.schedule {
                email::schedule::Schedule::Now => object("把任务改为立即发送", target),
                email::schedule::Schedule::Window(window) => {
                    let dates = window.dates.join("、");
                    let target = format!(
                        "{target}：{dates} 的 {}–{}，每个窗口最多 {} 封",
                        window.start, window.end, window.per_window
                    );
                    object("更改 1 个任务的发送时间", target)
                }
            }
        }
        "rescheduleEmail" => {
            let input: crate::RescheduleEmail = parse(input)?;
            let targets = email_targets(ctx, &[input.id]).await?;
            let at = chrono::DateTime::from_timestamp_millis(input.planned_at)
                .map(|at| {
                    at.with_timezone(&chrono::Local)
                        .format("%Y年%-m月%-d日 %H:%M:%S%.3f")
                        .to_string()
                })
                .unwrap_or_else(|| format!("无法识别的时间：{}", input.planned_at));
            Description::new(
                "更改 1 封邮件的发送时间".into(),
                targets
                    .into_iter()
                    .map(|target| format!("{target}；发送时间：{at}"))
                    .collect(),
            )
        }
        "deleteMentors" => {
            let input: crate::MentorIds = parse(input)?;
            let ids = input
                .ids
                .into_iter()
                .collect::<BTreeSet<_>>()
                .into_iter()
                .collect::<Vec<_>>();
            let targets = mentor_targets(ctx, &ids).await?;
            Description::new(format!("永久删除 {} 位导师", targets.len()), targets)
        }
        "mergeMentors" => {
            let input: crate::MentorMerge = parse(input)?;
            let targets = mentor_targets(ctx, &[input.other, input.keep]).await?;
            Description::new("合并 2 位导师，前一位并入后一位".into(), targets)
        }
        "deleteTemplate" => {
            let input: crate::TemplateId = parse(input)?;
            let name = sqlx::query_scalar!("SELECT name FROM template WHERE id = ?", input.id)
                .fetch_optional(&ctx.db)
                .await?
                .unwrap_or_else(|| "找不到的模板（可能已被删除）".into());
            object("永久删除 1 个模板", name)
        }
        "deleteMaterial" => {
            let input: crate::MaterialId = parse(input)?;
            let name = sqlx::query_scalar!("SELECT file_name FROM material WHERE id = ?", input.id)
                .fetch_optional(&ctx.db)
                .await?
                .unwrap_or_else(|| "找不到的材料（可能已被删除）".into());
            object("永久删除 1 份材料", name)
        }
        "deleteCrawl" => {
            let input: crate::CrawlId = parse(input)?;
            let row = sqlx::query!(
                "SELECT university, school FROM crawl WHERE id = ?",
                input.id
            )
            .fetch_optional(&ctx.db)
            .await?;
            let name = match row {
                Some(row) => {
                    let parts = row
                        .university
                        .into_iter()
                        .chain(row.school)
                        .collect::<Vec<_>>();
                    if parts.is_empty() {
                        "导师抓取".into()
                    } else {
                        parts.join(" ")
                    }
                }
                None => "找不到的抓取（可能已被删除）".into(),
            };
            object("永久删除 1 项抓取记录", name)
        }
        "deleteTag" => {
            let input: crate::TagId = parse(input)?;
            let name = sqlx::query_scalar!("SELECT name FROM tag WHERE id = ?", input.id)
                .fetch_optional(&ctx.db)
                .await?
                .unwrap_or_else(|| "找不到的标签（可能已被删除）".into());
            object("永久删除 1 个标签", name)
        }
        "deleteTodo" => {
            let input: crate::TodoId = parse(input)?;
            let name = sqlx::query_scalar!(
                r#"SELECT m.name || '：' || t.text AS "name!: String"
                FROM todo t
                JOIN mentor m ON m.id = t.mentor_id
                WHERE t.id = ?"#,
                input.id
            )
            .fetch_optional(&ctx.db)
            .await?
            .unwrap_or_else(|| "找不到的待办（可能已被删除）".into());
            object("永久删除 1 条待办", name)
        }
        "deleteModelConfig" => {
            let input: crate::ModelConfigId = parse(input)?;
            let name = sqlx::query_scalar!(
                r#"SELECT COALESCE(name, model) AS "name!: String"
                FROM model_config
                WHERE id = ?"#,
                input.id
            )
            .fetch_optional(&ctx.db)
            .await?
            .unwrap_or_else(|| "找不到的模型配置（可能已被删除）".into());
            object("永久删除 1 个模型配置", name)
        }
        "agreeModel" => {
            let input: crate::ModelAgreement = parse(input)?;
            let model =
                sqlx::query_scalar!("SELECT model FROM model_config WHERE id = ?", input.id)
                    .fetch_optional(&ctx.db)
                    .await?
                    .unwrap_or_else(|| "找不到的模型配置（可能已被删除）".into());
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
            .fetch_optional(&ctx.db)
            .await?;
            let Some(address) = address else {
                return Ok(None);
            };
            object("改邮箱的设置：会马上恢复发信", address)
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
            let name = sqlx::query_scalar!(
                r#"SELECT m.name
                FROM mentor m
                WHERE m.id = ? AND EXISTS (
                    SELECT 1 FROM email e
                    WHERE e.mentor_id = m.id AND e.kind = ? AND e.state = 'review'
                        AND (? <> 'first' OR e.task_id IS NULL)
                )"#,
                mentor,
                kind,
                kind
            )
            .fetch_optional(&ctx.db)
            .await?;
            let Some(name) = name else { return Ok(None) };
            let action = if kind == "first" {
                "删除 1 封首封草稿"
            } else {
                "删除 1 封回复草稿"
            };
            object(action, name)
        }
        "updateMentor" => {
            let input: crate::MentorEdit = parse(input)?;
            let fields = input.form.check().map_err(CallError::invalid)?;
            return changed_recipient(ctx, input.id, fields.emails.first().map(String::as_str))
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
            .fetch_all(&ctx.db)
            .await?;
            if addresses
                .iter()
                .any(|old| old.eq_ignore_ascii_case(address))
                || mentor::place(&addresses, address) != 0
            {
                return Ok(None);
            }
            return changed_recipient(ctx, input.id, Some(address)).await;
        }
        "importMentors" => {
            let input: crate::ImportMentors = parse(input)?;
            let sheets = crate::read_import(&input.file).await?;
            let mut tx = ctx.db.begin().await?;
            let (_, changes) =
                mentor::import::calculate(&mut tx, &sheets, &input.file.setup, &input.decisions)
                    .await?;
            return imported_recipients(&mut tx, &changes).await;
        }
        "acceptCandidates" => {
            let input: crawl::review::ReviewInput = parse(input)?;
            let mut tx = ctx.db.begin().await?;
            let plan = crawl::review::plan(&mut tx, &input).await?;
            return imported_recipients(&mut tx, &plan.changes).await;
        }
        _ => return Ok(None),
    };
    Ok(Some(description))
}

async fn changed_recipient(
    ctx: &Context,
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
    .fetch_optional(&ctx.db)
    .await?;
    Ok(row.map(|row| {
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
    }))
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
    .fetch_all(conn)
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
    Ok(Some(Description::new(action, targets)))
}
