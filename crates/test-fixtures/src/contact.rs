use sqlx::SqliteConnection;

pub type ContactSummary = (i64, String, Option<i64>, Option<i64>, i64, i64);

/// Asserts the stored summary matches the view, and returns it.
pub async fn reconciled(conn: &mut SqliteConnection) -> Result<Vec<ContactSummary>, sqlx::Error> {
    async fn summary(
        conn: &mut SqliteConnection,
        table: &str,
    ) -> Result<Vec<ContactSummary>, sqlx::Error> {
        let sql = format!(
            "SELECT mentor_id, status, last_out_at, last_reply_at, contact_count, unread FROM {table} ORDER BY mentor_id"
        );
        sqlx::query_as(sqlx::AssertSqlSafe(sql))
            .fetch_all(conn)
            .await
    }
    let stored = summary(conn, "mentor_contact").await?;
    assert_eq!(stored, summary(conn, "mentor_contact_live").await?);
    Ok(stored)
}
