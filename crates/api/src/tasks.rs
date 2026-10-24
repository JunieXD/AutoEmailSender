use super::*;
#[derive(Debug, Deserialize, Serialize, TS)]
pub struct TaskId {
    pub id: i64,
}
#[derive(Debug, Deserialize, TS)]
#[serde(rename_all = "camelCase")]
pub struct TaskRetry {
    pub task_id: i64,
    pub id: Option<i64>,
}
impl From<task::Error> for CallError {
    fn from(e: task::Error) -> Self {
        match e {
            task::Error::Invalid(s) => Self::invalid(s),
            task::Error::Db(e) => e.into(),
        }
    }
}
pub async fn plan_task(ctx: &Context, form: task::NewTask) -> Result<task::TaskPlan, CallError> {
    Ok(task::plan(&ctx.db, &form).await?)
}
pub async fn create_task(ctx: &Context, form: task::NewTask) -> Result<TaskId, CallError> {
    let id = task::create(&ctx.db, &form, now()).await?;
    ctx.generator.wake();
    Ok(TaskId { id })
}
pub async fn list_tasks(ctx: &Context, q: task::TaskQuery) -> Result<task::TaskPage, CallError> {
    Ok(task::list(&ctx.db, &q).await?)
}
pub async fn get_task(ctx: &Context, key: TaskId) -> Result<task::TaskSummary, CallError> {
    task::get(&ctx.db, key.id)
        .await?
        .ok_or_else(|| CallError::not_found("任务已被删除。"))
}
pub async fn list_task_drafts(
    ctx: &Context,
    q: task::DraftQuery,
) -> Result<task::DraftPage, CallError> {
    Ok(task::drafts(&ctx.db, &q, now()).await?)
}
pub async fn get_task_draft(
    ctx: &Context,
    key: task::TaskEmail,
) -> Result<task::TaskDraft, CallError> {
    task::draft(&ctx.db, &key)
        .await?
        .ok_or_else(|| CallError::not_found("这封邮件已被删除。"))
}
pub async fn save_task_draft(ctx: &Context, edit: task::DraftEdit) -> Result<(), CallError> {
    Ok(task::save(&ctx.db, &edit, now()).await?)
}
pub async fn check_task_draft(
    ctx: &Context,
    edit: task::DraftEdit,
) -> Result<Vec<check::Problem>, CallError> {
    Ok(task::check(&ctx.db, &edit, now()).await?)
}
pub async fn write_task_draft(
    ctx: &Context,
    edit: task::DraftEdit,
) -> Result<writing::Letter, CallError> {
    let t = sqlx::query!(
        "SELECT model_config_id FROM task WHERE id = ?",
        edit.task_id
    )
    .fetch_one(&ctx.db)
    .await?;
    let d = get_task_draft(
        ctx,
        task::TaskEmail {
            task_id: edit.task_id,
            id: edit.id,
        },
    )
    .await?;
    let chosen = model::selected(&ctx.db, t.model_config_id)
        .await?
        .ok_or(writing::Error::NoModel)?;
    let ids = sqlx::query_scalar!(
        "SELECT material_id FROM task_material WHERE task_id = ? AND role = 'reference'",
        edit.task_id
    )
    .fetch_all(&ctx.db)
    .await?;
    let refs = task::references(&mut *ctx.db.acquire().await?, &ids).await?;
    let _permit = ctx.model_limit.acquire().await;
    Ok(writing::write(
        &ctx.db,
        &ctx.net,
        d.mentor_id,
        &writing::Letter {
            subject: edit.subject,
            body_md: edit.body_md,
        },
        &edit.attachments,
        now(),
        writing::Basis {
            chosen: &chosen,
            references: &refs,
        },
    )
    .await?)
}
pub async fn prepare_task_approval(
    ctx: &Context,
    key: TaskId,
) -> Result<Vec<task::ApprovalCandidate>, CallError> {
    Ok(task::pending(&ctx.db, key.id, now()).await?)
}
pub async fn approve_task_emails(
    ctx: &Context,
    input: task::TaskApprovals,
) -> Result<u64, CallError> {
    if mailbox::list(&ctx.db).await?.is_empty() {
        return Err(CallError::invalid(
            "尚未添加发信的邮箱，请在设置中添加。".into(),
        ));
    }
    let n = task::approve(&ctx.db, &input, now()).await?;
    ctx.sender.wake();
    Ok(n)
}
pub async fn pause_task(ctx: &Context, key: TaskId) -> Result<(), CallError> {
    task::pause(&ctx.db, key.id, now()).await?;
    ctx.generator.wake();
    Ok(())
}
pub async fn resume_task(ctx: &Context, key: TaskId) -> Result<(), CallError> {
    task::resume(&ctx.db, key.id).await?;
    ctx.generator.wake();
    ctx.sender.wake();
    Ok(())
}
pub async fn stop_task(ctx: &Context, key: TaskId) -> Result<(), CallError> {
    task::stop(&ctx.db, key.id, now()).await?;
    ctx.generator.wake();
    Ok(())
}
pub async fn delete_task(ctx: &Context, key: TaskId) -> Result<(), CallError> {
    task::delete(&ctx.db, key.id).await?;
    ctx.generator.wake();
    Ok(())
}
pub async fn remove_task_email(ctx: &Context, key: task::TaskEmail) -> Result<(), CallError> {
    task::remove(&ctx.db, &key, now()).await?;
    ctx.generator.wake();
    Ok(())
}
pub async fn regenerate_task_email(ctx: &Context, key: task::TaskEmail) -> Result<u64, CallError> {
    let n = task::regenerate(&ctx.db, key.task_id, Some(key.id), false, now()).await?;
    ctx.generator.wake();
    Ok(n)
}
pub async fn reapply_task_template(ctx: &Context, key: task::TaskEmail) -> Result<(), CallError> {
    Ok(task::reapply(&ctx.db, &key, now()).await?)
}
pub async fn retry_task_generation(ctx: &Context, key: TaskRetry) -> Result<u64, CallError> {
    let n = task::regenerate(&ctx.db, key.task_id, key.id, true, now()).await?;
    ctx.generator.wake();
    Ok(n)
}

pub async fn task_work(ctx: &Context, _: Nothing) -> Result<Vec<task::TaskSummary>, CallError> {
    Ok(task::work(&ctx.db).await?)
}

#[derive(Debug, Deserialize, TS)]
#[serde(rename_all = "camelCase")]
pub struct TaskMaterial {
    #[serde(flatten)]
    pub email: task::TaskEmail,
    pub material_id: i64,
}
pub async fn attach_task_material(ctx: &Context, input: TaskMaterial) -> Result<bool, CallError> {
    Ok(email::tasks::attach_material(
        &ctx.db,
        input.email.task_id,
        input.email.id,
        input.material_id,
    )
    .await?)
}
