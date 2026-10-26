use super::*;

#[derive(Debug, Serialize, TS)]
pub struct TodayDrafts {
    pub id: i64,
    pub name: String,
    pub generated: i64,
}

#[derive(Debug, Serialize, TS)]
pub struct TodayWork {
    pub drafts: Vec<TodayDrafts>,
    pub matched: i64,
    pub sent: i64,
}

/// The UI supplies local day boundaries as UTC milliseconds, with an exclusive end.
pub async fn today_work(db: &SqlitePool, start: i64, end: i64) -> Result<TodayWork, sqlx::Error> {
    let mut tx = db.begin().await?;
    let drafts = sqlx::query_as!(
        TodayDrafts,
        r#"SELECT t.id AS "id!", t.name AS "name!", count(*) AS "generated!: i64"
        FROM email e
        JOIN task t ON t.id = e.task_id
        WHERE e.generated_at >= ? AND e.generated_at < ?
        GROUP BY t.id
        ORDER BY max(e.generated_at) DESC, t.id DESC"#,
        start,
        end
    )
    .fetch_all(&mut *tx)
    .await?;
    let matched = sqlx::query_scalar!(
        r#"SELECT count(*) AS "count!: i64"
        FROM mentor
        WHERE match_completed_at >= ? AND match_completed_at < ? AND trashed_at IS NULL"#,
        start,
        end
    )
    .fetch_one(&mut *tx)
    .await?;
    let sent = sqlx::query_scalar!(
        r#"SELECT count(*) AS "count!: i64"
        FROM email
        WHERE state IN ('sent', 'bounced') AND sent_at >= ? AND sent_at < ?"#,
        start,
        end
    )
    .fetch_one(&mut *tx)
    .await?;
    tx.commit().await?;
    Ok(TodayWork {
        drafts,
        matched,
        sent,
    })
}
