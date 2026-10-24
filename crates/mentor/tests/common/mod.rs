use std::num::NonZeroU32;

use mentor::MentorQuery;
use sqlx::SqlitePool;

pub async fn names(db: &SqlitePool, keyword: Option<&str>) -> Vec<String> {
    let query = MentorQuery {
        status: None,
        keyword: keyword.map(Into::into),
        page: NonZeroU32::MIN,
    };
    let page = mentor::list(db, &query).await.unwrap();
    page.mentors.into_iter().map(|m| m.name).collect()
}
