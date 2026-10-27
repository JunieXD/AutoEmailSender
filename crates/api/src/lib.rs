//! The operations the UI, the phone and agents call, and the one dispatcher that runs them all
//! (docs/design.md#桌面端). Input is validated here, when it is deserialized.

pub mod agent;
pub mod agent_settings;
use agent::{
    answer_agent_request, describe_operation, get_agent_access, list_agent_requests,
    list_operations, set_agent_access,
};
use agent_settings::*;
pub mod backup;
mod crawling;
mod fact_check;
mod mail_domains;
use fact_check::*;
pub use mail_domains::MailDomains;
pub mod startup;
mod statistics;
mod tasks;
mod todos;
use crawling::*;
use statistics::*;
use std::collections::BTreeMap;
use tasks::*;
use todos::*;

use base64::prelude::{BASE64_STANDARD, Engine};
use serde::{Deserialize, Serialize};
use sqlx::SqlitePool;
use ts_rs::{Config, TS, TypeVisitor};

pub struct Context {
    pub agent_settings: agent_settings::Settings,
    pub agent_access: agent::Access,
    pub writes: db::Writes,
    pub db: SqlitePool,
    pub net: net::Net,
    pub sender: send::Sender,
    pub receiver: receive::Receiver,
    pub matcher: matching::Matcher,
    pub reply_processor: receive::outcome::Processor,
    pub todo_reminders: receive::reminders::Scheduler,
    pub crawler: crawl::runner::Crawler,
    pub generator: task::generation::Generator,
    pub model_limit: model::background::Limit,
    pub mail_domains: MailDomains,
    /// Where material files are kept.
    pub materials: std::path::PathBuf,
}

impl Context {
    pub fn new(db: SqlitePool, net: net::Net, materials: std::path::PathBuf) -> Self {
        let writes = db::Writes::default();
        Self {
            agent_settings: Default::default(),
            agent_access: Default::default(),
            crawler: crawl::runner::Crawler::new(writes.clone()),
            writes,
            db,
            net,
            materials,
            sender: Default::default(),
            receiver: Default::default(),
            matcher: Default::default(),
            reply_processor: Default::default(),
            todo_reminders: Default::default(),
            generator: Default::default(),
            model_limit: Default::default(),
            mail_domains: Default::default(),
        }
    }
}

/// What the UI shows when an operation fails.
#[derive(Debug, Serialize, TS)]
pub struct CallError {
    pub code: ErrorCode,
    pub message: String,
}

#[derive(Debug, Serialize, TS)]
#[serde(rename_all = "snake_case")]
pub enum ErrorCode {
    /// The caller sent something the operation does not accept; a bug in the caller.
    BadInput,
    /// What the caller asked for no longer exists, such as a mentor deleted since the page opened.
    NotFound,
    /// What the user filled in cannot be saved; the message says what to change.
    Invalid,
    /// A service outside the app, such as a mail server, did not do what was asked; the message
    /// says why and what to do.
    External,
    NeedsAgreement,
    Internal,
}

impl CallError {
    fn bad_input(e: impl std::fmt::Display) -> Self {
        CallError {
            code: ErrorCode::BadInput,
            message: format!("参数不正确：{e}"),
        }
    }

    fn invalid(message: String) -> Self {
        CallError {
            code: ErrorCode::Invalid,
            message,
        }
    }

    fn not_found(message: &str) -> Self {
        CallError {
            code: ErrorCode::NotFound,
            message: message.into(),
        }
    }

    /// Details go to the log; the user gets what to do next.
    fn internal(e: impl std::fmt::Display) -> Self {
        tracing::error!("{e}");
        CallError {
            code: ErrorCode::Internal,
            message: "处理时出错。请重试；仍然出错时，导出日志发给开发者。".into(),
        }
    }
}

impl From<sqlx::Error> for CallError {
    fn from(e: sqlx::Error) -> Self {
        CallError::internal(format!("database: {e}"))
    }
}

/// A change that found nothing to change, as when the thing is gone, as an error saying so.
fn require_applied(applied: bool, message: &str) -> Result<(), CallError> {
    if applied {
        Ok(())
    } else {
        Err(CallError::not_found(message))
    }
}

macro_rules! operation_confirmation {
    (none) => {
        agent::OperationConfirmation::None
    };
    (confirm) => {
        agent::OperationConfirmation::Always
    };
    (confirm_if $condition:literal) => {
        agent::OperationConfirmation::Conditional($condition.into())
    };
}

/// Declares the catalog alongside the types and handlers, so all callers see the same operations.
macro_rules! operations {
    ($($group:literal {
        $(
            #[doc = $summary:literal]
            $(#[doc = $details:expr])*
            $op:literal [$confirmation:ident $($condition:literal)?]
                => $handler:ident($input:ty) -> $output:ty;
        )*
    })*) => {
        pub async fn call(
            ctx: &Context,
            op: &str,
            input: serde_json::Value,
        ) -> Result<serde_json::Value, CallError> {
            match op {
                $($($op => {
                    let input = serde_path_to_error::deserialize::<_, $input>(input)
                        .map_err(CallError::bad_input)?;
                    serde_json::to_value($handler(ctx, input).await?).map_err(CallError::internal)
                })*)*
                _ => Err(CallError::bad_input(format!("没有操作 {op}"))),
            }
        }

        fn operation_catalog() -> Vec<agent::OperationSummary> {
            vec![$($(
                agent::OperationSummary {
                    name: $op.into(),
                    summary: $summary.trim().into(),
                    group: $group.into(),
                    confirmation: operation_confirmation!($confirmation $($condition)?),
                },
            )*)*]
                .into_iter()
                .filter(|op| agent::visible(&op.name))
                .collect()
        }

        fn operation_description(name: &str) -> Result<agent::OperationDescription, CallError> {
            if !agent::visible(name) {
                return Err(CallError::invalid("这个操作只供应用界面使用".into()));
            }
            match name {
                $($($op => {
                    let cfg = Config::new().with_large_int("number");
                    let mut decls = BTreeMap::new();
                    collect::<$input>(&cfg, &mut decls);
                    collect::<$output>(&cfg, &mut decls);
                    let mut typescript = decls.values()
                        .map(|decl| format!("export {decl}\n\n"))
                        .collect::<String>();
                    typescript.push_str(&format!(
                        "export type Input = {};\nexport type Output = {};\n",
                        <$input>::name(&cfg),
                        <$output>::name(&cfg),
                    ));
                    let details = concat!($($details, "\n",)*).trim();
                    Ok(agent::OperationDescription {
                        name: $op.into(),
                        summary: $summary.trim().into(),
                        details: if details.is_empty() {
                            None
                        } else {
                            Some(details.into())
                        },
                        confirmation: operation_confirmation!($confirmation $($condition)?),
                        typescript,
                    })
                })*)*
                _ => Err(CallError::bad_input(format!("没有操作 {name}"))),
            }
        }

        /// TypeScript declarations of every type the operations use, and the `Operations` map the
        /// UI's typed `call` is built on.
        pub fn typescript() -> String {
            let cfg = Config::new().with_large_int("number");
            let mut decls = BTreeMap::new();
            collect::<CallError>(&cfg, &mut decls);
            collect::<crawl::runner::CrawlFinished>(&cfg, &mut decls);
            collect::<matching::MatchFinished>(&cfg, &mut decls);
            collect::<task::generation::DraftsFinished>(&cfg, &mut decls);
            collect::<ExportMentors>(&cfg, &mut decls);
            collect::<backup::RestoreInfo>(&cfg, &mut decls);
            $($(
                collect::<$input>(&cfg, &mut decls);
                collect::<$output>(&cfg, &mut decls);
            )*)*
            let mut out = String::from(
                "// Generated by `cargo xtask types` from crates/api. Do not edit.\n\n",
            );
            for decl in decls.values() {
                out.push_str(&format!("export {decl}\n\n"));
            }
            out.push_str(&format!(
                "export const TAG_COLORS = {:?} as const;\n\n",
                mentor::tags::COLORS,
            ));
            out.push_str("export interface Operations {\n");
            $($(
                out.push_str(&format!(
                    "  {}: {{ input: {}; output: {} }};\n",
                    $op,
                    <$input>::name(&cfg),
                    <$output>::name(&cfg),
                ));
            )*)*
            out.push_str("}\n");
            out.lines().map(str::trim_end).collect::<Vec<_>>().join("\n") + "\n"
        }
    };
}

pub async fn call_agent(
    ctx: &Context,
    op: &str,
    mut input: serde_json::Value,
    waiting: impl FnOnce(&agent::Description),
    cancelled: impl std::future::Future<Output = ()>,
) -> Result<serde_json::Value, CallError> {
    let mut log = AgentCallLog { op, success: false };
    let result = match op {
        // An agent does not decide whether agents may connect.
        op if !agent::visible(op) => Err(CallError::invalid("这个操作只供应用界面使用".into())),
        _ => {
            tokio::pin!(cancelled);
            let description = tokio::select! {
                biased;
                () = &mut cancelled => return Err(CallError::invalid("这个请求已经失效".into())),
                description = agent::confirmation(ctx, op, &input) => description?,
            };
            if let Some(description) = description {
                let rendered = ctx
                    .agent_access
                    .confirm(op, description.clone(), waiting, &mut cancelled)
                    .await?;
                if let Some(current) = agent::confirmation(ctx, op, &input).await?
                    && current != description
                {
                    tracing::info!(op, "agent confirmation changed before execution");
                    return Err(CallError::invalid(
                        "等你确认期间情况变了，请重新发起".into(),
                    ));
                }
                match op {
                    "approveTaskEmails" => {
                        let approvals = input["emails"].as_array_mut().unwrap_or_else(|| {
                            unreachable!("task approvals were parsed before confirmation")
                        });
                        for (approval, rendered) in approvals.iter_mut().zip(rendered) {
                            approval["html"] = rendered.html.into();
                            approval["text"] = rendered.text.into();
                        }
                    }
                    "approveFirstEmail" | "approveFollowUp" | "approveReply" => {
                        let rendered = rendered.into_iter().next().unwrap_or_else(|| {
                            unreachable!("the answer's rendering count was checked")
                        });
                        input["html"] = rendered.html.into();
                        input["text"] = rendered.text.into();
                    }
                    _ => {}
                }
            }
            call(ctx, op, input).await
        }
    };
    log.success = result.is_ok();
    if log.success {
        ctx.agent_access.emit(agent::Event::Called);
    }
    result
}

// Cancellation before execution must also record the call's failure.
struct AgentCallLog<'a> {
    op: &'a str,
    success: bool,
}

impl Drop for AgentCallLog<'_> {
    fn drop(&mut self) {
        tracing::info!(op = self.op, success = self.success, "agent call");
    }
}

operations! {
    "Agent 连接" {
        /// 读取命令行工具的安装状态、命令名和绝对路径。
        "getAgentCli" [none] => get_agent_cli(Nothing) -> AgentCli;
        /// 将 autoemail 安装到终端 PATH。
        "installAgentCli" [none] => install_agent_cli(Nothing) -> AgentCli;
        /// 撤回应用添加的命令行链接或用户 PATH 项。
        "uninstallAgentCli" [none] => uninstall_agent_cli(Nothing) -> AgentCli;
        /// 预览指定客户端的 MCP 配置路径、完整服务项及是否替换已有项。
        "previewMcpConfig" [none] => preview_mcp_config(McpConfigQuery) -> McpConfigPreview;
        /// 确认后写入指定客户端最近预览的配置，原文件变化时需重新预览。
        "writeMcpConfig" [none] => write_mcp_config(McpConfigQuery) -> ();
        /// 读取供其他 MCP 工具复制的 JSON 配置。
        "mcpConfigText" [none] => mcp_config_text(Nothing) -> String;
        /// 读取是否允许 Agent 连接。
        "getAgentAccess" [none] => get_agent_access(Nothing) -> bool;
        /// 设置是否允许 Agent 连接，enabled 为是否开启。
        "setAgentAccess" [none] => set_agent_access(agent::AgentAccess) -> ();
        /// 列出等待用户回答的 Agent 请求。
        "listAgentRequests" [none] => list_agent_requests(Nothing) -> Vec<agent::AgentRequest>;
        /// 回答指定 id 的 Agent 请求，allow 为是否允许。
        "answerAgentRequest" [none] => answer_agent_request(agent::AgentAnswer) -> ();
        /// 按组列出 Agent 可调用的操作、说明和确认方式。
        "listOperations" [none] => list_operations(Nothing) -> Vec<agent::OperationSummary>;
        /// 按 name 读取操作说明、确认方式及输入输出的 TypeScript 类型声明。
        "describeOperation" [none] => describe_operation(agent::OperationName)
            -> agent::OperationDescription;
    }
    "概览" {
        /// 读取后台保存失败的提示，没有失败时返回 null。
        "storageFailure" [none] => storage_failure(Nothing) -> Option<String>;
        /// 按筛选条件统计联系导师的进展。
        "contactStats" [none] => contact_stats(StatsQuery) -> stats::ContactProgress;
        /// 统计导师库概况。
        "mentorStats" [none] => mentor_stats(Nothing) -> stats::MentorOverview;
        /// 统计指定时段的模型用量。
        "tokenStats" [none] => token_stats(StatsPeriod) -> stats::TokenUsage;
        /// 查询今天待处理的邮件和任务。
        "todayWork" [none] => today_work(TodayWorkQuery) -> stats::TodayWork;
    }
    "抓取与候选" {
        /// 把你从学校官网整理的导师送进候选区，由用户审核后入库；收集导师前先读这个操作的说明。
        #[doc = include_str!("submit-candidates.md")]
        "submitCandidates" [none] => submit_candidates(crawl::submit::SubmitCandidates)
            -> crawl::submit::Submitted;
        /// 检查并整理抓取网址，urls 为按行分隔的网址。
        "checkCrawlUrls" [none] => check_crawl_urls(CrawlUrls) -> Vec<String>;
        /// 查询指定时间范围内抓取的后台进度。
        "crawlWork" [none] => crawl_work(CrawlWorkQuery) -> Vec<crawl::queue::CrawlSummary>;
        /// 从给定网址启动导师抓取。
        "startCrawl" [none] => start_crawl(CrawlStart) -> CrawlId;
        /// 分页列出抓取记录。
        "listCrawls" [none] => list_crawls(CrawlQuery) -> crawl::queue::CrawlList;
        /// 列出可用于筛选的抓取。
        "crawlOptions" [none] => crawl_options(Nothing) -> Vec<crawl::queue::CrawlOption>;
        /// 读取指定 id 的抓取详情。
        "getCrawl" [none] => get_crawl(CrawlDetailQuery) -> crawl::queue::CrawlDetail;
        /// 暂停指定抓取。
        "pauseCrawl" [none] => pause_crawl(CrawlId) -> ();
        /// 继续指定抓取。
        "resumeCrawl" [none] => resume_crawl(CrawlId) -> ();
        /// 取消指定抓取。
        "cancelCrawl" [none] => cancel_crawl(CrawlId) -> ();
        /// 重试抓取中失败或指定的页面。
        "retryCrawl" [none] => retry_crawl(CrawlRetry) -> u64;
        /// 永久删除指定抓取及尚未审核的候选资料。
        "deleteCrawl" [confirm] => delete_crawl(CrawlId) -> ();
        /// 预览指定候选导师可补充的资料。
        "previewEnrich" [none] => preview_enrich(CandidateIds) -> crawl::enrich::EnrichPlan;
        /// 为指定候选导师补充资料并返回处理计划。
        "enrichCandidates" [none] => enrich_candidates(CandidateIds) -> crawl::enrich::EnrichPlan;
        /// 按筛选条件分页列出候选导师。
        "listCandidates" [none] => list_candidates(crawl::review::CandidateQuery)
            -> crawl::review::CandidatePage;
        /// 读取指定 id 的候选导师详情。
        "getCandidate" [none] => get_candidate(CrawlId) -> crawl::review::CandidateDetail;
        /// 预览候选导师入库，包含可能重复的导师。
        "previewCandidates" [none] => preview_candidates(crawl::review::ReviewInput)
            -> mentor::import::Preview;
        /// 按审核决定将候选导师保存到导师库。
        "acceptCandidates" [confirm_if "候选入库会更改已批准邮件的收件地址时"] =>
            accept_candidates(crawl::review::ReviewInput) -> crawl::review::Reviewed;
        /// 拒绝指定 id 列表中的候选导师。
        "rejectCandidates" [none] => reject_candidates(CandidateIds) -> u64;
    }
    "发送计划" {
        /// 估算给定发送窗口的容量。
        "scheduleCapacity" [none] => schedule_capacity(email::schedule::Window)
            -> email::schedule::Capacity;
        /// 预览任务按给定时间安排邮件的结果。
        "previewTaskSchedule" [none] => preview_task_schedule(task::ApprovalPreview)
            -> task::SchedulePreview;
        /// 更改指定任务的发送时间安排。
        "changeTaskSchedule" [confirm] => change_task_schedule(task::ScheduleEdit) -> u64;
        /// 更改指定邮件的发送时间，plannedAt 为 UTC 毫秒时间戳。
        "rescheduleEmail" [confirm] => reschedule_email(RescheduleEmail) -> ();
        /// 立即发送指定 id 的已批准邮件。
        "sendEmailNow" [confirm] => send_email_now(EmailId) -> ();
        /// 按筛选条件分页读取发送计划。
        "sendingPlan" [none] => sending_plan(send::plan::PlanQuery) -> send::plan::PlanPage;
        /// 读取已排期邮件的后台进度。
        "scheduledWork" [none] => scheduled_work(Nothing) -> send::plan::ScheduledWork;
    }
    "任务" {
        /// 预览新任务的导师范围、草稿数量和模型用量。
        "planTask" [none] => plan_task(task::NewTask) -> task::TaskPlan;
        /// 创建联系任务并安排生成草稿。
        "createTask" [none] => create_task(task::NewTask) -> TaskId;
        /// 读取各任务的草稿生成进度。
        "taskWork" [none] => task_work(Nothing) -> Vec<task::TaskWork>;
        /// 按筛选条件分页列出任务。
        "listTasks" [none] => list_tasks(task::TaskQuery) -> task::TaskPage;
        /// 读取指定 id 的任务概况。
        "getTask" [none] => get_task(TaskId) -> task::TaskSummary;
        /// 按任务和筛选条件分页列出草稿。
        /// 会在后台查询收件地址的 DNS 记录，以检查是否有邮件服务器。
        "listTaskDrafts" [none] => list_task_drafts(task::DraftQuery) -> task::DraftPage;
        /// 读取任务中指定邮件的草稿详情。
        "getTaskDraft" [none] => get_task_draft(task::TaskEmail) -> task::TaskDraft;
        /// 向任务草稿添加指定材料，返回是否添加成功。
        "attachTaskMaterial" [none] => attach_task_material(TaskMaterial) -> bool;
        /// 保存任务中指定邮件的草稿。
        "saveTaskDraft" [none] => save_task_draft(task::DraftEdit) -> ();
        /// 检查给定任务草稿，返回需要处理的问题。
        "checkTaskDraft" [none] => check_task_draft(task::DraftEdit) -> Vec<check::Problem>;
        /// 根据给定任务草稿使用模型生成邮件。
        "writeTaskDraft" [none] => write_task_draft(task::DraftEdit) -> writing::Letter;
        /// 检查并列出任务中可批准的草稿。
        /// 会在后台查询收件地址的 DNS 记录，以检查是否有邮件服务器。
        "prepareTaskApproval" [none] => prepare_task_approval(TaskId)
            -> Vec<task::ApprovalCandidate>;
        /// 批准选中的任务邮件，按各自发送时间发出。
        "approveTaskEmails" [confirm] => approve_task_emails(task::TaskApprovals) -> u64;
        /// 暂停指定任务：不再生成草稿，已批准的邮件也先不发，仍可以审核和批准。
        "pauseTask" [none] => pause_task(TaskId) -> ();
        /// 恢复指定任务：接着生成排队的草稿（会调用模型、产生用量），并发出已批准的邮件。
        "resumeTask" [confirm] => resume_task(TaskId) -> ();
        /// 停止指定任务：没发出的邮件全部取消（记录保留），任务不能再继续。
        "stopTask" [none] => stop_task(TaskId) -> ();
        /// 永久删除指定任务和其中没发出的邮件（包括已批准的），发出过的留在导师的往来里。
        "deleteTask" [confirm] => delete_task(TaskId) -> ();
        /// 从任务中移除指定邮件。
        "removeTaskEmail" [none] => remove_task_email(task::TaskEmail) -> ();
        /// 安排重新生成任务中的指定草稿。
        "regenerateTaskEmail" [none] => regenerate_task_email(task::TaskEmail) -> u64;
        /// 为任务中的指定草稿重新套用模板。
        "reapplyTaskTemplate" [none] => reapply_task_template(task::TaskEmail) -> ();
        /// 重试任务中失败或未完成的草稿生成。
        "retryTaskGeneration" [none] => retry_task_generation(TaskRetry) -> u64;
        /// 读取指定任务的配置，用于再次创建任务。
        "restartTask" [none] => restart_task(TaskId) -> task::NewTask;
    }
    "收信与通知" {
        /// 按筛选条件分页列出导师会话。
        "listConversations" [none] => list_conversations(mentor::conversations::ConversationQuery)
            -> mentor::conversations::ConversationPage;
        /// 读取未读回信数量。
        "unreadCount" [none] => unread_count(Nothing) -> i64;
        /// 立即检查所有邮箱的新回信。
        "receiveNow" [none] => receive_now(Nothing) -> ReceiveStatus;
        /// 读取最近一次收信时间。
        "receiveStatus" [none] => receive_status(Nothing) -> ReceiveStatus;
        /// 读取后台工作通知开关。
        "getBackgroundNotifications" [none] => get_background_notifications(Nothing) -> bool;
        /// 设置后台工作通知，on 为是否开启。
        "setBackgroundNotifications" [none] => set_background_notifications(NotificationSetting)
            -> ();
        /// 读取 AI 邮件事实核对开关。
        "getAiFactCheck" [none] => get_ai_fact_check(Nothing) -> bool;
        /// 设置 AI 邮件事实核对，on 为是否开启。
        "setAiFactCheck" [none] => set_ai_fact_check(NotificationSetting) -> ();
        /// 用模型核对指定的待审核邮件并保存结果，核对开关关闭时返回 null。
        /// 会将邮件、个人资料和参考材料发给已同意的模型服务，并产生用量；读取已有结果请用草稿详情。
        "factCheckEmail" [none] => fact_check_email(EmailId)
            -> Option<email::fact_check::FactCheck>;
        /// 读取新回信通知开关。
        "getReplyNotifications" [none] => get_reply_notifications(Nothing) -> bool;
        /// 设置新回信通知，on 为是否开启。
        "setReplyNotifications" [none] => set_reply_notifications(NotificationSetting) -> ();
    }
    "匹配" {
        /// 估算指定导师的匹配数量和模型用量，redo 表示是否重算已有结果。
        "planMatching" [none] => plan_matching(MatchBatch) -> matching::MatchEstimate;
        /// 安排指定导师进行匹配，redo 表示是否重算已有结果。
        "startMatching" [none] => start_matching(MatchBatch) -> u64;
        /// 为指定 id 的导师重新计算匹配度。
        "matchMentor" [none] => match_mentor(MentorId) -> u64;
        /// 读取匹配队列进度。
        "matchingProgress" [none] => matching_progress(Nothing) -> mentor::matching::MatchProgress;
        /// 暂停导师匹配。
        "pauseMatching" [none] => pause_matching(Nothing) -> ();
        /// 继续导师匹配。
        "resumeMatching" [none] => resume_matching(Nothing) -> ();
        /// 取消尚未完成的导师匹配。
        "cancelMatching" [none] => cancel_matching(Nothing) -> ();
        /// 重试失败的导师匹配。
        "retryMatching" [none] => retry_matching(Nothing) -> u64;
    }
    "导师" {
        /// 预览表格导入，包含字段映射和重复导师处理结果。
        "previewImport" [none] => preview_import(PreviewImport) -> mentor::import::Preview;
        /// 按字段映射和重复处理决定导入导师表格。
        "importMentors" [confirm_if "导入会更改已批准邮件的收件地址时"] =>
            import_mentors(ImportMentors) -> mentor::import::Imported;
        /// 按筛选、排序和分页条件读取导师列表。
        "listMentors" [none] => list_mentors(mentor::MentorQuery) -> mentor::MentorPage;
        /// 读取导师筛选可用的学校、院系和标签等选项。
        "mentorFilterOptions" [none] => mentor_filter_options(Nothing)
            -> mentor::filters::MentorFilterOptions;
        /// 读取指定 id 的导师完整资料。
        "getMentor" [none] => get_mentor(MentorId) -> mentor::MentorDetail;
        /// 保存指定导师的备注。
        "setMentorNote" [none] => set_mentor_note(MentorNote) -> ();
        /// 添加一位导师，返回新导师的 id。
        "addMentor" [none] => add_mentor(mentor::MentorForm) -> MentorId;
        /// 更新指定导师的资料。
        "updateMentor" [confirm_if "更改已批准邮件的收件地址时"] =>
            update_mentor(MentorEdit) -> mentor::MentorDetail;
        /// 设置联系导师使用的邮箱，mailboxId 为 null 时取消指定。
        "setMentorMailbox" [confirm_if "这位导师有已批准还没发出的邮件，且发件邮箱改变时"] =>
            set_mentor_mailbox(MentorMailbox) -> mentor::MentorDetail;
        /// 为指定导师添加邮箱地址，address 为新地址。
        "addMentorEmail" [confirm_if "新地址成为已批准邮件的收件地址时"] =>
            add_mentor_email(MentorAddress) -> mentor::MentorDetail;
        /// 预览把 other 导师并入 keep 导师的结果。
        "previewMerge" [none] => preview_merge(MentorMerge) -> mentor::merge::Preview;
        /// 把 other 导师合并到 keep 导师。
        "mergeMentors" [confirm] => merge_mentors(MentorMerge) -> ();
        /// 按筛选和分页条件读取回收站导师。
        "listTrash" [none] => list_trash(mentor::trash::TrashQuery) -> mentor::MentorPage;
        /// 将指定 id 列表中的导师移入回收站。
        "trashMentors" [none] => trash_mentors(MentorIds) -> ();
        /// 从回收站恢复指定 id 列表中的导师。
        "restoreMentors" [none] => restore_mentors(MentorIds) -> ();
        /// 永久删除指定 id 列表中的导师。
        "deleteMentors" [confirm] => delete_mentors(MentorIds) -> ();
    }
    "标签" {
        /// 为指定导师添加或移除标签。
        "tagMentors" [none] => tag_mentors(TagMentors) -> ();
        /// 列出全部导师标签。
        "listTags" [none] => list_tags(Nothing) -> Vec<mentor::tags::Tag>;
        /// 创建标签，并可同时添加到 mentorIds 指定的导师。
        "createTag" [none] => create_tag(NewTag) -> mentor::tags::Tag;
        /// 更改指定标签的名称。
        "renameTag" [none] => rename_tag(TagName) -> ();
        /// 更改指定标签的颜色。
        "recolorTag" [none] => recolor_tag(TagColor) -> ();
        /// 调整指定标签的显示顺序。
        "moveTag" [none] => move_tag(TagMove) -> ();
        /// 永久删除指定标签。
        "deleteTag" [confirm] => delete_tag(TagId) -> ();
    }
    "邮箱" {
        /// 列出支持的邮箱服务商及连接设置。
        "listProviders" [none] => list_providers(Nothing)
            -> &'static [mailbox::providers::Provider];
        /// 列出已配置的邮箱，不包含授权码。
        "listMailboxes" [none] => list_mailboxes(Nothing) -> Vec<mailbox::Mailbox>;
        /// 读取多邮箱轮换设置。
        "getRotation" [none] => get_rotation(Nothing) -> profile::Rotation;
        /// 保存多邮箱轮换设置。
        "setRotation" [none] => set_rotation(profile::Rotation) -> ();
        /// 添加发信和收信邮箱，返回新邮箱的 id。
        "addMailbox" [none] => add_mailbox(NewMailbox) -> MailboxId;
        /// 修改指定邮箱设置，可填写新的授权码。
        "updateMailbox" [confirm_if "修改未到期暂停中的邮箱设置、会恢复发信时"] => update_mailbox(MailboxEdit) -> ();
        /// 立即恢复指定邮箱的发信。
        "resumeMailbox" [confirm] => resume_mailbox(MailboxId) -> ();
        /// 永久删除指定邮箱。
        "deleteMailbox" [confirm] => delete_mailbox(MailboxId) -> ();
        /// 测试给定邮箱配置能否连接发信服务器。
        "testSending" [none] => test_sending(MailboxTest) -> ();
        /// 测试给定邮箱配置能否连接收信服务器。
        "testReceiving" [none] => test_receiving(MailboxTest) -> ();
        /// 通过指定邮箱向它自己的地址发送测试信。
        "sendTestMail" [none] => send_test_mail(MailboxId) -> ();
    }
    "个人资料" {
        /// 读取学生个人资料。
        "getProfile" [none] => get_profile(Nothing) -> profile::Profile;
        /// 保存学生个人资料。
        "updateProfile" [none] => update_profile(profile::ProfileForm) -> profile::Profile;
        /// 标记首次使用引导已完成。
        "finishOnboarding" [none] => finish_onboarding(Nothing) -> ();
        /// 读取首次使用引导中的测试邮件草稿。
        "testLetter" [none] => test_letter(Nothing) -> Option<TestLetter>;
        /// 向用户自己的邮箱发送引导测试邮件。
        "sendTestLetter" [none] => send_test_letter(SendTestLetter) -> ();
        /// 读取邮件写作风格设置。
        "getLetterStyle" [none] => get_letter_style(Nothing) -> profile::LetterStyle;
        /// 保存邮件写作风格设置。
        "updateLetterStyle" [none] => update_letter_style(profile::LetterStyle) -> ();
    }
    "材料" {
        /// 列出个人材料及其用途。
        "listMaterials" [none] => list_materials(Nothing) -> Vec<material::Material>;
        /// 保存 Base64 编码的材料文件并提取文字，返回材料 id。
        "addMaterial" [none] => add_material(UploadedFile) -> MaterialId;
        /// 设置指定材料用于匹配、写信或作为附件。
        "setMaterialPurpose" [none] => set_material_purpose(MaterialPurpose) -> ();
        /// 读取指定材料提取出的文字。
        "getMaterialText" [none] => get_material_text(MaterialId) -> Option<String>;
        /// 永久删除指定材料。
        "deleteMaterial" [confirm] => delete_material(MaterialId) -> ();
    }
    "模板" {
        /// 列出邮件模板支持的占位符。
        "listPlaceholders" [none] => list_placeholders(Nothing) -> &'static [&'static str];
        /// 列出邮件模板摘要。
        "listTemplates" [none] => list_templates(Nothing) -> Vec<template::TemplateSummary>;
        /// 读取指定 id 的完整邮件模板。
        "getTemplate" [none] => get_template(TemplateId) -> template::Template;
        /// 解析 Base64 编码的 Word 文件为邮件模板内容。
        "importWordTemplate" [none] => import_word_template(UploadedFile) -> WordFile;
        /// 创建邮件模板，返回模板 id。
        "addTemplate" [none] => add_template(template::TemplateForm) -> TemplateId;
        /// 更新指定邮件模板。
        "updateTemplate" [none] => update_template(TemplateEdit) -> ();
        /// 永久删除指定邮件模板。
        "deleteTemplate" [confirm] => delete_template(TemplateId) -> ();
        /// 设置默认邮件模板，id 为 null 时取消默认。
        "setDefaultTemplate" [none] => set_default_template(DefaultTemplate) -> ();
        /// 将指定模板填入指定导师的资料。
        "applyTemplate" [none] => apply_template(TemplateForMentor) -> FilledTemplate;
        /// 为指定导师填入默认模板，没有默认模板时返回 null。
        "applyDefaultTemplate" [none] => apply_default_template(MentorId) -> Option<FilledTemplate>;
    }
    "追问" {
        /// 读取导师追问入口及可用状态。
        "getFollowUpEntry" [none] => get_follow_up_entry(MentorId)
            -> email::follow_up::FollowUpEntry;
        /// 读取指定导师已有的追问草稿。
        "getFollowUpDraft" [none] => get_follow_up_draft(MentorId)
            -> Option<email::follow_up::FollowUpDraft>;
        /// 为指定导师创建追问草稿。
        "createFollowUpDraft" [none] => create_follow_up_draft(MentorId)
            -> email::follow_up::FollowUpDraft;
        /// 保存指定导师的追问草稿。
        "saveFollowUpDraft" [none] => save_follow_up_draft(FollowUpEdit)
            -> Option<email::follow_up::FollowUpDraft>;
        /// 丢弃指定 id 的追问草稿。
        "discardFollowUpDraft" [none] => discard_follow_up_draft(EmailId) -> ();
        /// 批准指定导师的追问草稿并安排发送。
        "approveFollowUp" [confirm] => approve_follow_up(ReplyEmail) -> ();
        /// 根据给定草稿使用模型撰写追问邮件。
        "writeFollowUp" [none] => write_follow_up(FirstDraftEdit) -> writing::Letter;
        /// 检查给定追问草稿，返回需要处理的问题。
        "checkFollowUpDraft" [none] => check_follow_up_draft(ReplyDraftEdit) -> Vec<check::Problem>;
    }
    "回复" {
        /// 读取指定导师的回复草稿。
        "getReplyDraft" [none] => get_reply_draft(MentorId) -> Option<email::ReplyDraft>;
        /// 读取导师可回复的目标邮件，支持指定回信。
        "getReplyTarget" [none] => get_reply_target(ReplyTargetQuery) -> Option<email::ReplyTarget>;
        /// 保存导师回复草稿，空内容且无附件时删除已有草稿。
        "saveReplyDraft" [confirm_if "空保存会删除已有回复草稿时"] =>
            save_reply_draft(ReplyDraftEdit) -> Option<email::ReplyDraft>;
        /// 检查给定回复草稿，返回需要处理的问题。
        "checkReplyDraft" [none] => check_reply_draft(ReplyDraftEdit) -> Vec<check::Problem>;
        /// 批准指定导师的回复草稿并安排发送。
        "approveReply" [confirm] => approve_reply(ReplyEmail) -> ();
        /// 根据给定草稿使用模型撰写回复邮件。
        "writeReply" [none] => write_reply(ReplyDraftEdit) -> writing::Letter;
        /// 向指定导师的邮件草稿添加材料。
        "attachDraftMaterial" [none] => attach_draft_material(DraftMaterial) -> bool;
    }
    "首封" {
        /// 读取指定导师独立于任务的首封草稿。
        "getFirstDraft" [none] => get_first_draft(MentorId) -> Option<email::FirstDraft>;
        /// 保存导师首封草稿，主题和正文均为空时删除已有草稿。
        "saveFirstDraft" [confirm_if "空保存会删除已有首封草稿时"] =>
            save_first_draft(FirstDraftEdit) -> Option<email::FirstDraft>;
        /// 检查给定首封草稿，返回需要处理的问题。
        "checkFirstDraft" [none] => check_first_draft(FirstDraftEdit) -> Vec<check::Problem>;
        /// 根据给定草稿使用模型撰写首封邮件。
        "writeLetter" [none] => write_letter(FirstDraftEdit) -> writing::Letter;
        /// 查询邮箱域名的 DNS 记录，检查是否有邮件服务器，返回需要处理的问题。
        "checkMailDomain" [none] => check_mail_domain(MailAddress) -> Option<check::Problem>;
        /// 批准指定导师的首封草稿并安排发送。
        "approveFirstEmail" [confirm] => approve_first_email(FirstEmail) -> ();
    }
    "待办与回信处理" {
        /// 读取 AI 从回信提取待办的开关。
        "getAiReplyTodos" [none] => get_ai_reply_todos(Nothing) -> bool;
        /// 设置 AI 从回信提取待办的开关。
        "setAiReplyTodos" [none] => set_ai_reply_todos(AiReplyTodos) -> ();
        /// 读取待办提醒开关。
        "getTodoReminders" [none] => get_todo_reminders(Nothing) -> bool;
        /// 设置待办提醒，on 为是否开启。
        "setTodoReminders" [none] => set_todo_reminders(NotificationSetting) -> ();
        /// 为指定导师添加待办，返回待办 id。
        "addTodo" [none] => add_todo(NewTodo) -> i64;
        /// 更新指定待办的内容和时间。
        "updateTodo" [none] => update_todo(TodoEdit) -> ();
        /// 设置指定待办是否完成。
        "setTodoDone" [none] => set_todo_done(TodoDone) -> ();
        /// 永久删除指定待办。
        "deleteTodo" [confirm] => delete_todo(TodoId) -> ();
        /// 按筛选和分页条件读取待办。
        "listTodos" [none] => list_todos(email::todos::TodoQuery) -> email::todos::TodoPage;
        /// 安排使用模型重新从指定回信提取待办。
        "retryReplyTodos" [none] => retry_reply_todos(ReplyMessage) -> ();
        /// 读取 AI 识别回信结果的开关。
        "getAiReplyOutcome" [none] => get_ai_reply_outcome(Nothing) -> bool;
        /// 设置 AI 识别回信结果的开关。
        "setAiReplyOutcome" [none] => set_ai_reply_outcome(AiReplyOutcome) -> ();
        /// 设置指定回信的联系结果。
        "setReplyOutcome" [none] => set_reply_outcome(ReplyOutcomeEdit) -> ();
        /// 安排使用模型重新识别指定回信的联系结果。
        "retryReplyOutcome" [none] => retry_reply_outcome(ReplyMessage) -> ();
        /// 读取 AI 回信处理队列状态。
        "replyProcessing" [none] => reply_processing(Nothing) -> email::outcome::ReplyProcessing;
        /// 继续已暂停的 AI 回信处理，会调用已同意的模型服务。
        "resumeReplyProcessing" [none] => resume_reply_processing(Nothing) -> ();
    }
    "往来邮件" {
        /// 读取指定导师的往来邮件和状态记录。
        "listCorrespondence" [none] => list_correspondence(MentorId) -> Vec<Entry>;
        /// 将指定导师的回信标为已读。
        "markRead" [none] => mark_read(MentorId) -> ();
        /// 将尚未发出的邮件撤回为草稿。
        "withdrawEmail" [none] => withdraw_email(EmailId) -> ();
        /// 重试发送指定 id 的失败邮件。
        "retryEmail" [confirm] => retry_email(EmailId) -> ();
        /// 确认指定邮件没有发出并结束待确认的取消状态。
        "confirmUnsentEmail" [none] => confirm_unsent_email(EmailId) -> ();
        /// 按原内容重新发送指定 id 的邮件。
        "resendEmail" [confirm] => resend_email(EmailId) -> ();
    }
    "模型" {
        /// 列出支持的模型服务商。
        "listModelProviders" [none] => list_model_providers(Nothing)
            -> &'static [model::providers::Provider];
        /// 列出模型配置，不包含 API Key。
        "listModelConfigs" [none] => list_model_configs(Nothing) -> Vec<model::ModelConfig>;
        /// 添加模型配置，返回配置 id。
        "addModelConfig" [none] => add_model_config(NewModelConfig) -> ModelConfigId;
        /// 更新指定模型配置，可填写新的 API Key。
        "updateModelConfig" [none] => update_model_config(ModelConfigEdit) -> ();
        /// 永久删除指定模型配置。
        "deleteModelConfig" [confirm] => delete_model_config(ModelConfigId) -> ();
        /// 设置默认模型配置。
        "setDefaultModel" [none] => set_default_model(ModelConfigId) -> ();
        /// 同意将数据发送到指定模型服务商和地址。
        /// 这会允许应用在使用该模型时发送导师、邮件或材料等相关数据。
        "agreeModel" [confirm] => agree_model(ModelAgreement) -> ();
        /// 向给定模型服务请求可用模型名称。
        "listProviderModels" [none] => list_provider_models(ModelSource) -> Vec<String>;
        /// 向给定模型服务发送测试请求，检查模型能否响应，可能产生用量。
        "testModel" [none] => test_model(ModelTest) -> ();
    }
}

#[derive(Debug, Deserialize, TS)]
pub struct MatchBatch {
    pub ids: Vec<i64>,
    pub redo: bool,
}

async fn plan_matching(
    ctx: &Context,
    input: MatchBatch,
) -> Result<matching::MatchEstimate, CallError> {
    Ok(matching::plan(&ctx.db, &input.ids, input.redo).await?)
}
async fn start_matching(ctx: &Context, input: MatchBatch) -> Result<u64, CallError> {
    matching::ready(&ctx.db).await?;
    let count = mentor::matching::enqueue(&ctx.db, &input.ids, false, input.redo, now()).await?;
    ctx.matcher.wake();
    Ok(count)
}
async fn match_mentor(ctx: &Context, input: MentorId) -> Result<u64, CallError> {
    matching::ready(&ctx.db).await?;
    let m = get_mentor(ctx, MentorId { id: input.id }).await?;
    if m.trashed {
        return Err(CallError::invalid("请先从回收站恢复这位导师。".into()));
    }
    if m.research.is_none() && m.papers.is_empty() {
        return Err(CallError::invalid(
            "请先补充导师的研究方向或代表论文。".into(),
        ));
    }
    let count = mentor::matching::enqueue(&ctx.db, &[input.id], true, true, now()).await?;
    ctx.matcher.wake();
    Ok(count)
}
async fn storage_failure(ctx: &Context, _: Nothing) -> Result<Option<String>, CallError> {
    Ok(ctx.writes.failure().map(str::to_owned))
}
async fn matching_progress(
    ctx: &Context,
    _: Nothing,
) -> Result<mentor::matching::MatchProgress, CallError> {
    Ok(mentor::matching::progress(&ctx.db).await?)
}
async fn pause_matching(ctx: &Context, _: Nothing) -> Result<(), CallError> {
    Ok(mentor::matching::pause(&ctx.db, now(), None).await?)
}
async fn resume_matching(ctx: &Context, _: Nothing) -> Result<(), CallError> {
    matching::ready(&ctx.db).await?;
    mentor::matching::resume(&ctx.db).await?;
    ctx.matcher.wake();
    Ok(())
}
async fn cancel_matching(ctx: &Context, _: Nothing) -> Result<(), CallError> {
    mentor::matching::cancel(&ctx.db).await?;
    ctx.matcher.wake();
    Ok(())
}
async fn retry_matching(ctx: &Context, _: Nothing) -> Result<u64, CallError> {
    matching::ready(&ctx.db).await?;
    let count = mentor::matching::retry(&ctx.db, now()).await?;
    ctx.matcher.wake();
    Ok(count)
}
impl From<matching::Error> for CallError {
    fn from(e: matching::Error) -> Self {
        match e {
            matching::Error::NoModel => Self::invalid(matching::NO_MODEL.into()),
            matching::Error::NoBasis => Self::invalid(matching::NO_BASIS.into()),
            matching::Error::NotAgreed => Self {
                code: ErrorCode::NeedsAgreement,
                message: matching::NOT_AGREED.into(),
            },
            matching::Error::Db(e) => e.into(),
        }
    }
}

/// The input of operations that need none.
#[derive(Debug, Deserialize, TS)]
pub struct Nothing {}

const NO_MENTOR: &str = "找不到这位导师，可能已被永久删除。";

#[derive(Debug, Deserialize, Serialize, TS)]
pub struct MentorId {
    pub id: i64,
}

#[derive(Debug, Deserialize, TS)]
pub struct MentorIds {
    pub ids: Vec<i64>,
}

#[derive(Debug, Deserialize, TS)]
pub struct MentorEdit {
    pub id: i64,
    #[serde(flatten)]
    pub form: mentor::MentorForm,
}

#[derive(Debug, Deserialize, TS)]
pub struct MentorAddress {
    pub id: i64,
    pub address: String,
}

#[derive(Debug, Deserialize, TS)]
#[serde(rename_all = "camelCase")]
pub struct MentorMailbox {
    pub id: i64,
    pub mailbox_id: Option<i64>,
}

/// Two mentors listed twice: `other` goes into `keep`.
#[derive(Debug, Deserialize, TS)]
pub struct MentorMerge {
    pub keep: i64,
    pub other: i64,
}

#[derive(Debug, Deserialize, TS)]
pub struct MentorNote {
    pub id: i64,
    /// Blank clears the note.
    pub note: String,
}

async fn list_mentors(
    ctx: &Context,
    mut query: mentor::MentorQuery,
) -> Result<mentor::MentorPage, CallError> {
    query.filter.check().map_err(CallError::invalid)?;
    Ok(mentor::list(&ctx.db, &query, now()).await?)
}

async fn mentor_filter_options(
    ctx: &Context,
    _: Nothing,
) -> Result<mentor::filters::MentorFilterOptions, CallError> {
    Ok(mentor::filters::options(&ctx.db).await?)
}

async fn get_mentor(ctx: &Context, input: MentorId) -> Result<mentor::MentorDetail, CallError> {
    mentor::get(&ctx.db, input.id)
        .await?
        .ok_or_else(|| CallError::not_found(NO_MENTOR))
}

async fn set_mentor_note(ctx: &Context, input: MentorNote) -> Result<(), CallError> {
    let note = Some(input.note.as_str()).filter(|n| !n.trim().is_empty());
    require_applied(mentor::set_note(&ctx.db, input.id, note).await?, NO_MENTOR)
}

/// Adds the declarations of `T` and every named type it refers to, keyed by name.
fn collect<T: TS + 'static + ?Sized>(cfg: &Config, decls: &mut BTreeMap<String, String>) {
    struct Visitor<'a> {
        cfg: &'a Config,
        decls: &'a mut BTreeMap<String, String>,
    }
    impl TypeVisitor for Visitor<'_> {
        fn visit<T: TS + 'static + ?Sized>(&mut self) {
            // Only named types have a path to export to; primitives and containers are inlined.
            if T::output_path().is_none() {
                return;
            }
            let (name, decl) = (T::name(self.cfg), T::decl(self.cfg));
            match self.decls.get(&name) {
                Some(seen) => assert_eq!(seen, &decl, "two types are named {name}"),
                None => {
                    self.decls.insert(name, decl);
                    T::visit_dependencies(self);
                }
            }
        }
    }
    let mut visitor = Visitor { cfg, decls };
    visitor.visit::<T>();
    // What a container such as `Vec<T>` holds.
    T::visit_generics(&mut visitor);
}

async fn add_mentor(ctx: &Context, form: mentor::MentorForm) -> Result<MentorId, CallError> {
    let fields = form.check().map_err(CallError::invalid)?;
    let id = mentor::create(&ctx.db, &fields, now()).await?;
    look_up(ctx, id).await?;
    Ok(MentorId { id })
}

/// Returns the mentor as saved, so the page shows it at once.
async fn update_mentor(
    ctx: &Context,
    input: MentorEdit,
) -> Result<mentor::MentorDetail, CallError> {
    let fields = input.form.check().map_err(CallError::invalid)?;
    let applied = mentor::update(&ctx.db, input.id, &fields, now()).await?;
    require_applied(applied, NO_MENTOR)?;
    look_up(ctx, input.id).await?;
    get_mentor(ctx, MentorId { id: input.id }).await
}

async fn set_mentor_mailbox(
    ctx: &Context,
    input: MentorMailbox,
) -> Result<mentor::MentorDetail, CallError> {
    let applied = mentor::set_mailbox(&ctx.db, input.id, input.mailbox_id)
        .await
        .map_err(|e| {
            if e.as_database_error()
                .is_some_and(|e| e.is_foreign_key_violation())
            {
                CallError::invalid("这个邮箱已经删除，请刷新后重新选择。".into())
            } else {
                e.into()
            }
        })?;
    require_applied(applied, NO_MENTOR)?;
    ctx.sender.wake();
    get_mentor(ctx, MentorId { id: input.id }).await
}

async fn add_mentor_email(
    ctx: &Context,
    input: MentorAddress,
) -> Result<mentor::MentorDetail, CallError> {
    let address = input.address.trim();
    if !mentor::is_email(address) {
        return Err(CallError::invalid(format!("邮箱地址“{address}”格式不正确")));
    }
    let applied = mentor::add_email(&ctx.db, input.id, address, now()).await?;
    require_applied(applied, NO_MENTOR)?;
    look_up(ctx, input.id).await?;
    get_mentor(ctx, MentorId { id: input.id }).await
}

async fn preview_merge(
    ctx: &Context,
    input: MentorMerge,
) -> Result<mentor::merge::Preview, CallError> {
    Ok(mentor::merge::preview(&ctx.db, input.keep, input.other).await?)
}

async fn merge_mentors(ctx: &Context, input: MentorMerge) -> Result<(), CallError> {
    mentor::merge::merge(&ctx.db, input.keep, input.other, now()).await?;
    ctx.reply_processor.wake();
    ctx.todo_reminders.wake();
    Ok(())
}

/// Looks up the earlier mail at a mentor's new addresses without waiting for the next round.
async fn look_up(ctx: &Context, mentor_id: i64) -> Result<(), CallError> {
    if receive::queued(&ctx.db, mentor_id).await? {
        ctx.receiver.wake();
    }
    Ok(())
}

async fn list_trash(
    ctx: &Context,
    query: mentor::trash::TrashQuery,
) -> Result<mentor::MentorPage, CallError> {
    Ok(mentor::trash::list(&ctx.db, &query).await?)
}

async fn trash_mentors(ctx: &Context, input: MentorIds) -> Result<(), CallError> {
    mentor::trash::trash(&ctx.db, &input.ids, now()).await?;
    ctx.todo_reminders.wake();
    Ok(())
}

async fn restore_mentors(ctx: &Context, input: MentorIds) -> Result<(), CallError> {
    mentor::trash::restore(&ctx.db, &input.ids, now()).await?;
    ctx.reply_processor.wake();
    ctx.todo_reminders.wake();
    Ok(())
}

async fn delete_mentors(ctx: &Context, input: MentorIds) -> Result<(), CallError> {
    mentor::trash::delete(&ctx.db, &input.ids).await?;
    ctx.todo_reminders.wake();
    Ok(())
}

#[derive(Debug, Deserialize, TS)]
#[serde(rename_all = "camelCase")]
pub struct TagMentors {
    pub tag_id: i64,
    pub mentor_ids: Vec<i64>,
    /// Adds the tag when true, removes it when false.
    pub on: bool,
}

#[derive(Debug, Deserialize, TS)]
pub struct TagId {
    pub id: i64,
}

/// A new tag, put straight on these mentors.
#[derive(Debug, Deserialize, TS)]
#[serde(rename_all = "camelCase")]
pub struct NewTag {
    #[serde(flatten)]
    pub form: mentor::tags::TagForm,
    pub mentor_ids: Vec<i64>,
}

#[derive(Debug, Deserialize, TS)]
pub struct TagName {
    pub id: i64,
    pub name: String,
}

#[derive(Debug, Deserialize, TS)]
pub struct TagColor {
    pub id: i64,
    pub color: String,
}

#[derive(Debug, Deserialize, TS)]
pub struct TagMove {
    pub id: i64,
    pub direction: mentor::tags::Direction,
}

const NO_TAG: &str = "找不到这个标签，可能已被删除。";

async fn tag_mentors(ctx: &Context, input: TagMentors) -> Result<(), CallError> {
    Ok(mentor::tags::tag_mentors(&ctx.db, input.tag_id, &input.mentor_ids, input.on).await?)
}

async fn list_tags(ctx: &Context, _: Nothing) -> Result<Vec<mentor::tags::Tag>, CallError> {
    Ok(mentor::tags::list(&ctx.db).await?)
}

async fn create_tag(ctx: &Context, input: NewTag) -> Result<mentor::tags::Tag, CallError> {
    let fields = input.form.check().map_err(CallError::invalid)?;
    mentor::tags::create(&ctx.db, &fields, &input.mentor_ids)
        .await
        .map_err(|e| tag_error(e, &fields.name))
}

async fn rename_tag(ctx: &Context, input: TagName) -> Result<(), CallError> {
    let name = mentor::tags::checked_name(&input.name).map_err(CallError::invalid)?;
    let applied = mentor::tags::rename(&ctx.db, input.id, &name)
        .await
        .map_err(|e| tag_error(e, &name))?;
    require_applied(applied, NO_TAG)
}

async fn recolor_tag(ctx: &Context, input: TagColor) -> Result<(), CallError> {
    let color = mentor::tags::checked_color(&input.color).map_err(CallError::invalid)?;
    let applied = mentor::tags::recolor(&ctx.db, input.id, &color).await?;
    require_applied(applied, NO_TAG)
}

async fn move_tag(ctx: &Context, input: TagMove) -> Result<(), CallError> {
    Ok(mentor::tags::move_tag(&ctx.db, input.id, input.direction).await?)
}

async fn delete_tag(ctx: &Context, input: TagId) -> Result<(), CallError> {
    Ok(mentor::tags::delete(&ctx.db, input.id).await?)
}

fn tag_error(e: mentor::tags::TagError, name: &str) -> CallError {
    match e {
        mentor::tags::TagError::NameTaken => CallError::invalid(format!("已有名为“{name}”的标签")),
        mentor::tags::TagError::Db(e) => e.into(),
    }
}

impl From<mentor::SaveError> for CallError {
    fn from(e: mentor::SaveError) -> Self {
        match e {
            mentor::SaveError::EmailTaken {
                address,
                name,
                trashed,
            } => CallError::invalid(if trashed {
                format!("邮箱“{address}”已用于回收站里的导师{name}")
            } else {
                format!("邮箱“{address}”已用于导师{name}")
            }),
            mentor::SaveError::Db(e) => e.into(),
        }
    }
}

impl From<mentor::merge::MergeError> for CallError {
    fn from(e: mentor::merge::MergeError) -> Self {
        match e {
            mentor::merge::MergeError::Gone => {
                CallError::not_found("有一位导师已被永久删除或移入回收站。")
            }
            mentor::merge::MergeError::Busy { name, count } => CallError::invalid(format!(
                "{name}老师还有 {count} 封邮件会发出（待发送、发送中或发送失败）。等它们发出，\
                或改回草稿后再合并。"
            )),
            mentor::merge::MergeError::Db(e) => e.into(),
        }
    }
}

#[derive(Debug, Deserialize, Serialize, TS)]
pub struct MailboxId {
    pub id: i64,
}

#[derive(Debug, Deserialize, TS)]
pub struct NewMailbox {
    #[serde(flatten)]
    pub form: mailbox::MailboxForm,
    pub secret: String,
}

#[derive(Debug, Deserialize, TS)]
pub struct MailboxEdit {
    pub id: i64,
    #[serde(flatten)]
    pub form: mailbox::MailboxForm,
    /// Blank keeps the saved one, unless the servers changed.
    pub secret: String,
}

/// A mailbox as filled in, perhaps not saved yet.
#[derive(Debug, Deserialize, TS)]
pub struct MailboxTest {
    /// The saved mailbox being edited, whose secret a blank one stands for while the servers stay
    /// the same.
    #[ts(optional)]
    pub id: Option<i64>,
    #[serde(flatten)]
    pub form: mailbox::MailboxForm,
    pub secret: String,
}

const NO_MAILBOX: &str = "找不到这个邮箱，可能已被删除。";
const NO_SECRET: &str = "请填写授权码或密码";

async fn list_providers(
    _: &Context,
    _: Nothing,
) -> Result<&'static [mailbox::providers::Provider], CallError> {
    Ok(mailbox::providers::PROVIDERS)
}

async fn list_mailboxes(ctx: &Context, _: Nothing) -> Result<Vec<mailbox::Mailbox>, CallError> {
    Ok(mailbox::list(&ctx.db, now()).await?)
}

async fn get_rotation(ctx: &Context, _: Nothing) -> Result<profile::Rotation, CallError> {
    Ok(profile::rotation(&ctx.db).await?)
}

async fn set_rotation(ctx: &Context, rotation: profile::Rotation) -> Result<(), CallError> {
    profile::set_rotation(&ctx.db, rotation).await?;
    ctx.sender.wake();
    Ok(())
}

async fn add_mailbox(ctx: &Context, input: NewMailbox) -> Result<MailboxId, CallError> {
    let fields = input.form.check().map_err(CallError::invalid)?;
    let secret = mailbox::checked_secret(&input.secret)
        .ok_or_else(|| CallError::invalid(NO_SECRET.into()))?;
    let id = mailbox::create(&ctx.db, &fields, &secret, now()).await?;
    // Emails may be waiting for a mailbox.
    ctx.sender.wake();
    ctx.receiver.wake();
    Ok(MailboxId { id })
}

async fn update_mailbox(ctx: &Context, input: MailboxEdit) -> Result<(), CallError> {
    let fields = input.form.check().map_err(CallError::invalid)?;
    let secret = entered_or_saved_secret(ctx, &input.secret, Some(input.id), &fields).await?;
    let held = ctx.receiver.hold().await;
    let updated = mailbox::update(&ctx.db, input.id, &fields, &secret).await?;
    drop(held);
    require_applied(updated, NO_MAILBOX)?;
    // A higher limit or a shorter interval may let a waiting email go, and receiving that
    // failed may work now.
    ctx.sender.wake();
    ctx.receiver.wake();
    Ok(())
}

async fn resume_mailbox(ctx: &Context, input: MailboxId) -> Result<(), CallError> {
    require_applied(mailbox::resume(&ctx.db, input.id).await?, NO_MAILBOX)?;
    ctx.sender.wake();
    Ok(())
}

async fn delete_mailbox(ctx: &Context, input: MailboxId) -> Result<(), CallError> {
    let _held = ctx.receiver.hold().await;
    Ok(mailbox::delete(&ctx.db, input.id).await?)
}

async fn test_sending(ctx: &Context, input: MailboxTest) -> Result<(), CallError> {
    let account = test_account(ctx, input).await?;
    Ok(mailbox::check_sending(&ctx.net, &account).await?)
}

async fn test_receiving(ctx: &Context, input: MailboxTest) -> Result<(), CallError> {
    let account = test_account(ctx, input).await?;
    Ok(mailbox::check_receiving(&ctx.net, &account).await?)
}

async fn test_account(ctx: &Context, input: MailboxTest) -> Result<mailbox::Account, CallError> {
    let fields = input.form.check().map_err(CallError::invalid)?;
    let secret = entered_or_saved_secret(ctx, &input.secret, input.id, &fields).await?;
    Ok(fields.account(secret))
}

/// The secret typed in, else the saved one of the mailbox being edited if its servers stay the
/// same.
async fn entered_or_saved_secret(
    ctx: &Context,
    secret: &str,
    id: Option<i64>,
    fields: &mailbox::MailboxFields,
) -> Result<String, CallError> {
    if let Some(secret) = mailbox::checked_secret(secret) {
        return Ok(secret);
    }
    let saved = match id {
        Some(id) => mailbox::secret(&ctx.db, id, fields).await?,
        None => None,
    };
    saved.ok_or_else(|| CallError::invalid(NO_SECRET.into()))
}

async fn send_test_mail(ctx: &Context, input: MailboxId) -> Result<(), CallError> {
    let account = mailbox::account(&ctx.db, input.id).await?.ok_or_else(|| {
        CallError::invalid("请先在“设置 › 邮件 › 邮箱”填写授权码或密码，再发测试信。".into())
    })?;
    Ok(mailbox::send_test(&ctx.net, &account).await?)
}

#[derive(Debug, Deserialize, TS)]
#[serde(rename_all = "camelCase")]
pub struct SendTestLetter {
    pub mailbox_id: i64,
    pub subject: String,
    pub html: String,
    pub text: String,
    pub attachments: Vec<i64>,
}

async fn send_test_letter(ctx: &Context, input: SendTestLetter) -> Result<(), CallError> {
    let account = mailbox::account(&ctx.db, input.mailbox_id)
        .await?
        .ok_or_else(|| CallError::not_found(NO_MAILBOX))?;
    let ids = sqlx::types::Json(&input.attachments);
    let rows = sqlx::query!(
        r#"SELECT m.id AS "id?", m.file_name FROM json_each(?) j
           LEFT JOIN material m ON m.id = j.value ORDER BY j.key"#,
        ids,
    )
    .fetch_all(&ctx.db)
    .await?;
    let mut attached: Vec<email::Attached> = Vec::new();
    for row in rows {
        let (Some(material_id), Some(file_name)) = (row.id, row.file_name) else {
            return Err(CallError::invalid(
                "附件材料已被删除。请重新选择附件后再发送。".into(),
            ));
        };
        // As with an approved letter, the first selected file with each name is kept.
        if !attached.iter().any(|a| a.file_name == file_name) {
            attached.push(email::Attached {
                material_id,
                file_name,
            });
        }
    }
    let files = send::files(&ctx.materials, &attached)
        .await
        .map_err(CallError::invalid)?;
    let message_id = mailbox::message_id(&account);
    let outgoing = mailbox::compose(
        &account,
        &mailbox::Letter {
            message_id: &message_id,
            in_reply_to: None,
            references: None,
            to_name: &account.sender_name,
            to: account.address.as_ref(),
            subject: &input.subject,
            html: &input.html,
            text: &input.text,
            attachments: &files,
        },
    )?;
    mailbox::login(&ctx.net, &account)
        .await?
        .send(&outgoing, || false)
        .await
        .map_err(|e| {
            tracing::warn!("test letter from mailbox {}: {e}", input.mailbox_id);
            CallError {
                code: ErrorCode::External,
                message: e.message(),
            }
        })?;
    tracing::info!("test letter sent from mailbox {}", input.mailbox_id);
    Ok(())
}

impl From<mailbox::SaveError> for CallError {
    fn from(e: mailbox::SaveError) -> Self {
        match e {
            mailbox::SaveError::AddressTaken => CallError::invalid("这个邮箱已经添加过了".into()),
            mailbox::SaveError::Db(e) => e.into(),
        }
    }
}

/// The details go to the log; the user reads what happened and what to do.
impl From<mailbox::MailError> for CallError {
    fn from(e: mailbox::MailError) -> Self {
        tracing::warn!("{e}");
        CallError {
            code: ErrorCode::External,
            message: e.message(),
        }
    }
}

#[derive(Debug, Deserialize, Serialize, TS)]
pub struct ModelConfigId {
    pub id: i64,
}

#[derive(Debug, Deserialize, TS)]
#[serde(rename_all = "camelCase")]
pub struct NewModelConfig {
    #[serde(flatten)]
    pub form: model::ModelForm,
    pub api_key: String,
}

#[derive(Debug, Deserialize, TS)]
#[serde(rename_all = "camelCase")]
pub struct ModelConfigEdit {
    pub id: i64,
    #[serde(flatten)]
    pub form: model::ModelForm,
    /// Blank keeps the saved one, unless the provider or address changed.
    pub api_key: String,
}

/// A provider as filled in, perhaps not saved yet, to list its models.
#[derive(Debug, Deserialize, TS)]
#[serde(rename_all = "camelCase")]
pub struct ModelSource {
    /// The saved configuration being edited, whose API key a blank one stands for while the
    /// provider and address stay the same.
    #[ts(optional)]
    pub id: Option<i64>,
    pub provider: String,
    /// Only read for a custom provider.
    pub base_url: String,
    pub api_key: String,
}

/// A configuration as filled in, perhaps not saved yet.
#[derive(Debug, Deserialize, TS)]
#[serde(rename_all = "camelCase")]
pub struct ModelTest {
    /// The saved configuration being edited, whose API key a blank one stands for while the
    /// provider and address stay the same.
    #[ts(optional)]
    pub id: Option<i64>,
    #[serde(flatten)]
    pub form: model::ModelForm,
    pub api_key: String,
}

const NO_MODEL_CONFIG: &str = "找不到这个模型配置，可能已被删除。";
const NO_API_KEY: &str = "请填写 API Key";

async fn list_model_providers(
    _: &Context,
    _: Nothing,
) -> Result<&'static [model::providers::Provider], CallError> {
    Ok(model::providers::PROVIDERS)
}

async fn list_model_configs(
    ctx: &Context,
    _: Nothing,
) -> Result<Vec<model::ModelConfig>, CallError> {
    Ok(model::list(&ctx.db).await?)
}

async fn add_model_config(
    ctx: &Context,
    input: NewModelConfig,
) -> Result<ModelConfigId, CallError> {
    let fields = input.form.check().map_err(CallError::invalid)?;
    let api_key = entered_or_saved_key(ctx, &input.api_key, None, &fields.source).await?;
    let id = model::create(&ctx.db, &fields, &api_key, now()).await?;
    Ok(ModelConfigId { id })
}

async fn update_model_config(ctx: &Context, input: ModelConfigEdit) -> Result<(), CallError> {
    let fields = input.form.check().map_err(CallError::invalid)?;
    let api_key = entered_or_saved_key(ctx, &input.api_key, Some(input.id), &fields.source).await?;
    let applied = model::update(&ctx.db, input.id, &fields, &api_key).await?;
    require_applied(applied, NO_MODEL_CONFIG)
}

async fn delete_model_config(ctx: &Context, input: ModelConfigId) -> Result<(), CallError> {
    Ok(model::delete(&ctx.db, input.id).await?)
}

async fn set_default_model(ctx: &Context, input: ModelConfigId) -> Result<(), CallError> {
    let applied = profile::set_default_model(&ctx.db, input.id).await?;
    require_applied(applied, "找不到这个模型，可能已被删除。")
}

/// What the user was told AI features send to, and agreed to.
#[derive(Debug, Deserialize, TS)]
#[serde(rename_all = "camelCase")]
pub struct ModelAgreement {
    pub id: i64,
    pub provider: String,
    pub base_url: Option<String>,
}

async fn agree_model(ctx: &Context, input: ModelAgreement) -> Result<(), CallError> {
    let (provider, base_url) = (&input.provider, input.base_url.as_deref());
    let applied = model::agree(&ctx.db, input.id, provider, base_url).await?;
    require_applied(
        applied,
        "这个模型配置刚被修改或删除了。请看过新的说明后再确认。",
    )
}

async fn list_provider_models(ctx: &Context, input: ModelSource) -> Result<Vec<String>, CallError> {
    let source =
        model::Source::check(&input.provider, &input.base_url).map_err(CallError::invalid)?;
    let api_key = entered_or_saved_key(ctx, &input.api_key, input.id, &source).await?;
    Ok(model::list_models(&ctx.net, &source.access(Some(api_key))).await?)
}

async fn test_model(ctx: &Context, input: ModelTest) -> Result<(), CallError> {
    let fields = input.form.check().map_err(CallError::invalid)?;
    let api_key = entered_or_saved_key(ctx, &input.api_key, input.id, &fields.source).await?;
    Ok(model::test(
        &ctx.net,
        &fields.source.access(Some(api_key)),
        &fields.model,
    )
    .await?)
}

/// The API key typed in, else the saved one of the configuration being edited if it is still for
/// the same place. Models on this computer, such as Ollama's, need none.
async fn entered_or_saved_key(
    ctx: &Context,
    api_key: &str,
    id: Option<i64>,
    source: &model::Source,
) -> Result<String, CallError> {
    if let Some(api_key) = model::checked_key(api_key) {
        return Ok(api_key);
    }
    let saved = match id {
        Some(id) => model::api_key(&ctx.db, id, source).await?,
        None => None,
    };
    match (saved, source) {
        (Some(Some(api_key)), _) => Ok(api_key),
        (Some(None), _) => Err(CallError::invalid(model::ModelError::MissingKey.message())),
        (None, model::Source::Custom(_)) => Ok(String::new()),
        (None, model::Source::Known(_)) => Err(CallError::invalid(NO_API_KEY.into())),
    }
}

/// The details go to the log; the user reads what happened and what to do.
impl From<model::ModelError> for CallError {
    fn from(e: model::ModelError) -> Self {
        tracing::warn!("{e}");
        CallError {
            code: ErrorCode::External,
            message: e.message(),
        }
    }
}

async fn get_profile(ctx: &Context, _: Nothing) -> Result<profile::Profile, CallError> {
    Ok(profile::get(&ctx.db).await?)
}

async fn update_profile(
    ctx: &Context,
    form: profile::ProfileForm,
) -> Result<profile::Profile, CallError> {
    Ok(profile::update(&ctx.db, &form, now()).await?)
}

async fn finish_onboarding(ctx: &Context, _: Nothing) -> Result<(), CallError> {
    Ok(profile::finish_onboarding(&ctx.db).await?)
}

#[derive(Debug, Serialize, TS)]
#[serde(rename_all = "camelCase")]
pub struct TestLetter {
    pub template_name: String,
    pub subject: String,
    pub body_md: String,
    pub attachments: Vec<i64>,
}

async fn test_letter(ctx: &Context, _: Nothing) -> Result<Option<TestLetter>, CallError> {
    let mut tx = ctx.db.begin().await?;
    let Some(t) = template::default_or_first(&mut tx).await? else {
        return Ok(None);
    };
    let name = sqlx::query_scalar!("SELECT name FROM profile")
        .fetch_one(&mut *tx)
        .await?;
    let attachments =
        sqlx::query_scalar!("SELECT id FROM material WHERE attach_by_default = 1 ORDER BY id")
            .fetch_all(&mut *tx)
            .await?;
    tx.commit().await?;
    let fill = template::Fill {
        my_name: name.as_deref(),
        ..template::EXAMPLE_MENTOR
    };
    Ok(Some(TestLetter {
        template_name: t.name,
        subject: fill.subject(&t.subject),
        body_md: fill.body(&t.body_md),
        attachments,
    }))
}

async fn get_letter_style(ctx: &Context, _: Nothing) -> Result<profile::LetterStyle, CallError> {
    Ok(profile::letter_style(&ctx.db).await?)
}

async fn update_letter_style(ctx: &Context, style: profile::LetterStyle) -> Result<(), CallError> {
    style.check().map_err(CallError::invalid)?;
    Ok(profile::update_letter_style(&ctx.db, &style).await?)
}

#[derive(Debug, Deserialize, Serialize, TS)]
pub struct MaterialId {
    pub id: i64,
}

/// A file the user picked, as base64: JSON carries no bytes.
#[derive(Debug, Deserialize, TS)]
#[serde(rename_all = "camelCase")]
pub struct UploadedFile {
    pub file_name: String,
    pub data: String,
}

#[derive(Debug, Deserialize, TS)]
pub struct MaterialPurpose {
    pub id: i64,
    pub purpose: material::Purpose,
    pub on: bool,
}

const NO_MATERIAL: &str = "找不到这份材料，可能已被删除。";

impl From<material::Error> for CallError {
    fn from(e: material::Error) -> Self {
        match e {
            material::Error::Type => CallError::invalid("只能添加 PDF、DOCX 或 DOC 文件。".into()),
            material::Error::TooLarge => CallError::invalid("文件超过 20 MB，无法添加。".into()),
            material::Error::Unreadable(detail) => {
                tracing::warn!("reading a material: {detail}");
                CallError::invalid(
                    "未能读取文件：文件可能已损坏、设有密码，或格式与扩展名不符。".into(),
                )
            }
            material::Error::InUse => CallError::invalid(
                "这份材料是待发邮件的附件，邮件发出或改回草稿去掉附件后才能删除。".into(),
            ),
            material::Error::TaskInUse { name } => CallError::invalid(format!(
                "这份材料正用于任务“{name}”的草稿生成，请等待生成完成，或停止该任务后再删除。"
            )),
            material::Error::Db(e) => e.into(),
            material::Error::File(e) => CallError::internal(format!("material file: {e}")),
        }
    }
}

async fn list_materials(ctx: &Context, _: Nothing) -> Result<Vec<material::Material>, CallError> {
    Ok(material::list(&ctx.db).await?)
}

async fn add_material(ctx: &Context, input: UploadedFile) -> Result<MaterialId, CallError> {
    let data = uploaded_bytes(&input.data)?;
    let extracted = material::extract(input.file_name.trim(), data).await?;
    let id = material::add(&ctx.db, &ctx.materials, extracted, now()).await?;
    Ok(MaterialId { id })
}

async fn set_material_purpose(ctx: &Context, input: MaterialPurpose) -> Result<(), CallError> {
    let applied = material::set_purpose(&ctx.db, input.id, input.purpose, input.on, now()).await?;
    require_applied(applied, NO_MATERIAL)
}

async fn get_material_text(ctx: &Context, input: MaterialId) -> Result<Option<String>, CallError> {
    material::text(&ctx.db, input.id)
        .await?
        .ok_or_else(|| CallError::not_found(NO_MATERIAL))
}

async fn delete_material(ctx: &Context, input: MaterialId) -> Result<(), CallError> {
    let applied = material::delete(&ctx.db, &ctx.materials, input.id, now()).await?;
    require_applied(applied, NO_MATERIAL)
}

#[derive(Debug, Deserialize, Serialize, TS)]
pub struct TemplateId {
    pub id: i64,
}

#[derive(Debug, Deserialize, TS)]
pub struct TemplateEdit {
    pub id: i64,
    #[serde(flatten)]
    pub form: template::TemplateForm,
}

const NO_TEMPLATE: &str = "找不到这个模板，可能已被删除。";

async fn list_placeholders(_: &Context, _: Nothing) -> Result<&'static [&'static str], CallError> {
    Ok(&template::PLACEHOLDERS)
}

async fn list_templates(
    ctx: &Context,
    _: Nothing,
) -> Result<Vec<template::TemplateSummary>, CallError> {
    Ok(template::list(&ctx.db).await?)
}

async fn get_template(ctx: &Context, input: TemplateId) -> Result<template::Template, CallError> {
    template::get(&ctx.db, input.id)
        .await?
        .ok_or_else(|| CallError::not_found(NO_TEMPLATE))
}

async fn add_template(
    ctx: &Context,
    form: template::TemplateForm,
) -> Result<TemplateId, CallError> {
    let fields = form.check().map_err(CallError::invalid)?;
    let id = template::create(&ctx.db, &fields, now()).await?;
    Ok(TemplateId { id })
}

#[derive(Debug, Serialize, TS)]
#[serde(rename_all = "camelCase")]
pub struct WordFile {
    pub name: String,
    pub html: String,
}

async fn import_word_template(_: &Context, input: UploadedFile) -> Result<WordFile, CallError> {
    let data = uploaded_bytes(&input.data)?;
    let Some((name, extension)) = input.file_name.trim().rsplit_once('.') else {
        return Err(CallError::invalid("请选择 DOCX 或 DOC 文件。".into()));
    };
    if !extension.eq_ignore_ascii_case("docx") && !extension.eq_ignore_ascii_case("doc") {
        return Err(CallError::invalid("请选择 DOCX 或 DOC 文件。".into()));
    }
    let unreadable = |detail: String| {
        tracing::warn!("reading a Word template: {detail}");
        CallError::invalid(
            "未能读取 Word 文件：文件可能已损坏、设有密码或不是 Word 格式，请换一个文件。".into(),
        )
    };
    // Parsing is slow work, and a library may panic on a damaged file.
    let html = tokio::task::spawn_blocking(move || {
        material::word_body(&data)
            .map(|document| document.map(|body| body.to_html()))
            .map_err(|error| error.to_string())
    })
    .await
    .map_err(|error| unreadable(error.to_string()))?
    .map_err(unreadable)?
    .ok_or_else(|| CallError::invalid("文件中没有可导入的文字，请换一个 Word 文件。".into()))?;
    Ok(WordFile {
        name: name.into(),
        html,
    })
}

async fn update_template(ctx: &Context, input: TemplateEdit) -> Result<(), CallError> {
    let fields = input.form.check().map_err(CallError::invalid)?;
    let applied = template::update(&ctx.db, input.id, &fields, now()).await?;
    require_applied(applied, NO_TEMPLATE)
}

async fn delete_template(ctx: &Context, input: TemplateId) -> Result<(), CallError> {
    Ok(template::delete(&ctx.db, input.id).await?)
}

#[derive(Debug, Deserialize, TS)]
#[serde(rename_all = "camelCase")]
pub struct TemplateForMentor {
    pub template_id: i64,
    pub mentor_id: i64,
}

#[derive(Debug, Serialize, TS)]
#[serde(rename_all = "camelCase")]
pub struct FilledTemplate {
    pub subject: String,
    pub body_md: String,
}

/// A template to make the default, or none.
#[derive(Debug, Deserialize, TS)]
pub struct DefaultTemplate {
    pub id: Option<i64>,
}

async fn set_default_template(ctx: &Context, input: DefaultTemplate) -> Result<(), CallError> {
    let applied = profile::set_default_template(&ctx.db, input.id).await?;
    require_applied(applied, NO_TEMPLATE)
}

async fn apply_template(
    ctx: &Context,
    input: TemplateForMentor,
) -> Result<FilledTemplate, CallError> {
    let id = input.template_id;
    let t = get_template(ctx, TemplateId { id }).await?;
    fill(ctx, &t, input.mentor_id).await
}

/// The default template filled in for the mentor, which a first email starts from; none when no
/// template is the default.
async fn apply_default_template(
    ctx: &Context,
    input: MentorId,
) -> Result<Option<FilledTemplate>, CallError> {
    match template::default(&ctx.db).await? {
        Some(t) => Ok(Some(fill(ctx, &t, input.id).await?)),
        None => Ok(None),
    }
}

/// The template with its placeholders filled in for the mentor.
async fn fill(
    ctx: &Context,
    t: &template::Template,
    mentor_id: i64,
) -> Result<FilledTemplate, CallError> {
    let m = get_mentor(ctx, MentorId { id: mentor_id }).await?;
    let me = profile::get(&ctx.db).await?;
    let filled = task::fill(&t.subject, &t.body_md, &m, me.name.as_deref());
    Ok(FilledTemplate {
        subject: filled.subject,
        body_md: filled.body_md,
    })
}

#[derive(Debug, Deserialize, TS)]
#[serde(rename_all = "camelCase")]
pub struct DraftMaterial {
    pub mentor_id: i64,
    pub kind: email::DraftKind,
    pub material_id: i64,
}

async fn attach_draft_material(ctx: &Context, input: DraftMaterial) -> Result<bool, CallError> {
    Ok(
        email::attach_draft_material(&ctx.db, input.mentor_id, input.kind, input.material_id)
            .await?,
    )
}

async fn get_first_draft(
    ctx: &Context,
    input: MentorId,
) -> Result<Option<email::FirstDraft>, CallError> {
    Ok(email::first_draft(&ctx.db, input.id).await?)
}

#[derive(Debug, Deserialize, TS)]
#[serde(rename_all = "camelCase")]
pub struct FirstDraftEdit {
    pub mentor_id: i64,
    pub subject: String,
    pub body_md: String,
    /// Materials, by id.
    pub attachments: Vec<i64>,
}

async fn save_first_draft(
    ctx: &Context,
    input: FirstDraftEdit,
) -> Result<Option<email::FirstDraft>, CallError> {
    let saved = email::save_first_draft(
        &ctx.db,
        input.mentor_id,
        &input.subject,
        &input.body_md,
        &input.attachments,
        now(),
    )
    .await?;
    if saved.is_none() && (!input.subject.trim().is_empty() || !input.body_md.trim().is_empty()) {
        return Err(CallError::invalid(
            "还有一封没发出的首封或追问，请先在任务或往来里处理。".into(),
        ));
    }
    Ok(saved)
}

/// The draft as it is in the composer, which may be ahead of the one saved.
async fn check_first_draft(
    ctx: &Context,
    input: FirstDraftEdit,
) -> Result<Vec<check::Problem>, CallError> {
    let id = input.mentor_id;
    let m = get_mentor(ctx, MentorId { id }).await?;
    let mut problems = check::letter(
        &m.name,
        &m.aliases,
        &input.subject,
        &input.body_md,
        !input.attachments.is_empty(),
    );
    let (count, last) = match m.contact {
        mentor::Contact::None => (0, None),
        mentor::Contact::Contacted { count, last_out_at }
        | mentor::Contact::Bounced { count, last_out_at } => (count, Some(last_out_at)),
        mentor::Contact::Replied {
            count, last_out_at, ..
        } => (count, last_out_at),
    };
    problems.extend(check::recent(count, last, now()));
    if let Some(draft) = email::first_draft(&ctx.db, id).await? {
        problems.extend(check::fact_check(
            draft.fact_check.as_ref(),
            &input.subject,
            &input.body_md,
        ));
    }
    Ok(problems)
}

/// Drafts the letter when it is blank and rewrites it otherwise.
async fn write_letter(ctx: &Context, input: FirstDraftEdit) -> Result<writing::Letter, CallError> {
    let _permit = ctx.model_limit.acquire().await;
    let current = writing::Letter {
        subject: input.subject,
        body_md: input.body_md,
    };
    let at = now();
    let chosen = model::chosen(&ctx.db)
        .await?
        .ok_or(writing::Error::NoModel)?;
    let references = material::for_writing(&ctx.db).await?;
    let prepared = writing::prepare(
        &ctx.db,
        input.mentor_id,
        &input.attachments,
        writing::Basis {
            chosen: &chosen,
            references: &references,
            kind: writing::Kind::First,
        },
    )
    .await?;
    let email_id = email::first_draft(&ctx.db, input.mentor_id)
        .await?
        .map(|draft| draft.id);
    Ok(
        writing::write(&ctx.net, &prepared, &current, |purpose, usage| {
            model::usage::record_for_email(
                &ctx.db,
                chosen.id,
                &chosen.model,
                purpose,
                email_id,
                usage,
                at,
            )
        })
        .await?,
    )
}

impl From<writing::Error> for CallError {
    fn from(e: writing::Error) -> Self {
        match e {
            writing::Error::NoModel => {
                CallError::invalid("尚未添加模型。在“设置 › AI › 模型”里添加后再试。".into())
            }
            writing::Error::NotAgreed => CallError {
                code: ErrorCode::NeedsAgreement,
                message: matching::NOT_AGREED.into(),
            },
            writing::Error::NoMentor => CallError::not_found(NO_MENTOR),
            writing::Error::Model(e) => e.into(),
            writing::Error::NotALetter { finish_reason } => {
                tracing::warn!(?finish_reason, "the model's answer is not a letter");
                let message = match finish_reason.as_deref() {
                    Some("length") => "模型的回答太长，被截断了。再试一次，或者换个模型。",
                    _ => "模型没有按要求的格式回答。再试一次，或者换个模型。",
                };
                CallError {
                    code: ErrorCode::External,
                    message: message.into(),
                }
            }
            writing::Error::Db(e) => e.into(),
        }
    }
}

#[derive(Debug, Deserialize, TS)]
pub struct MailAddress {
    pub address: String,
}

/// A lookup that fails, such as when offline, tells nothing: the interface shows no error and
/// asks again later (docs/design.md#联系).
async fn check_mail_domain(
    ctx: &Context,
    input: MailAddress,
) -> Result<Option<check::Problem>, CallError> {
    ctx.mail_domains.check(&input.address).await
}

/// The mentor's first draft as the user approved it, with the HTML and plain text the interface
/// rendered from it in the letter style.
#[derive(Debug, Deserialize, TS)]
#[serde(rename_all = "camelCase")]
pub struct FirstEmail {
    pub planned_at: Option<i64>,
    pub mentor_id: i64,
    pub subject: String,
    pub body_md: String,
    /// Agent approvals receive these from the confirmation dialog.
    #[ts(optional)]
    pub html: Option<String>,
    #[ts(optional)]
    pub text: Option<String>,
    /// Materials, by id.
    pub attachments: Vec<i64>,
}

async fn approve_first_email(ctx: &Context, input: FirstEmail) -> Result<(), CallError> {
    let subject = input.subject.trim();
    if subject.is_empty() || input.body_md.trim().is_empty() {
        return Err(CallError::invalid("主题和正文都要填写".into()));
    }
    let id = input.mentor_id;
    let m = get_mentor(ctx, MentorId { id }).await?;
    if m.emails.is_empty() {
        return Err(CallError::invalid(
            "导师尚未填写邮箱。在导师资料里填写后再发送。".into(),
        ));
    }
    if mailbox::list(&ctx.db, now()).await?.is_empty() {
        return Err(CallError::invalid(
            "尚未添加发信的邮箱。在“设置 › 邮件 › 邮箱”里添加后再发送。".into(),
        ));
    }
    let approval = email::Approval {
        subject,
        body_md: &input.body_md,
        html: input
            .html
            .as_deref()
            .ok_or_else(|| CallError::invalid("缺少邮件的 HTML 渲染结果。".into()))?,
        text: input
            .text
            .as_deref()
            .ok_or_else(|| CallError::invalid("缺少邮件的纯文本渲染结果。".into()))?,
        attachments: &input.attachments,
    };
    approved(
        ctx,
        email::approve_first(&ctx.db, input.mentor_id, &approval, input.planned_at, now()).await?,
        "这位导师已经有一封首封邮件，或已移入回收站。",
        "有附件的材料已被删除。重新选择附件后再发送。",
    )
}

fn approved(
    ctx: &Context,
    result: email::Approved,
    refused: &str,
    material_gone: &str,
) -> Result<(), CallError> {
    match result {
        email::Approved::Yes => {
            ctx.sender.wake();
            Ok(())
        }
        email::Approved::TimePassed => Err(CallError::invalid(
            "所选时间已过，请选择未来的日期和时间。".into(),
        )),
        email::Approved::Refused => Err(CallError::not_found(refused)),
        email::Approved::FollowUpReplied => {
            Err(CallError::invalid("导师已回信，请到往来里回复".into()))
        }
        email::Approved::FollowUpParentBounced => Err(CallError::invalid(
            "上一封被退回，请先核对导师的邮箱".into(),
        )),
        email::Approved::MaterialGone => Err(CallError::invalid(material_gone.into())),
        email::Approved::ReplyGone => Err(CallError::not_found(
            "回的那封信已不在这位导师的往来里。请选择另一封回信后再发送。",
        )),
        email::Approved::ReplyChanged => {
            Err(CallError::invalid("回复的信已更换，请再看一遍。".into()))
        }
        email::Approved::NoAddress => Err(CallError::invalid(
            "导师尚未填写邮箱。在导师资料里填写后再发送。".into(),
        )),
    }
}

#[derive(Debug, Deserialize, TS)]
#[serde(rename_all = "camelCase")]
pub struct ReplyTargetQuery {
    pub mentor_id: i64,
    pub id: Option<i64>,
}

async fn get_reply_target(
    ctx: &Context,
    input: ReplyTargetQuery,
) -> Result<Option<email::ReplyTarget>, CallError> {
    Ok(email::reply_target(&ctx.db, input.mentor_id, input.id).await?)
}

async fn get_reply_draft(
    ctx: &Context,
    input: MentorId,
) -> Result<Option<email::ReplyDraft>, CallError> {
    Ok(email::reply_draft(&ctx.db, input.id).await?)
}

#[derive(Debug, Deserialize, TS)]
#[serde(rename_all = "camelCase")]
pub struct ReplyDraftEdit {
    pub mentor_id: i64,
    pub reply_to: String,
    pub body_md: String,
    pub attachments: Vec<i64>,
}

async fn save_reply_draft(
    ctx: &Context,
    input: ReplyDraftEdit,
) -> Result<Option<email::ReplyDraft>, CallError> {
    let saved = email::save_reply_draft(
        &ctx.db,
        input.mentor_id,
        &input.reply_to,
        &input.body_md,
        &input.attachments,
        now(),
    )
    .await?;
    if saved.is_none() && (!input.body_md.trim().is_empty() || !input.attachments.is_empty()) {
        return Err(CallError::not_found(
            "回的那封信已不在这位导师的往来里，或导师已移入回收站。请选择另一封回信后再试。",
        ));
    }
    Ok(saved)
}

async fn check_reply_draft(
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
    // The subject belongs to the received message, not to the user's writing. Its placeholders
    // cannot be fixed here; checks apply only to the new body, never to the appended quote.
    let mut problems = check::letter(
        &m.name,
        &m.aliases,
        "",
        &input.body_md,
        !input.attachments.is_empty(),
    );
    if let Some(draft) = email::reply_draft(&ctx.db, input.mentor_id).await? {
        let subject = draft
            .target
            .as_ref()
            .and_then(|t| t.subject.as_deref())
            .map(email::reply_subject)
            .unwrap_or_default();
        problems.extend(check::fact_check(
            draft.fact_check.as_ref(),
            &subject,
            &input.body_md,
        ));
    }
    Ok(problems)
}

#[derive(Debug, Deserialize, TS)]
#[serde(rename_all = "camelCase")]
pub struct ReplyEmail {
    pub mentor_id: i64,
    pub draft_id: i64,
    pub reply_to: String,
    pub body_md: String,
    /// Agent approvals receive these from the confirmation dialog.
    #[ts(optional)]
    pub html: Option<String>,
    #[ts(optional)]
    pub text: Option<String>,
    pub attachments: Vec<i64>,
}

async fn approve_reply(ctx: &Context, input: ReplyEmail) -> Result<(), CallError> {
    if input.body_md.trim().is_empty() {
        return Err(CallError::invalid("请先填写回复正文。".into()));
    }
    let approval = email::Approval {
        subject: "",
        body_md: &input.body_md,
        html: input
            .html
            .as_deref()
            .ok_or_else(|| CallError::invalid("缺少邮件的 HTML 渲染结果。".into()))?,
        text: input
            .text
            .as_deref()
            .ok_or_else(|| CallError::invalid("缺少邮件的纯文本渲染结果。".into()))?,
        attachments: &input.attachments,
    };
    approved(
        ctx,
        email::approve_reply(
            &ctx.db,
            input.mentor_id,
            input.draft_id,
            &input.reply_to,
            &approval,
            now(),
        )
        .await?,
        "这封回复已被批准、已被清空，或导师已移入回收站。请刷新往来后查看。",
        "有附件的材料已被删除。重新选择附件后再发送。",
    )
}

impl From<email::follow_up::Error> for CallError {
    fn from(e: email::follow_up::Error) -> Self {
        match e {
            email::follow_up::Error::Invalid(s) => Self::invalid(s),
            email::follow_up::Error::Db(e) => e.into(),
        }
    }
}
async fn get_follow_up_entry(
    ctx: &Context,
    key: MentorId,
) -> Result<email::follow_up::FollowUpEntry, CallError> {
    Ok(email::follow_up::entry(&ctx.db, key.id).await?)
}
async fn get_follow_up_draft(
    ctx: &Context,
    key: MentorId,
) -> Result<Option<email::follow_up::FollowUpDraft>, CallError> {
    Ok(email::follow_up::draft(&ctx.db, key.id).await?)
}
async fn create_follow_up_draft(
    ctx: &Context,
    key: MentorId,
) -> Result<email::follow_up::FollowUpDraft, CallError> {
    Ok(email::follow_up::create(&ctx.db, key.id, now()).await?)
}
#[derive(Debug, Deserialize, TS)]
#[serde(rename_all = "camelCase")]
pub struct FollowUpEdit {
    pub draft_id: i64,
    #[serde(flatten)]
    pub letter: ReplyDraftEdit,
}
async fn save_follow_up_draft(
    ctx: &Context,
    input: FollowUpEdit,
) -> Result<Option<email::follow_up::FollowUpDraft>, CallError> {
    let d = input.letter;
    let saved = email::follow_up::save(
        &ctx.db,
        d.mentor_id,
        input.draft_id,
        &d.reply_to,
        &d.body_md,
        &d.attachments,
        now(),
    )
    .await?;
    if saved.is_none() {
        return Err(CallError::invalid(
            "追问已取消或已发送，请刷新往来后查看。".into(),
        ));
    }
    Ok(saved)
}
async fn approve_follow_up(ctx: &Context, input: ReplyEmail) -> Result<(), CallError> {
    if input.body_md.trim().is_empty() {
        return Err(CallError::invalid("请先填写追问正文。".into()));
    }
    let approval = email::Approval {
        subject: "",
        body_md: &input.body_md,
        html: input
            .html
            .as_deref()
            .ok_or_else(|| CallError::invalid("缺少邮件的 HTML 渲染结果。".into()))?,
        text: input
            .text
            .as_deref()
            .ok_or_else(|| CallError::invalid("缺少邮件的纯文本渲染结果。".into()))?,
        attachments: &input.attachments,
    };
    approved(
        ctx,
        email::follow_up::approve(
            &ctx.db,
            input.mentor_id,
            input.draft_id,
            &input.reply_to,
            &approval,
            now(),
        )
        .await?,
        "追问已取消、已发送，或导师已无法追问，请刷新往来后查看。",
        "有附件的材料已被删除。重新选择附件后再发送。",
    )
}
async fn write_follow_up(
    ctx: &Context,
    input: FirstDraftEdit,
) -> Result<writing::Letter, CallError> {
    let _permit = ctx.model_limit.acquire().await;
    let chosen = model::chosen(&ctx.db)
        .await?
        .ok_or(writing::Error::NoModel)?;
    let references = material::for_writing(&ctx.db).await?;
    let previous = task::history(&mut *ctx.db.acquire().await?, input.mentor_id).await?;
    let prepared = writing::prepare(
        &ctx.db,
        input.mentor_id,
        &input.attachments,
        writing::Basis {
            chosen: &chosen,
            references: &references,
            kind: writing::Kind::FollowUp(&previous),
        },
    )
    .await?;
    let email_id = email::follow_up::draft(&ctx.db, input.mentor_id)
        .await?
        .map(|draft| draft.id);
    let at = now();
    Ok(writing::write(
        &ctx.net,
        &prepared,
        &writing::Letter {
            subject: String::new(),
            body_md: input.body_md,
        },
        |purpose, usage| {
            model::usage::record_for_email(
                &ctx.db,
                chosen.id,
                &chosen.model,
                purpose,
                email_id,
                usage,
                at,
            )
        },
    )
    .await?)
}

impl From<writing::reply::Error> for CallError {
    fn from(error: writing::reply::Error) -> Self {
        match error {
            writing::reply::Error::NoTarget => CallError::not_found(
                "回的那封信已删除或缺少邮件编号，无法在原线程里回复。请点另一封回信旁的“回复”；\
                缺少编号的请到邮箱里回复。",
            ),
            writing::reply::Error::Db(error) => error.into(),
        }
    }
}

async fn write_reply(ctx: &Context, input: ReplyDraftEdit) -> Result<writing::Letter, CallError> {
    let _permit = ctx.model_limit.acquire().await;
    let chosen = model::chosen(&ctx.db)
        .await?
        .ok_or(writing::Error::NoModel)?;
    let references = material::for_writing(&ctx.db).await?;
    let previous = writing::reply::history(&ctx.db, input.mentor_id, &input.reply_to).await?;
    let prepared = writing::prepare(
        &ctx.db,
        input.mentor_id,
        &input.attachments,
        writing::Basis {
            chosen: &chosen,
            references: &references,
            kind: writing::Kind::Reply(&previous),
        },
    )
    .await?;
    let email_id = email::reply_draft(&ctx.db, input.mentor_id)
        .await?
        .filter(|draft| draft.reply_to == input.reply_to)
        .map(|draft| draft.id);
    let at = now();
    Ok(writing::write(
        &ctx.net,
        &prepared,
        &writing::Letter {
            subject: String::new(),
            body_md: input.body_md,
        },
        |purpose, usage| {
            model::usage::record_for_email(
                &ctx.db,
                chosen.id,
                &chosen.model,
                purpose,
                email_id,
                usage,
                at,
            )
        },
    )
    .await?)
}

async fn discard_follow_up_draft(ctx: &Context, key: EmailId) -> Result<(), CallError> {
    require_applied(
        email::follow_up::discard(&ctx.db, key.id, now()).await?,
        "追问已取消或已发送，请刷新往来后查看。",
    )
}

#[derive(Debug, Deserialize, TS)]
pub struct AiReplyOutcome {
    pub enabled: bool,
}

#[derive(Debug, Deserialize, TS)]
#[serde(rename_all = "camelCase")]
pub struct ReplyMessage {
    pub message_id: i64,
}

#[derive(Debug, Deserialize, TS)]
#[serde(rename_all = "camelCase")]
pub struct ReplyOutcomeEdit {
    pub message_id: i64,
    pub outcome: email::outcome::ReplyOutcome,
}

async fn get_ai_reply_outcome(ctx: &Context, _: Nothing) -> Result<bool, CallError> {
    Ok(email::outcome::enabled(&ctx.db).await?)
}
async fn set_ai_reply_outcome(ctx: &Context, input: AiReplyOutcome) -> Result<(), CallError> {
    email::outcome::set_enabled(&ctx.db, input.enabled).await?;
    ctx.reply_processor.wake();
    Ok(())
}
async fn set_reply_outcome(ctx: &Context, input: ReplyOutcomeEdit) -> Result<(), CallError> {
    if !email::outcome::set(&ctx.db, input.message_id, input.outcome).await? {
        return Err(CallError::invalid(
            "只能标记导师的回信，请重新选择回信。".into(),
        ));
    }
    ctx.reply_processor.wake();
    Ok(())
}
async fn retry_reply_outcome(ctx: &Context, input: ReplyMessage) -> Result<(), CallError> {
    require_applied(
        email::outcome::retry(&ctx.db, input.message_id).await?,
        "这封回信没有标记失败的结果可重试。",
    )?;
    ctx.reply_processor.wake();
    Ok(())
}
async fn reply_processing(
    ctx: &Context,
    _: Nothing,
) -> Result<email::outcome::ReplyProcessing, CallError> {
    Ok(email::outcome::progress(&ctx.db).await?)
}
async fn resume_reply_processing(ctx: &Context, _: Nothing) -> Result<(), CallError> {
    email::outcome::resume(&ctx.db).await?;
    ctx.reply_processor.wake();
    Ok(())
}

/// An email the app sent, or mail seen in the mailbox, in the correspondence with a mentor.
#[derive(Debug, Serialize, TS)]
#[serde(rename_all = "camelCase")]
pub enum Entry {
    Email(email::Email),
    Message(receive::Message),
    Todo(email::todos::Todo),
}

/// The correspondence with a mentor, oldest first; emails still to go out come last.
async fn list_correspondence(ctx: &Context, input: MentorId) -> Result<Vec<Entry>, CallError> {
    let mut tx = ctx.db.begin().await?;
    let emails = email::list_in(&mut tx, input.id).await?;
    let messages = receive::list_in(&mut tx, input.id).await?;
    let mut entries: Vec<(i64, Entry)> = emails
        .into_iter()
        .map(|e| {
            let at = match e.delivery {
                email::Delivery::MaybeSent { sent_at, .. }
                | email::Delivery::Sent { sent_at, .. }
                | email::Delivery::Bounced { sent_at, .. } => sent_at,
                email::Delivery::Approved
                | email::Delivery::Sending { .. }
                | email::Delivery::Failed { .. }
                | email::Delivery::Canceled { .. } => i64::MAX,
            };
            (at, Entry::Email(e))
        })
        .chain(messages.into_iter().map(|m| (m.sent_at, Entry::Message(m))))
        .collect();
    let todos = email::todos::for_mentor(&mut *tx, input.id).await?;
    let mut from_replies: BTreeMap<i64, Vec<email::todos::Todo>> = BTreeMap::new();
    for todo in todos {
        if let Some(id) = todo.message_id {
            from_replies.entry(id).or_default().push(todo);
        } else {
            entries.push((todo.created_at, Entry::Todo(todo)));
        }
    }
    entries.sort_by_key(|(at, _)| *at);
    let mut result = Vec::new();
    for (_, entry) in entries {
        let message = match &entry {
            Entry::Message(m) => Some(m.id),
            _ => None,
        };
        result.push(entry);
        if let Some(todos) = message.and_then(|id| from_replies.remove(&id)) {
            result.extend(todos.into_iter().map(Entry::Todo));
        }
    }
    Ok(result)
}

async fn mark_read(ctx: &Context, input: MentorId) -> Result<(), CallError> {
    Ok(receive::mark_read(&ctx.db, input.id, now()).await?)
}

#[derive(Debug, Deserialize, TS)]
pub struct EmailId {
    pub id: i64,
}

async fn withdraw_email(ctx: &Context, input: EmailId) -> Result<(), CallError> {
    let applied = email::withdraw(&ctx.db, input.id, now())
        .await
        .map_err(|e| {
            if e.as_database_error()
                .is_some_and(|e| e.is_unique_violation())
            {
                CallError::invalid(
                    "这位导师已有同类草稿。请先完成或清空那封草稿，再改回这封。".into(),
                )
            } else {
                e.into()
            }
        })?;
    require_applied(applied, "这封邮件已在发送或已发出，无法改回草稿。")?;
    ctx.sender.wake();
    Ok(())
}

async fn retry_email(ctx: &Context, input: EmailId) -> Result<(), CallError> {
    let applied = email::retry(&ctx.db, input.id, now()).await?;
    require_applied(applied, "这封邮件已取消，或已不在可重试状态。")?;
    ctx.sender.wake();
    Ok(())
}

async fn confirm_unsent_email(ctx: &Context, input: EmailId) -> Result<(), CallError> {
    require_applied(
        email::confirm_unsent(&ctx.db, input.id, now()).await?,
        "这封邮件没有待确认的取消记录，或发送状态已变化。",
    )
}

async fn resend_email(ctx: &Context, input: EmailId) -> Result<(), CallError> {
    approved(
        ctx,
        email::resend(&ctx.db, input.id, now()).await?,
        "这封邮件已经重新发送过，或导师已移入回收站。",
        "有附件的材料已被删除，没法按原样重新发送。",
    )
}

/// UTC milliseconds.
fn now() -> i64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map_or(0, |d| d.as_millis() as i64)
}

async fn list_conversations(
    ctx: &Context,
    query: mentor::conversations::ConversationQuery,
) -> Result<mentor::conversations::ConversationPage, CallError> {
    Ok(mentor::conversations::list(&ctx.db, &query).await?)
}

async fn unread_count(ctx: &Context, _: Nothing) -> Result<i64, CallError> {
    Ok(mentor::conversations::unread_count(&ctx.db).await?)
}

#[derive(Debug, Serialize, TS)]
#[serde(rename_all = "camelCase")]
pub struct ReceiveStatus {
    pub received_at: Option<i64>,
}

async fn receive_status(ctx: &Context, _: Nothing) -> Result<ReceiveStatus, CallError> {
    Ok(ReceiveStatus {
        received_at: ctx.receiver.received_at(),
    })
}

async fn receive_now(ctx: &Context, input: Nothing) -> Result<ReceiveStatus, CallError> {
    ctx.receiver
        .receive_now()
        .await
        .map_err(|e| CallError::internal(format!("database: {e}")))?;
    receive_status(ctx, input).await
}

#[derive(Debug, Deserialize, TS)]
pub struct NotificationSetting {
    pub on: bool,
}

async fn get_reply_notifications(ctx: &Context, _: Nothing) -> Result<bool, CallError> {
    Ok(profile::reply_notifications(&ctx.db).await?)
}

async fn set_reply_notifications(
    ctx: &Context,
    input: NotificationSetting,
) -> Result<(), CallError> {
    Ok(profile::set_reply_notifications(&ctx.db, input.on).await?)
}

async fn get_background_notifications(ctx: &Context, _: Nothing) -> Result<bool, CallError> {
    Ok(profile::background_notifications(&ctx.db).await?)
}
async fn set_background_notifications(
    ctx: &Context,
    input: NotificationSetting,
) -> Result<(), CallError> {
    Ok(profile::set_background_notifications(&ctx.db, input.on).await?)
}

#[derive(Debug, Deserialize, TS)]
#[serde(rename_all = "camelCase")]
pub struct ImportFile {
    pub file_name: String,
    pub data: String,
    pub setup: mentor::import::Setup,
}

#[derive(Debug, Deserialize, TS)]
pub struct ImportMentors {
    #[serde(flatten)]
    pub file: ImportFile,
    pub decisions: mentor::import::Decisions,
}

fn uploaded_bytes(data: &str) -> Result<Vec<u8>, CallError> {
    if data.len() / 4 * 3 > material::MAX_SIZE + 2 {
        return Err(CallError::invalid(
            "文件超过 20 MB，请缩小文件后重试".into(),
        ));
    }
    let bytes = BASE64_STANDARD.decode(data).map_err(CallError::bad_input)?;
    if bytes.len() > material::MAX_SIZE {
        return Err(CallError::invalid(
            "文件超过 20 MB，请缩小文件后重试".into(),
        ));
    }
    Ok(bytes)
}

async fn read_import(input: &ImportFile) -> Result<Vec<sheet::Sheet>, CallError> {
    let bytes = uploaded_bytes(&input.data)?;
    let name = input.file_name.clone();
    tokio::task::spawn_blocking(move || sheet::read(&name, &bytes))
        .await
        .map_err(CallError::internal)?
        .map_err(|e| {
            if let sheet::ReadError::Damaged(detail) = &e {
                tracing::warn!("reading table: {detail}");
            }
            CallError::invalid(e.to_string())
        })
}
#[derive(Debug, Deserialize, TS)]
pub struct PreviewImport {
    #[serde(flatten)]
    pub file: ImportFile,
    #[ts(optional)]
    pub decisions: Option<mentor::import::Decisions>,
}
async fn preview_import(
    ctx: &Context,
    input: PreviewImport,
) -> Result<mentor::import::Preview, CallError> {
    let sheets = read_import(&input.file).await?;
    Ok(mentor::import::preview_decisions(
        &ctx.db,
        &sheets,
        &input.file.setup,
        &input.decisions.unwrap_or_default(),
    )
    .await?)
}
impl From<mentor::import::Error> for CallError {
    fn from(error: mentor::import::Error) -> Self {
        match error {
            mentor::import::Error::Invalid(reason) => Self::invalid(reason),
            mentor::import::Error::Database(error) => error.into(),
        }
    }
}

async fn import_mentors(
    ctx: &Context,
    input: ImportMentors,
) -> Result<mentor::import::Imported, CallError> {
    let sheets = read_import(&input.file).await?;
    let result =
        mentor::import::import(&ctx.db, &sheets, &input.file.setup, &input.decisions, now())
            .await?;
    tracing::info!(
        created = result.created,
        updated = result.updated,
        "imported mentors"
    );
    Ok(result)
}

#[derive(Debug, Clone, Copy, Deserialize, TS)]
#[serde(rename_all = "camelCase")]
pub enum TableFormat {
    Xlsx,
    Csv,
}

#[derive(Debug, Deserialize, TS)]
pub struct ExportMentors {
    pub scope: mentor::export::Scope,
    pub format: TableFormat,
    pub note: bool,
}

pub async fn export_mentors(ctx: &Context, mut input: ExportMentors) -> Result<Vec<u8>, CallError> {
    if let mentor::export::Scope::Filter(filter) = &mut input.scope {
        filter.check().map_err(CallError::invalid)?;
    }
    let rows = mentor::export::export(&ctx.db, &input.scope, input.note, now()).await?;
    tokio::task::spawn_blocking(move || write_table(rows, input.format))
        .await
        .map_err(CallError::internal)?
}

fn write_table(rows: Vec<Vec<String>>, format: TableFormat) -> Result<Vec<u8>, CallError> {
    match format {
        TableFormat::Csv => Ok(sheet::write_csv(&rows)),
        TableFormat::Xlsx => {
            for row in rows.iter().skip(1) {
                for (field, value) in rows[0].iter().zip(row) {
                    if value.chars().count() > 32767 {
                        let advice = if field == "备注" {
                            "改导出 CSV，或取消“包含备注”后重试"
                        } else {
                            "改导出 CSV 后重试"
                        };
                        return Err(CallError::invalid(format!(
                            "{}的“{field}”超过 XLSX 每格 32767 个字的上限，请{advice}",
                            row[0]
                        )));
                    }
                }
            }
            let instructions = mentor::export::instructions(&rows[0]);
            sheet::write_xlsx(&[
                sheet::Sheet {
                    name: "导师".into(),
                    rows,
                },
                instructions,
            ])
            .map_err(CallError::internal)
        }
    }
}

#[cfg(test)]
mod table_tests {
    use super::*;

    #[test]
    fn long_cells_identify_the_mentor_and_field_and_allow_csv() {
        for field in ["备注", "研究方向"] {
            let rows = vec![
                vec!["姓名".into(), field.into()],
                vec!["王明".into(), "长".repeat(32768)],
            ];
            let error = write_table(rows.clone(), TableFormat::Xlsx).unwrap_err();
            assert!(matches!(error.code, ErrorCode::Invalid));
            assert!(error.message.contains("王明") && error.message.contains(field));
            assert!(error.message.contains("CSV"));
            if field == "备注" {
                assert!(error.message.contains("包含备注"));
            }
            assert!(write_table(rows, TableFormat::Csv).is_ok());
        }
        let rows = vec![
            vec!["姓名".into(), "备注".into()],
            vec!["王明".into(), "长".repeat(32767)],
        ];
        assert!(write_table(rows, TableFormat::Xlsx).is_ok());
    }
}

#[cfg(test)]
use test_fixtures::provider;

#[cfg(test)]
mod writing_tests {
    use super::*;
    use std::{
        future::{Future, poll_fn},
        sync::Arc,
        task::Poll,
    };

    #[sqlx::test(migrator = "db::MIGRATOR")]
    async fn queued_letter_uses_the_model_address_saved_before_its_turn(db: SqlitePool) {
        let mentor = mentor::create(
            &db,
            &mentor::MentorFields {
                name: "王伟".into(),
                ..Default::default()
            },
            0,
        )
        .await
        .unwrap();
        let ctx = Context::new(db, net::Net::new(vec![]).unwrap(), Default::default());
        let (old_address, old_received) = provider::scripted(vec![(
            "<subject>申请</subject><body>旧地址的信</body>",
            "stop",
        )])
        .await
        .unwrap();
        let (new_address, new_received) = provider::scripted(vec![(
            "<subject>申请</subject><body>新地址的信</body>",
            "stop",
        )])
        .await
        .unwrap();
        let form = |address: &str| model::ModelForm {
            name: String::new(),
            provider: model::providers::CUSTOM.into(),
            base_url: address.into(),
            model: "m".into(),
            input_price: None,
            output_price: None,
        };
        let id = model::create(&ctx.db, &form(&old_address).check().unwrap(), "", 0)
            .await
            .unwrap();
        assert!(
            model::agree(&ctx.db, id, "custom", Some(&old_address))
                .await
                .unwrap()
        );

        let mut permits = Vec::new();
        for _ in 0..model::background::CONCURRENCY {
            permits.push(ctx.model_limit.acquire().await);
        }
        let owners = Arc::strong_count(permits[0].semaphore());
        let mut writing = std::pin::pin!(call(
            &ctx,
            "writeLetter",
            serde_json::json!({
                "mentorId": mentor, "subject": "", "bodyMd": "", "attachments": []
            }),
        ));
        // An owned acquire retains the semaphore while queued. Wait for that so the edit
        // cannot race with database reads that precede acquiring capacity.
        poll_fn(|cx| {
            assert!(writing.as_mut().poll(cx).is_pending());
            if Arc::strong_count(permits[0].semaphore()) > owners {
                Poll::Ready(())
            } else {
                Poll::Pending
            }
        })
        .await;

        update_model_config(
            &ctx,
            ModelConfigEdit {
                id,
                form: form(&new_address),
                api_key: String::new(),
            },
        )
        .await
        .unwrap();
        agree_model(
            &ctx,
            ModelAgreement {
                id,
                provider: "custom".into(),
                base_url: Some(new_address),
            },
        )
        .await
        .unwrap();
        drop(permits);
        let letter = writing.await.unwrap();

        assert!(
            old_received.lock().unwrap().is_empty(),
            "queued request reached the old model address"
        );
        assert_eq!(new_received.lock().unwrap().len(), 1);
        assert_eq!(
            letter,
            serde_json::json!({"subject": "申请", "bodyMd": "新地址的信"})
        );
    }

    #[sqlx::test(migrator = "db::MIGRATOR")]
    async fn first_follow_up_and_reply_checks_report_student_decisions(db: SqlitePool) {
        let mentor = mentor::create(
            &db,
            &mentor::MentorFields {
                name: "王伟".into(),
                ..Default::default()
            },
            now(),
        )
        .await
        .unwrap();
        let ctx = Context::new(db, net::Net::new(vec![]).unwrap(), Default::default());
        // Follow-ups share checkReplyDraft with replies; all entry points reach check::letter.
        for op in ["checkFirstDraft", "checkReplyDraft"] {
            let problems = call(
                &ctx,
                op,
                serde_json::json!({
                    "mentorId": mentor, "subject": "申请", "replyTo": "<target@example.edu>",
                    "bodyMd": "王老师：时间为【请补充：时间】。", "attachments": []
                }),
            )
            .await
            .unwrap();
            assert_eq!(
                problems,
                serde_json::json!([{
                    "kind": "placeholder", "name": "【请补充：时间】"
                }])
            );
        }
    }
}

#[cfg(test)]
mod reply_outcome_tests {
    use super::*;
    use serde_json::{Value, json};

    #[sqlx::test(migrator = "db::MIGRATOR")]
    async fn operations_expose_reply_only_fields_and_reject_non_reply_edits(db: SqlitePool) {
        sqlx::query(
            r#"INSERT INTO mailbox (id, address, sender_name, provider, smtp_host,
                smtp_port, smtp_security, imap_host, imap_port, imap_security, auth,
                secret, daily_limit, min_interval_s, rotation_order, created_at)
            VALUES (1, 'me@example.com', '学生', 'custom', 'localhost', 465, 'tls',
                'localhost', 993, 'tls', 'password', 'fixture', 50, 60, 1, 0)"#,
        )
        .execute(&db)
        .await
        .unwrap();
        let m = mentor::create(
            &db,
            &mentor::MentorFields {
                name: "导师".into(),
                ..Default::default()
            },
            1,
        )
        .await
        .unwrap();
        let mut ids = vec![];
        for (uid, kind) in ["reply", "auto_reply", "sent"].into_iter().enumerate() {
            let id = sqlx::query(
                r#"INSERT INTO message (mailbox_id, folder, uidvalidity, uid, kind,
                    sent_at, from_address, truncated)
                VALUES (1, 'INBOX', 1, ?, ?, 1, 'mentor@example.edu', 0)"#,
            )
            .bind(uid as i64)
            .bind(kind)
            .execute(&db)
            .await
            .unwrap()
            .last_insert_rowid();
            sqlx::query("INSERT INTO message_mentor(mentor_id,message_id) VALUES(?,?)")
                .bind(m)
                .bind(id)
                .execute(&db)
                .await
                .unwrap();
            ids.push(id);
        }
        let ctx = Context::new(db, net::Net::new(vec![]).unwrap(), Default::default());
        assert_eq!(
            call(&ctx, "getAiReplyOutcome", json!({})).await.unwrap(),
            false
        );
        call(&ctx, "setAiReplyOutcome", json!({"enabled":true}))
            .await
            .unwrap();
        assert_eq!(
            call(&ctx, "getAiReplyOutcome", json!({})).await.unwrap(),
            true
        );
        let entries = call(&ctx, "listCorrespondence", json!({"id":m}))
            .await
            .unwrap();
        assert_eq!(entries[0]["message"]["marking"], json!({"state":"marking"}));
        assert_eq!(entries[0]["message"]["outcome"], Value::Null);
        for i in [1, 2] {
            assert!(entries[i]["message"].get("outcome").is_none());
            assert!(entries[i]["message"].get("marking").is_none());
            let error = call(
                &ctx,
                "setReplyOutcome",
                json!({"messageId":ids[i],"outcome":"interested"}),
            )
            .await
            .unwrap_err();
            assert!(matches!(error.code, ErrorCode::Invalid));
            assert!(error.message.contains("导师的回信"));
        }
        let claim = email::outcome::next(&ctx.db).await.unwrap().unwrap();
        email::outcome::fail(&ctx.db, claim, "格式不对")
            .await
            .unwrap();
        assert_eq!(
            call(&ctx, "replyProcessing", json!({})).await.unwrap()["failed"],
            1
        );
        call(&ctx, "retryReplyOutcome", json!({"messageId":ids[0]}))
            .await
            .unwrap();
        assert_eq!(
            call(&ctx, "replyProcessing", json!({})).await.unwrap()["remaining"],
            1
        );
        call(
            &ctx,
            "setReplyOutcome",
            json!({"messageId":ids[0],"outcome":"no_slot"}),
        )
        .await
        .unwrap();
        assert_eq!(
            call(&ctx, "replyProcessing", json!({})).await.unwrap()["remaining"],
            0
        );
        let entries = call(&ctx, "listCorrespondence", json!({"id":m}))
            .await
            .unwrap();
        assert_eq!(entries[0]["message"]["outcome"], "no_slot");
        assert_eq!(entries[0]["message"]["marking"], Value::Null);
        call(&ctx, "resumeReplyProcessing", json!({}))
            .await
            .unwrap();
        call(&ctx, "setAiReplyOutcome", json!({"enabled":false}))
            .await
            .unwrap();
    }
}
