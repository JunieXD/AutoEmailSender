use super::*;
use email::fact_check::FactCheck;

pub async fn get_ai_fact_check(ctx: &Context, _: Nothing) -> Result<bool, CallError> {
    Ok(profile::ai_fact_check(&ctx.db).await?)
}
pub async fn set_ai_fact_check(ctx: &Context, input: NotificationSetting) -> Result<(), CallError> {
    Ok(profile::set_ai_fact_check(&ctx.db, input.on).await?)
}

pub async fn fact_check_email(
    ctx: &Context,
    input: EmailId,
) -> Result<Option<FactCheck>, CallError> {
    // Settings, configuration and consent can change while waiting for shared model capacity.
    let _permit = ctx.model_limit.acquire().await;
    if !profile::ai_fact_check(&ctx.db).await? {
        return Ok(None);
    }
    let row = sqlx::query!(
        r#"SELECT task_id, kind, mentor_id, subject, body_md, generation_claim
        FROM email
        WHERE id = ? AND state = 'review'"#,
        input.id,
    )
    .fetch_optional(&ctx.db)
    .await?
    .ok_or_else(|| CallError::invalid("这封邮件已不在待审核状态，请刷新后重试。".into()))?;
    let result: Result<FactCheck, CallError> = async {
        let (chosen, references) = if let Some(task_id) = row.task_id {
            let config =
                sqlx::query_scalar!("SELECT model_config_id FROM task WHERE id = ?", task_id)
                    .fetch_one(&ctx.db)
                    .await?;
            let chosen = model::selected(&ctx.db, config)
                .await?
                .ok_or(writing::Error::NoModel)?;
            let ids = sqlx::query_scalar!(
                "SELECT material_id FROM task_material WHERE task_id = ? AND role = 'reference'",
                task_id
            )
            .fetch_all(&ctx.db)
            .await?;
            let references = task::references(&mut *ctx.db.acquire().await?, &ids).await?;
            (chosen, references)
        } else {
            (
                model::chosen(&ctx.db)
                    .await?
                    .ok_or(writing::Error::NoModel)?,
                material::for_writing(&ctx.db).await?,
            )
        };
        // Reply subjects live on the target message until approval fixes the outgoing subject.
        let subject = if row.kind == "reply" {
            email::reply_draft(&ctx.db, row.mentor_id)
                .await?
                .and_then(|d| d.target)
                .and_then(|t| t.subject)
                .map(|s| email::reply_subject(&s))
                .unwrap_or_default()
        } else {
            row.subject.unwrap_or_default()
        };
        let findings = writing::fact_check::check(
            &ctx.db,
            &ctx.net,
            input.id,
            &writing::Letter {
                subject,
                body_md: row.body_md.unwrap_or_default(),
            },
            &chosen,
            &references,
            now(),
        )
        .await?;
        Ok(FactCheck::Completed { findings })
    }
    .await;
    let stored = match &result {
        Ok(checked) => checked.clone(),
        Err(error) => FactCheck::Failed {
            reason: error.message.clone(),
        },
    };
    if !email::fact_check::save(&ctx.db, input.id, row.generation_claim, &stored, now()).await? {
        return Err(CallError::invalid(
            "这封邮件已重新生成或不在待审核状态，核对结果未保存。".into(),
        ));
    }
    result.map(Some)
}

impl From<writing::fact_check::Error> for CallError {
    fn from(error: writing::fact_check::Error) -> Self {
        match error {
            writing::fact_check::Error::NoModel => writing::Error::NoModel.into(),
            writing::fact_check::Error::NotAgreed => writing::Error::NotAgreed.into(),
            writing::fact_check::Error::Model(error) => error.into(),
            writing::fact_check::Error::Db(error) => error.into(),
            error @ writing::fact_check::Error::InvalidAnswer => CallError {
                code: ErrorCode::External,
                message: error.message(),
            },
        }
    }
}

pub async fn check_follow_up_draft(
    ctx: &Context,
    input: ReplyDraftEdit,
) -> Result<Vec<check::Problem>, CallError> {
    let m = get_mentor(
        ctx,
        MentorId {
            id: input.mentor_id,
        },
    )
    .await?;
    let mut problems = check::letter(
        &m.name,
        &m.aliases,
        "",
        &input.body_md,
        !input.attachments.is_empty(),
    );
    if let Some(draft) = email::follow_up::draft(&ctx.db, input.mentor_id).await? {
        let subject = draft
            .target
            .as_ref()
            .map(|t| t.subject.as_str())
            .unwrap_or_default();
        problems.extend(check::fact_check(
            draft.fact_check.as_ref(),
            subject,
            &input.body_md,
        ));
    }
    Ok(problems)
}
