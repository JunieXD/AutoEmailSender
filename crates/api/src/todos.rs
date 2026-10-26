//! Todo operations exposed to hosts and the interface.

use crate::*;

#[derive(Debug, Deserialize, TS)]
pub struct AiReplyTodos {
    pub enabled: bool,
}

#[derive(Debug, Deserialize, TS)]
#[serde(rename_all = "camelCase")]
pub struct NewTodo {
    pub mentor_id: i64,
    #[serde(flatten)]
    pub form: email::todos::TodoForm,
}

#[derive(Debug, Deserialize, TS)]
pub struct TodoEdit {
    pub id: i64,
    #[serde(flatten)]
    pub form: email::todos::TodoForm,
}

#[derive(Debug, Deserialize, TS)]
pub struct TodoId {
    pub id: i64,
}

#[derive(Debug, Deserialize, TS)]
pub struct TodoDone {
    pub id: i64,
    pub done: bool,
}

pub async fn get_ai_reply_todos(ctx: &Context, _: Nothing) -> Result<bool, CallError> {
    Ok(email::todos::enabled(&ctx.db).await?)
}

pub async fn set_ai_reply_todos(ctx: &Context, input: AiReplyTodos) -> Result<(), CallError> {
    if input.enabled {
        let chosen = model::chosen(&ctx.db)
            .await?
            .ok_or_else(|| CallError::invalid("请先在“设置 › AI › 模型”里添加模型。".into()))?;
        if !chosen.agreed {
            return Err(CallError {
                code: ErrorCode::NeedsAgreement,
                message: "请先确认发给模型服务商的内容，再开启回信待办。".into(),
            });
        }
    }
    email::todos::set_enabled(&ctx.db, input.enabled).await?;
    ctx.reply_processor.wake();
    Ok(())
}

pub async fn get_todo_reminders(ctx: &Context, _: Nothing) -> Result<bool, CallError> {
    Ok(profile::todo_reminders(&ctx.db).await?)
}

pub async fn set_todo_reminders(
    ctx: &Context,
    input: NotificationSetting,
) -> Result<(), CallError> {
    profile::set_todo_reminders(&ctx.db, input.on).await?;
    ctx.todo_reminders.wake();
    Ok(())
}

pub async fn add_todo(ctx: &Context, input: NewTodo) -> Result<i64, CallError> {
    let fields = input
        .form
        .check()
        .map_err(|error| CallError::invalid(error.into()))?;
    let id = email::todos::add(&ctx.db, input.mentor_id, &fields, now())
        .await?
        .ok_or_else(|| CallError::invalid("导师不存在或已在回收站里，请刷新后重试。".into()))?;
    ctx.todo_reminders.wake();
    Ok(id)
}

pub async fn update_todo(ctx: &Context, input: TodoEdit) -> Result<(), CallError> {
    let fields = input
        .form
        .check()
        .map_err(|error| CallError::invalid(error.into()))?;
    require_applied(
        email::todos::update(&ctx.db, input.id, &fields, now()).await?,
        "这条待办已删除，请刷新后重试。",
    )?;
    ctx.todo_reminders.wake();
    Ok(())
}

pub async fn set_todo_done(ctx: &Context, input: TodoDone) -> Result<(), CallError> {
    require_applied(
        email::todos::set_done(&ctx.db, input.id, input.done, now()).await?,
        "这条待办已删除，请刷新后重试。",
    )?;
    ctx.todo_reminders.wake();
    Ok(())
}

pub async fn delete_todo(ctx: &Context, input: TodoId) -> Result<(), CallError> {
    require_applied(
        email::todos::delete(&ctx.db, input.id).await?,
        "这条待办已删除，请刷新后重试。",
    )?;
    ctx.todo_reminders.wake();
    Ok(())
}

pub async fn list_todos(
    ctx: &Context,
    input: email::todos::TodoQuery,
) -> Result<email::todos::TodoPage, CallError> {
    Ok(email::todos::list(&ctx.db, &input).await?)
}

pub async fn retry_reply_todos(ctx: &Context, input: ReplyMessage) -> Result<(), CallError> {
    require_applied(
        email::outcome::retry_action(
            &ctx.db,
            input.message_id,
            email::outcome::Action::Todos,
            now(),
        )
        .await?,
        "这封回信没有提取失败的待办可重试。",
    )?;
    ctx.reply_processor.wake();
    Ok(())
}
