use std::error::Error;

use api::{Context, call_agent};
use serde_json::json;
use sqlx::SqlitePool;

type Result<T = ()> = std::result::Result<T, Box<dyn Error + Send + Sync>>;

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn catalog_only_lists_agent_operations(db: SqlitePool) -> Result {
    let ctx = Context::new(db, net::Net::new(vec![])?, Default::default());
    let catalog = call_agent(
        &ctx,
        "listOperations",
        json!({}),
        |_| {},
        std::future::pending(),
    )
    .await
    .map_err(|e| e.message)?;
    let operations = catalog.as_array().ok_or("catalog is not an array")?;
    assert!(operations.iter().any(|op| op["name"] == "getProfile"));
    for name in [
        "getAgentAccess",
        "setAgentAccess",
        "listAgentRequests",
        "answerAgentRequest",
    ] {
        assert!(!operations.iter().any(|op| op["name"] == name));
    }
    Ok(())
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn description_provides_input_types_and_keeps_ui_operations_private(
    db: SqlitePool,
) -> Result {
    let ctx = Context::new(db, net::Net::new(vec![])?, Default::default());
    let description = call_agent(
        &ctx,
        "describeOperation",
        json!({"name":"rescheduleEmail"}),
        |_| {},
        std::future::pending(),
    )
    .await
    .map_err(|e| e.message)?;
    let types = description["typescript"].as_str().ok_or("missing types")?;
    assert!(types.contains("type RescheduleEmail ="));
    assert!(types.contains("plannedAt: number"));
    assert!(types.contains("type Input = RescheduleEmail;"));
    assert!(types.contains("type Output = null;"));
    assert!(
        call_agent(
            &ctx,
            "describeOperation",
            json!({"name":"answerAgentRequest"}),
            |_| {},
            std::future::pending(),
        )
        .await
        .is_err()
    );
    Ok(())
}
