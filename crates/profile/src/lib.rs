//! The user's own profile: who they are and how they want their letters (docs/data-model.md).

use serde::{Deserialize, Serialize};
use sqlx::SqlitePool;
use ts_rs::TS;

/// What the user says about themselves, which writing and matching draw on.
#[derive(Debug, Serialize, TS)]
#[serde(rename_all = "camelCase")]
pub struct Profile {
    pub name: Option<String>,
    pub research_interests: Option<String>,
    pub rewrite_preference: Option<String>,
}

/// The profile as typed; blank fields are cleared.
#[derive(Debug, Deserialize, TS)]
#[serde(rename_all = "camelCase")]
pub struct ProfileForm {
    pub name: String,
    pub research_interests: String,
    pub rewrite_preference: String,
}

fn filled(text: &str) -> Option<&str> {
    Some(text.trim()).filter(|t| !t.is_empty())
}

pub async fn get(db: &SqlitePool) -> Result<Profile, sqlx::Error> {
    sqlx::query_as!(
        Profile,
        "SELECT name, research_interests, rewrite_preference FROM profile"
    )
    .fetch_one(db)
    .await
}

pub async fn update(db: &SqlitePool, form: &ProfileForm) -> Result<(), sqlx::Error> {
    let (name, research_interests, rewrite_preference) = (
        filled(&form.name),
        filled(&form.research_interests),
        filled(&form.rewrite_preference),
    );
    sqlx::query!(
        "UPDATE profile SET name = ?, research_interests = ?, rewrite_preference = ?",
        name,
        research_interests,
        rewrite_preference,
    )
    .execute(db)
    .await?;
    Ok(())
}
