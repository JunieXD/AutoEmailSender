use std::collections::BTreeMap;

use mentor::{
    Affiliation,
    export::Profile,
    intake,
    supplement::{append, comparable, same},
};
use serde::{Deserialize, Serialize};
use sqlx::{SqliteConnection, SqlitePool, types::Json};
use ts_rs::TS;

use crate::Error;

#[derive(
    Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Serialize, Deserialize, TS, sqlx::Type,
)]
#[serde(rename_all = "camelCase")]
#[sqlx(rename_all = "camelCase")]
#[ts(rename = "LibraryUpdateField")]
pub enum Field {
    Name,
    Research,
    SourceUrl,
    Title,
    PrimaryEmail,
    PrimaryAffiliation,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(untagged)]
pub(crate) enum Value {
    Text(Option<String>),
    Affiliation(Affiliation),
}

pub(crate) fn same_unit(a: &Affiliation, b: &Affiliation) -> bool {
    [
        (&a.university, &b.university),
        (&a.school, &b.school),
        (&a.department, &b.department),
    ]
    .into_iter()
    .all(|(a, b)| a.as_deref().map(comparable) == b.as_deref().map(comparable))
}

impl Value {
    pub(crate) fn same(&self, other: &Self) -> bool {
        match (self, other) {
            (Self::Text(a), Self::Text(b)) => {
                a.as_deref().map(comparable) == b.as_deref().map(comparable)
            }
            (Self::Affiliation(a), Self::Affiliation(b)) => same_unit(a, b),
            _ => false,
        }
    }
    fn display(&self) -> String {
        match self {
            Self::Text(value) => value.clone().unwrap_or_default(),
            Self::Affiliation(a) => [&a.university, &a.school, &a.department]
                .into_iter()
                .filter_map(|s| s.as_deref())
                .collect::<Vec<_>>()
                .join(" / "),
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, TS)]
#[serde(rename_all = "camelCase")]
#[ts(rename = "LibraryUpdate")]
pub struct Update {
    pub update_id: i64,
    pub id: i64,
    pub name: String,
    pub field: Field,
    pub affiliation: Option<Affiliation>,
    pub local: String,
    pub incoming: String,
    pub take: bool,
}

#[derive(Debug, Serialize, TS)]
#[ts(rename = "LibraryUpdateGroup")]
pub struct Group {
    pub field: Field,
    pub differences: Vec<Update>,
}

#[derive(Debug, Deserialize, TS)]
#[ts(rename = "LibraryUpdateChoices")]
pub struct Choices {
    pub choices: Vec<Update>,
}

struct Pending {
    id: i64,
    mentor_id: i64,
    field: Field,
    unit: Option<Affiliation>,
    original: Value,
    incoming: Value,
}

pub struct Plan {
    pub changes: Vec<intake::Change>,
    ids: Vec<i64>,
    pub mentors: u32,
}

async fn pending(conn: &mut SqliteConnection) -> Result<Vec<Pending>, sqlx::Error> {
    let rows = sqlx::query!(
        r#"SELECT u.id, l.mentor_id, u.field AS "field: Field", u.university, u.school,
            u.department, u.original AS "original: Json<Value>", u.incoming AS "incoming: Json<Value>"
        FROM library_update u JOIN mentor_library l ON l.library_id = u.library_id
        WHERE l.mentor_id IN (SELECT id FROM mentor WHERE trashed_at IS NULL)
        ORDER BY u.field, l.mentor_id, u.id"#
    )
    .fetch_all(conn)
    .await?;
    Ok(rows
        .into_iter()
        .map(|r| Pending {
            id: r.id,
            mentor_id: r.mentor_id,
            field: r.field,
            unit: (r.field == Field::Title).then(|| Affiliation {
                university: (!r.university.is_empty()).then_some(r.university),
                school: (!r.school.is_empty()).then_some(r.school),
                department: (!r.department.is_empty()).then_some(r.department),
                title: None,
            }),
            original: r.original.0,
            incoming: r.incoming.0,
        })
        .collect())
}

fn current(profile: &Profile, field: Field, unit: Option<&Affiliation>) -> Option<Value> {
    Some(match field {
        Field::Name => Value::Text(Some(profile.fields.name.clone())),
        Field::Research => Value::Text(profile.fields.research.clone()),
        Field::SourceUrl => Value::Text(profile.source_url.clone()),
        Field::PrimaryEmail => Value::Text(profile.fields.emails.first().cloned()),
        Field::PrimaryAffiliation => profile
            .fields
            .affiliations
            .first()
            .cloned()
            .map(Value::Affiliation)
            .unwrap_or(Value::Text(None)),
        Field::Title => Value::Text(
            profile
                .fields
                .affiliations
                .iter()
                .find(|a| unit.is_some_and(|unit| same_unit(a, unit)))?
                .title
                .clone(),
        ),
    })
}

fn visible(pending: &Pending, profile: &Profile) -> Option<Update> {
    let local = current(profile, pending.field, pending.unit.as_ref())?;
    if local.same(&pending.incoming) {
        return None;
    }
    Some(Update {
        update_id: pending.id,
        id: profile.id,
        name: profile.fields.name.clone(),
        field: pending.field,
        affiliation: pending.unit.clone(),
        local: local.display(),
        incoming: pending.incoming.display(),
        take: local.same(&pending.original),
    })
}

pub async fn list(db: &SqlitePool) -> Result<Vec<Group>, sqlx::Error> {
    let mut tx = db.begin().await?;
    let rows = pending(&mut tx).await?;
    let ids: Vec<_> = rows.iter().map(|r| r.mentor_id).collect();
    let profiles = mentor::export::profiles(&mut tx, &ids).await?;
    let mut groups = BTreeMap::<Field, Vec<Update>>::new();
    for row in rows {
        if let Some(update) = visible(&row, &profiles[&row.mentor_id]) {
            groups.entry(row.field).or_default().push(update);
        }
    }
    Ok(groups
        .into_iter()
        .map(|(field, differences)| Group { field, differences })
        .collect())
}

fn take(profile: &mut Profile, field: Field, unit: Option<&Affiliation>, value: &Value) {
    match (field, value) {
        (Field::Name, Value::Text(Some(name))) => {
            profile.fields.aliases.retain(|a| !same(a, name));
            append(
                &mut profile.fields.aliases,
                std::slice::from_ref(&profile.fields.name),
            );
            profile.fields.name.clone_from(name);
        }
        (Field::Research, Value::Text(value)) => profile.fields.research.clone_from(value),
        (Field::SourceUrl, Value::Text(value)) => profile.source_url.clone_from(value),
        (Field::Title, Value::Text(value)) => {
            if let Some(a) = profile
                .fields
                .affiliations
                .iter_mut()
                .find(|a| unit.is_some_and(|u| same_unit(a, u)))
            {
                a.title.clone_from(value);
            }
        }
        (Field::PrimaryEmail, Value::Text(Some(email))) => {
            profile
                .fields
                .emails
                .retain(|e| !e.eq_ignore_ascii_case(email));
            profile.fields.emails.insert(0, email.clone());
        }
        (Field::PrimaryAffiliation, Value::Affiliation(incoming)) => {
            let a = profile
                .fields
                .affiliations
                .iter()
                .position(|a| same_unit(a, incoming))
                .map(|n| profile.fields.affiliations.remove(n))
                .unwrap_or_else(|| incoming.clone());
            profile.fields.affiliations.insert(0, a);
        }
        _ => unreachable!("pending values are created by the corresponding field"),
    }
}

pub async fn plan(conn: &mut SqliteConnection, choices: &Choices) -> Result<Plan, Error> {
    let rows = pending(conn).await?;
    let ids: Vec<_> = rows.iter().map(|r| r.mentor_id).collect();
    let before = mentor::export::profiles(conn, &ids).await?;
    let mut after = before.clone();
    let mut applied = Vec::new();
    for choice in &choices.choices {
        let row = rows
            .iter()
            .find(|r| r.id == choice.update_id)
            .ok_or_else(|| Error::Invalid("待处理更新已变化，请刷新后重新选择。".into()))?;
        let Some(mut displayed) = visible(row, &before[&row.mentor_id]) else {
            applied.push(row.id);
            continue;
        };
        displayed.take = choice.take;
        if displayed != *choice {
            return Err(Error::Invalid(
                "导师资料或待处理更新已变化，请刷新后重新选择。".into(),
            ));
        }
        if choice.take {
            take(
                after
                    .get_mut(&row.mentor_id)
                    .unwrap_or_else(|| unreachable!("loaded above")),
                row.field,
                row.unit.as_ref(),
                &row.incoming,
            );
        }
        applied.push(row.id);
    }
    let changes: Vec<_> = after
        .into_iter()
        .filter_map(|(id, profile)| {
            (profile != before[&id]).then(|| intake::Change::edit(before[&id].clone(), profile))
        })
        .collect();
    Ok(Plan {
        mentors: changes.len() as u32,
        changes,
        ids: applied,
    })
}

pub async fn apply(db: &SqlitePool, choices: &Choices, now: i64) -> Result<u32, Error> {
    let mut tx = db.begin_with("BEGIN IMMEDIATE").await?;
    let plan = plan(&mut tx, choices).await?;
    intake::apply(&mut tx, plan.changes, now).await?;
    sqlx::query!(
        "DELETE FROM library_update WHERE id IN (SELECT value FROM json_each(?))",
        Json(plan.ids)
    )
    .execute(&mut *tx)
    .await?;
    tx.commit().await?;
    Ok(plan.mentors)
}

pub async fn count(db: &SqlitePool) -> Result<i64, sqlx::Error> {
    let ids: std::collections::BTreeSet<_> = list(db)
        .await?
        .into_iter()
        .flat_map(|g| g.differences)
        .map(|d| d.id)
        .collect();
    Ok(ids.len() as i64)
}
