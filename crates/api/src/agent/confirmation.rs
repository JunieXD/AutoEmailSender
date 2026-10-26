//! The confirmation decision and its explanation use the same snapshot of each affected object.

use serde::{Deserialize, Serialize, de::DeserializeOwned};
use sqlx::types::Json;
use ts_rs::TS;

use crate::{CallError, Context};

#[derive(Clone, Serialize, TS)]
pub struct Description {
    /// A complete sentence for the dialog, notification and CLI.
    pub action: String,
    pub targets: Vec<String>,
    pub more: usize,
}

impl Description {
    fn new(action: String, targets: Vec<String>) -> Self {
        let more = targets.len().saturating_sub(5);
        Self {
            action,
            targets: targets.into_iter().take(5).collect(),
            more,
        }
    }
}

fn parse<T: DeserializeOwned>(input: &serde_json::Value) -> Result<T, CallError> {
    serde_json::from_value(input.clone()).map_err(CallError::bad_input)
}

fn names(targets: &[String]) -> String {
    let shown = targets
        .iter()
        .take(5)
        .cloned()
        .collect::<Vec<_>>()
        .join("、");
    if targets.len() > 5 {
        format!("{shown}等 {} 位导师", targets.len())
    } else {
        shown
    }
}

async fn mentor_names(ctx: &Context, ids: &[i64]) -> Result<Vec<String>, CallError> {
    let ids = Json(ids);
    Ok(sqlx::query_scalar!(
        r#"SELECT COALESCE(m.name, '找不到的导师（可能已被删除）') AS "name!: String"
        FROM json_each(?) selected
        LEFT JOIN mentor m ON m.id = selected.value
        ORDER BY CAST(selected.key AS INTEGER)"#,
        ids
    )
    .fetch_all(&ctx.db)
    .await?)
}

async fn email_names(ctx: &Context, ids: &[i64]) -> Result<Vec<String>, CallError> {
    let ids = Json(ids);
    Ok(sqlx::query_scalar!(
        r#"SELECT COALESCE(m.name, '找不到邮件对应的导师（邮件可能已被删除）') AS "name!: String"
        FROM json_each(?) selected
        LEFT JOIN email e ON e.id = selected.value
        LEFT JOIN mentor m ON m.id = e.mentor_id
        ORDER BY CAST(selected.key AS INTEGER)"#,
        ids
    )
    .fetch_all(&ctx.db)
    .await?)
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

fn object(action: &str, kind: &str, target: String) -> Description {
    Description::new(format!("{action}{kind}“{target}”"), vec![target])
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
            let ids = input.emails.iter().map(|e| e.id).collect::<Vec<_>>();
            let targets = email_names(ctx, &ids).await?;
            Description::new(
                format!("发出给{}的 {} 封邮件", names(&targets), ids.len()),
                targets,
            )
        }
        "approveFirstEmail" | "approveFollowUp" | "approveReply" => {
            let input: MentorKey = parse(input)?;
            let targets = mentor_names(ctx, &[input.mentor_id]).await?;
            let kind = match op {
                "approveFirstEmail" => "首封邮件",
                "approveFollowUp" => "跟进邮件",
                _ => "回复邮件",
            };
            Description::new(format!("发出给{}的{kind}", names(&targets)), targets)
        }
        "sendEmailNow" | "resendEmail" | "retryEmail" => {
            let input: crate::EmailId = parse(input)?;
            let targets = email_names(ctx, &[input.id]).await?;
            let action = match op {
                "sendEmailNow" => "立即发出",
                "resendEmail" => "重新发出",
                _ => "重试发出",
            };
            Description::new(format!("{action}给{}的邮件", names(&targets)), targets)
        }
        "resumeTask" | "deleteTask" => {
            let input: crate::TaskId = parse(input)?;
            let target = task_name(ctx, input.id).await?;
            let action = if op == "resumeTask" {
                format!("恢复任务“{target}”的发信")
            } else {
                format!("永久删除任务“{target}”及其中的草稿")
            };
            Description::new(action, vec![target])
        }
        "resumeMailbox" | "deleteMailbox" => {
            let input: crate::MailboxId = parse(input)?;
            let target = mailbox_address(ctx, input.id).await?;
            if op == "resumeMailbox" {
                Description::new(format!("恢复邮箱 {target} 的发信"), vec![target])
            } else {
                object("永久删除", "邮箱", target)
            }
        }
        "changeTaskSchedule" => {
            let input: task::ScheduleEdit = parse(input)?;
            let target = task_name(ctx, input.id).await?;
            let schedule = match input.schedule {
                email::schedule::Schedule::Now => "立即发送".into(),
                email::schedule::Schedule::Window(window) => format!(
                    "{} 的 {}—{}，每个时间段 {} 封",
                    window.dates.join("、"),
                    window.start,
                    window.end,
                    window.per_window
                ),
            };
            Description::new(
                format!("把任务“{target}”的发送时间改为{schedule}"),
                vec![target],
            )
        }
        "rescheduleEmail" => {
            let input: crate::RescheduleEmail = parse(input)?;
            let targets = email_names(ctx, &[input.id]).await?;
            let at = chrono::DateTime::from_timestamp_millis(input.planned_at)
                .map(|at| {
                    at.with_timezone(&chrono::Local)
                        .format("%Y 年 %-m 月 %-d 日 %H:%M")
                        .to_string()
                })
                .unwrap_or_else(|| "无法识别的时间（请检查所选时间）".into());
            Description::new(
                format!("把给{}的邮件发送时间改为 {at}", names(&targets)),
                targets,
            )
        }
        "deleteMentors" => {
            let input: crate::MentorIds = parse(input)?;
            let targets = mentor_names(ctx, &input.ids).await?;
            Description::new(
                format!("永久删除 {} 位导师：{}", targets.len(), names(&targets)),
                targets,
            )
        }
        "mergeMentors" => {
            let input: crate::MentorMerge = parse(input)?;
            let targets = mentor_names(ctx, &[input.other, input.keep]).await?;
            Description::new(format!("把导师{}并入{}", targets[0], targets[1]), targets)
        }
        "deleteTemplate" => {
            let input: crate::TemplateId = parse(input)?;
            let name = sqlx::query_scalar!("SELECT name FROM template WHERE id = ?", input.id)
                .fetch_optional(&ctx.db)
                .await?
                .unwrap_or_else(|| "找不到的模板（可能已被删除）".into());
            object("永久删除", "模板", name)
        }
        "deleteMaterial" => {
            let input: crate::MaterialId = parse(input)?;
            let name = sqlx::query_scalar!("SELECT file_name FROM material WHERE id = ?", input.id)
                .fetch_optional(&ctx.db)
                .await?
                .unwrap_or_else(|| "找不到的材料（可能已被删除）".into());
            object("永久删除", "材料", name)
        }
        "deleteCrawl" => {
            let input: crate::CrawlId = parse(input)?;
            let name = sqlx::query_scalar!(
                r#"SELECT COALESCE(NULLIF(trim(COALESCE(university, '') || ' ' ||
                    COALESCE(school, '')), ''), '导师抓取') AS "name!: String"
                FROM crawl
                WHERE id = ?"#,
                input.id
            )
            .fetch_optional(&ctx.db)
            .await?
            .unwrap_or_else(|| "找不到的抓取（可能已被删除）".into());
            object("永久删除", "抓取", name)
        }
        "deleteTag" => {
            let input: crate::TagId = parse(input)?;
            let name = sqlx::query_scalar!("SELECT name FROM tag WHERE id = ?", input.id)
                .fetch_optional(&ctx.db)
                .await?
                .unwrap_or_else(|| "找不到的标签（可能已被删除）".into());
            object("永久删除", "标签", name)
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
            object("永久删除", "待办", name)
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
            object("永久删除", "模型配置", name)
        }
        "agreeModel" => {
            let input: crate::ModelAgreement = parse(input)?;
            let model =
                sqlx::query_scalar!("SELECT model FROM model_config WHERE id = ?", input.id)
                    .fetch_optional(&ctx.db)
                    .await?
                    .unwrap_or_else(|| "找不到的模型配置（可能已被删除）".into());
            let provider = if input.provider == model::providers::CUSTOM {
                input
                    .base_url
                    .unwrap_or_else(|| "自定义服务（未填写地址）".into())
            } else {
                model::providers::find(&input.provider)
                    .map_or("找不到的模型服务商", |p| p.name)
                    .into()
            };
            let target = format!("{provider}（{model}）");
            Description::new(format!("同意把数据发给 {target}"), vec![target])
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
            Description::new(
                format!("改邮箱 {address} 的设置：会马上恢复发信"),
                vec![address],
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
            let kind = if kind == "first" { "首封" } else { "回复" };
            Description::new(format!("删除给{name}的{kind}草稿"), vec![name])
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
        r#"SELECT m.name, COUNT(*) AS count
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
        let action = match address {
            Some(address) => format!(
                "把导师{}的发信地址改成 {address}：已批准还没发出的 {} 封会发到新地址",
                row.name, row.count
            ),
            None => format!(
                "移除导师{}的发信地址：已批准还没发出的 {} 封将没有收件地址",
                row.name, row.count
            ),
        };
        Description::new(action, vec![row.name])
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
            COUNT(*) AS count
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
    let details = rows
        .iter()
        .take(5)
        .map(|row| {
            let address = row.address.as_deref().unwrap_or("无发信地址");
            format!("{} → {address}（{} 封）", row.name, row.count)
        })
        .collect::<Vec<_>>()
        .join("、");
    let targets = rows.into_iter().map(|row| row.name).collect::<Vec<_>>();
    let more = if targets.len() > 5 {
        format!("等 {} 位导师", targets.len())
    } else {
        String::new()
    };
    Ok(Some(Description::new(
        format!(
            "更改导师的发信地址：{details}{more}；已批准还没发出的 {count} 封将使用更改后的地址"
        ),
        targets,
    )))
}
