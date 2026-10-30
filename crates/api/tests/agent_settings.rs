use api::{Context, call_agent};
use serde_json::json;
use sqlx::SqlitePool;

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn installation_and_configuration_are_only_available_to_the_ui(
    db: SqlitePool,
) -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
    let ctx = Context::new(db, net::Net::new(vec![])?, std::path::Path::new(""));
    let catalog = api::call(&ctx, "listOperations", json!({}))
        .await
        .map_err(|e| e.message)?;
    let operations = catalog.as_array().ok_or("missing catalog")?;
    for name in [
        "getAgentCli",
        "installAgentCli",
        "uninstallAgentCli",
        "previewMcpConfig",
        "writeMcpConfig",
        "mcpConfigText",
    ] {
        assert!(!operations.iter().any(|op| op["name"] == name));
        for (op, input) in [
            (name, json!({"client": "claudeDesktop"})),
            ("describeOperation", json!({"name": name})),
        ] {
            let result = call_agent(&ctx, op, input, |_| {}, std::future::pending()).await;
            assert!(matches!(
                result,
                Err(api::CallError {
                    code: api::ErrorCode::Invalid,
                    ..
                })
            ));
        }
    }
    Ok(())
}
