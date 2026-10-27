use std::collections::BTreeSet;

use cloud_api::{library as wire, path};
use mentor::{import::Decisions, intake};
use reqwest::Method;
use serde::{Deserialize, Serialize};
use sqlx::{SqliteConnection, SqlitePool, types::Json};
use ts_rs::TS;

use crate::Error;

#[derive(Debug, Deserialize, TS)]
#[ts(rename = "LibrarySelection")]
pub struct Selection {
    pub ids: Vec<String>,
    #[serde(default)]
    #[ts(optional)]
    pub decisions: Option<Decisions>,
}

#[derive(Debug, Serialize, TS)]
#[ts(rename = "LibraryAddPreview")]
pub struct Preview {
    #[serde(flatten)]
    pub intake: intake::Preview,
    pub missing: u32,
}

#[derive(Debug, Serialize, TS)]
#[ts(rename = "LibraryAdded")]
pub struct Added {
    pub created: u32,
    pub supplemented: u32,
    pub missing: u32,
}

pub async fn fetch(
    client: &account::Client,
    db: &SqlitePool,
    ids: &[String],
) -> Result<wire::Fetched, Error> {
    if !(1..=200).contains(&ids.len()) || ids.iter().collect::<BTreeSet<_>>().len() != ids.len() {
        return Err(Error::Invalid(
            "一次请选择 1–200 位导师，不能重复选择。".into(),
        ));
    }
    Ok(client
        .authenticated(
            db,
            Method::POST,
            path::LIBRARY_FETCH,
            Some(&wire::FetchRequest { ids: ids.to_vec() }),
        )
        .await?)
}

pub async fn calculate(
    conn: &mut SqliteConnection,
    fetched: &wire::Fetched,
    selection: &Selection,
) -> Result<(Preview, Vec<intake::Change>), Error> {
    let ids = Json(
        fetched
            .records
            .iter()
            .map(|r| &r.preview.summary.id)
            .collect::<Vec<_>>(),
    );
    let targets = sqlx::query!(
        r#"SELECT library_id, mentor_id FROM mentor_library
        WHERE library_id IN (SELECT value FROM json_each(?))"#,
        ids
    )
    .fetch_all(&mut *conn)
    .await?;
    let mut rows = Vec::new();
    let mut errors = Vec::new();
    for record in &fetched.records {
        let number = selection
            .ids
            .iter()
            .position(|id| *id == record.preview.summary.id)
            .ok_or_else(|| {
                Error::Invalid("服务器返回了未选择的导师，请稍后重试或升级应用。".into())
            })? as u32
            + 1;
        match crate::profile(record) {
            Ok(profile) => rows.push(intake::Row {
                number,
                targets: targets
                    .iter()
                    .filter(|t| t.library_id == record.preview.summary.id)
                    .map(|t| t.mentor_id)
                    .collect(),
                profile,
            }),
            Err(reason) => errors.push(mentor::import::RowError {
                row: number,
                name: record.preview.summary.name.clone(),
                reason,
            }),
        }
    }
    let default = Decisions::default();
    let decisions = selection.decisions.as_ref().unwrap_or(&default);
    let (mut intake, changes) = intake::calculate(conn, rows, decisions).await?;
    intake.errors.extend(errors);
    intake.errors.sort_by_key(|e| e.row);
    Ok((
        Preview {
            intake,
            missing: fetched.missing.len() as u32,
        },
        changes,
    ))
}

pub async fn preview(
    client: &account::Client,
    db: &SqlitePool,
    input: &Selection,
) -> Result<Preview, Error> {
    let fetched = fetch(client, db, &input.ids).await?;
    let mut tx = db.begin().await?;
    Ok(calculate(&mut tx, &fetched, input).await?.0)
}

pub async fn write(
    db: &SqlitePool,
    input: &Selection,
    fetched: &wire::Fetched,
    now: i64,
) -> Result<Added, Error> {
    let mut tx = db.begin_with("BEGIN IMMEDIATE").await?;
    let (_, changes) = calculate(&mut tx, fetched, input).await?;
    let (result, accepted) = intake::apply(&mut tx, changes, now).await?;
    for (row, mentor) in accepted {
        let record = fetched
            .records
            .iter()
            .find(|r| r.preview.summary.id == input.ids[(row - 1) as usize])
            .unwrap_or_else(|| unreachable!("only fetched rows are accepted"));
        let id = &record.preview.summary.id;
        sqlx::query!(
            r#"INSERT INTO mentor_library (library_id, mentor_id, status, record)
            VALUES (?, ?, 'active', ?)
            ON CONFLICT (library_id) DO UPDATE SET status = excluded.status, record = excluded.record"#,
            id,
            mentor,
            Json(record)
        )
        .execute(&mut *tx)
        .await?;
        crate::updates::readded(&mut tx, id, record).await?;
    }
    tx.commit().await?;
    Ok(Added {
        created: result.created,
        supplemented: result.updated,
        missing: fetched.missing.len() as u32,
    })
}
