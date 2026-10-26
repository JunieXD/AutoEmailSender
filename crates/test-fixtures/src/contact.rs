use sqlx::SqliteConnection;

pub type ContactSummary = (i64, String, Option<i64>, Option<i64>, i64, i64);

/// Asserts every stored column matches the view, and returns the contact fields used by tests.
pub async fn reconciled(conn: &mut SqliteConnection) -> Result<Vec<ContactSummary>, sqlx::Error> {
    let different: bool = sqlx::query_scalar(
        r#"SELECT EXISTS (
            SELECT *
            FROM mentor_contact EXCEPT
            SELECT *
            FROM mentor_contact_live
        ) OR EXISTS (
            SELECT *
            FROM mentor_contact_live EXCEPT
            SELECT *
            FROM mentor_contact
        )"#,
    )
    .fetch_one(&mut *conn)
    .await?;
    assert!(
        !different,
        "stored contact summary differs from its definition"
    );
    sqlx::query_as(
        r#"SELECT mentor_id, status, last_out_at, last_reply_at, contact_count, unread
        FROM mentor_contact
        ORDER BY mentor_id"#,
    )
    .fetch_all(conn)
    .await
}
