use std::time::Duration;

use rmcp::handler::server::tool::schema_for_input;
use rmcp::model::{
    CallToolRequestParams, CallToolResponse, CallToolResult, ContentBlock, Implementation,
    ListToolsResult, PaginatedRequestParams, ProgressNotificationParam, ServerCapabilities,
    ServerConfig, Tool,
};
use rmcp::service::RequestContext;
use rmcp::{ErrorData, RoleServer, ServerHandler, ServiceExt};
use serde::Deserialize;
use serde_json::{Value, json};

use crate::client;

struct Server;

#[derive(Deserialize, rmcp::schemars::JsonSchema)]
#[schemars(crate = "rmcp::schemars")]
struct ListInput {}

#[derive(Deserialize, rmcp::schemars::JsonSchema)]
#[schemars(crate = "rmcp::schemars")]
struct DescribeInput {
    /// 从 list_operations 获取的操作名。
    name: String,
}

#[derive(Deserialize, rmcp::schemars::JsonSchema)]
#[schemars(crate = "rmcp::schemars")]
struct CallInput {
    /// 从 list_operations 获取的操作名。
    name: String,
    /// 按 describe_operation 返回的 Input 类型填写；无参数时传 {}。
    input: serde_json::Map<String, Value>,
}

pub async fn run() -> Result<(), String> {
    let (reader, writer) = rmcp::transport::stdio();
    // Match the app channel so a base64-encoded 20 MiB material also fits through MCP.
    let transport = rmcp::transport::async_rw::AsyncRwTransport::new(reader, writer)
        .with_max_line_length(28 * 1024 * 1024);
    let service = Server
        .serve(transport)
        .await
        .map_err(|error| format!("MCP 连接失败：{error}"))?;
    service
        .waiting()
        .await
        .map_err(|error| format!("MCP 服务结束时出错：{error}"))?;
    Ok(())
}

impl ServerHandler for Server {
    fn get_info(&self) -> ServerConfig {
        ServerConfig::new(ServerCapabilities::builder().enable_tools().build())
            .with_server_info(Implementation::new("autoemail", env!("CARGO_PKG_VERSION")))
            .with_instructions(
                "这是 Auto Email Sender，帮学生整理导师资料、生成邮件草稿并联系导师的桌面应用。
先 list_operations 列出操作，再 describe_operation 查看说明与类型，最后 call_operation 调用。
向导师发信或恢复发信、永久丢失数据、同意向模型服务商发送数据等关键操作，需用户在应用里确认；
某些操作只在特定情况下需确认，以操作说明和应用判断为准。调用会等用户回答，最多 5 分钟。
若连接失败，请告诉用户打开 Auto Email Sender，并在设置里允许 Agent 连接，再重新调用。",
            )
    }

    async fn list_tools(
        &self,
        _: Option<PaginatedRequestParams>,
        _: RequestContext<RoleServer>,
    ) -> Result<ListToolsResult, ErrorData> {
        Ok(ListToolsResult::with_all_items(vec![
            Tool::new(
                "list_operations",
                "先调用此工具，从运行中的应用列出操作、分组和确认方式；
然后用 describe_operation 查看输入类型，最后用 call_operation 调用。",
                schema_for_input::<ListInput>()
                    .map_err(|error| ErrorData::internal_error(error, None))?,
            ),
            Tool::new(
                "describe_operation",
                "先用 list_operations 找操作，再用此工具查看说明、确认方式和输入输出 TypeScript 声明；
按 Input 类型构造参数后用 call_operation 调用。",
                schema_for_input::<DescribeInput>()
                    .map_err(|error| ErrorData::internal_error(error, None))?,
            ),
            Tool::new(
                "call_operation",
                "先 list_operations，再 describe_operation 查看输入类型，最后用此工具调用应用操作；
需要确认时会等待用户在应用里回答，最多 5 分钟。",
                schema_for_input::<CallInput>()
                    .map_err(|error| ErrorData::internal_error(error, None))?,
            ),
        ]))
    }

    async fn call_tool(
        &self,
        request: CallToolRequestParams,
        context: RequestContext<RoleServer>,
    ) -> Result<CallToolResponse, ErrorData> {
        let result = tokio::select! {
            result = execute(request, &context) => result,
            () = context.ct.cancelled() => {
                Err("调用已取消，未回答的确认请求已作废；已开始的操作会继续完成".into())
            }
        };
        let result = match result {
            Ok(value) => match value.get("error") {
                Some(error) => CallToolResult::error(vec![ContentBlock::text(error.to_string())]),
                None => CallToolResult::success(vec![ContentBlock::text(value["ok"].to_string())]),
            },
            Err(message) => CallToolResult::error(vec![ContentBlock::text(message)]),
        };
        Ok(result.into())
    }
}

async fn execute(
    request: CallToolRequestParams,
    context: &RequestContext<RoleServer>,
) -> Result<Value, String> {
    // MCP permits omitted arguments for parameterless tools.
    let arguments = Value::Object(request.arguments.unwrap_or_default());
    let invalid = |error| format!("工具参数不正确：{error}");
    let (op, input) = match request.name.as_ref() {
        "list_operations" => {
            serde_json::from_value::<ListInput>(arguments).map_err(invalid)?;
            ("listOperations".into(), json!({}))
        }
        "describe_operation" => {
            let input: DescribeInput = serde_json::from_value(arguments).map_err(invalid)?;
            ("describeOperation".into(), json!({"name":input.name}))
        }
        "call_operation" => {
            let input: CallInput = serde_json::from_value(arguments).map_err(invalid)?;
            (input.name, Value::Object(input.input))
        }
        name => return Err(format!("没有工具 {name}")),
    };
    let (waiting, confirmation) = tokio::sync::oneshot::channel();
    let call = client::call(&op, input, |_| {
        // This only signals progress; the final response owns the result.
        let _ = waiting.send(());
    });
    tokio::pin!(call);
    tokio::select! {
        biased;
        result = &mut call => return result,
        waiting = confirmation => { waiting.map_err(|_| "应用未返回确认状态")?; }
    }
    let Some(token) = context.meta.get_progress_token() else {
        return call.await;
    };
    let mut interval = tokio::time::interval(Duration::from_secs(10));
    let mut progress = 0.0;
    loop {
        tokio::select! {
            biased;
            result = &mut call => return result,
            _ = interval.tick() => {
                progress += 1.0;
                context.peer
                    .notify_progress(
                        ProgressNotificationParam::new(token.clone(), progress)
                            .with_message("正在等待用户在 Auto Email Sender 里确认，最多 5 分钟"),
                    )
                    .await
                    .map_err(|error| format!("MCP 进度通知失败：{error}"))?;
            }
        }
    }
}
