use std::collections::BTreeMap;

use mentor::{
    Affiliation,
    export::Profile,
    intake,
    supplement::{affiliation_position, append, same},
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

pub(crate) fn same_text(a: Option<&str>, b: Option<&str>) -> bool {
    match (a, b) {
        (Some(a), Some(b)) => same(a, b),
        (None, None) => true,
        _ => false,
    }
}

impl Value {
    fn is_current(&self, local: &Self, affiliations: &[Affiliation]) -> bool {
        match (self, local) {
            (Self::Text(a), Self::Text(b)) => same_text(a.as_deref(), b.as_deref()),
            (Self::Affiliation(unit), Self::Affiliation(_)) => {
                affiliation_position(affiliations, unit) == Some(0)
            }
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

async fn pending(
    conn: &mut SqliteConnection,
    ids: Option<&[i64]>,
) -> Result<Vec<Pending>, sqlx::Error> {
    let rows = sqlx::query!(
        r#"SELECT u.id, l.mentor_id, u.field AS "field: Field", u.university, u.school,
            u.department, u.original AS "original: Json<Value>", u.incoming AS "incoming: Json<Value>"
        FROM library_update u JOIN mentor_library l ON l.library_id = u.library_id
        WHERE l.mentor_id IN (SELECT id FROM mentor WHERE trashed_at IS NULL)
            AND (?1 IS NULL OR u.id IN (SELECT value FROM json_each(?1)))
        ORDER BY u.field, l.mentor_id, u.id"#,
        ids.map(Json)
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

struct Current {
    id: i64,
    name: String,
    research: Option<String>,
    source_url: Option<String>,
    email: Option<String>,
    affiliations: Vec<Affiliation>,
}

async fn current_profiles(
    conn: &mut SqliteConnection,
    ids: &[i64],
) -> Result<BTreeMap<i64, Current>, sqlx::Error> {
    let mut profiles: BTreeMap<_, _> = sqlx::query!(
        r#"SELECT m.id, m.name, m.source_url, d.research,
            (SELECT address FROM mentor_email WHERE mentor_id = m.id
                ORDER BY position LIMIT 1) AS "email?: String"
        FROM mentor m JOIN mentor_detail d ON d.mentor_id = m.id
        WHERE m.id IN (SELECT value FROM json_each(?))"#,
        Json(ids)
    )
    .fetch_all(&mut *conn)
    .await?
    .into_iter()
    .map(|r| {
        (
            r.id,
            Current {
                id: r.id,
                name: r.name,
                research: r.research,
                source_url: r.source_url,
                email: r.email,
                affiliations: vec![],
            },
        )
    })
    .collect();
    for row in sqlx::query!(
        r#"SELECT mentor_id, university, school, department, title
        FROM mentor_affiliation WHERE mentor_id IN (SELECT value FROM json_each(?))
        ORDER BY mentor_id, position"#,
        Json(ids)
    )
    .fetch_all(&mut *conn)
    .await?
    {
        profiles
            .get_mut(&row.mentor_id)
            .unwrap_or_else(|| unreachable!("selected mentor"))
            .affiliations
            .push(Affiliation {
                university: row.university,
                school: row.school,
                department: row.department,
                title: row.title,
            });
    }
    Ok(profiles)
}

fn visible(pending: &Pending, profile: &Current) -> Option<Update> {
    let local = match pending.field {
        Field::Name => Value::Text(Some(profile.name.clone())),
        Field::Research => Value::Text(profile.research.clone()),
        Field::SourceUrl => Value::Text(profile.source_url.clone()),
        Field::PrimaryEmail => Value::Text(profile.email.clone()),
        Field::PrimaryAffiliation => profile
            .affiliations
            .first()
            .cloned()
            .map(Value::Affiliation)
            .unwrap_or(Value::Text(None)),
        Field::Title => {
            let position = affiliation_position(&profile.affiliations, pending.unit.as_ref()?)?;
            Value::Text(profile.affiliations[position].title.clone())
        }
    };
    if pending.incoming.is_current(&local, &profile.affiliations) {
        return None;
    }
    Some(Update {
        update_id: pending.id,
        id: profile.id,
        name: profile.name.clone(),
        field: pending.field,
        affiliation: pending.unit.clone(),
        local: local.display(),
        incoming: pending.incoming.display(),
        take: pending.original.is_current(&local, &profile.affiliations),
    })
}

pub async fn list(db: &SqlitePool) -> Result<Vec<Group>, sqlx::Error> {
    let mut tx = db.begin().await?;
    let rows = pending(&mut tx, None).await?;
    let ids: Vec<_> = rows.iter().map(|r| r.mentor_id).collect();
    let profiles = current_profiles(&mut tx, &ids).await?;
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
            if let Some(position) =
                unit.and_then(|unit| affiliation_position(&profile.fields.affiliations, unit))
            {
                profile.fields.affiliations[position]
                    .title
                    .clone_from(value);
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
            let a = affiliation_position(&profile.fields.affiliations, incoming)
                .map(|n| profile.fields.affiliations.remove(n))
                .unwrap_or_else(|| incoming.clone());
            profile.fields.affiliations.insert(0, a);
        }
        _ => unreachable!("pending values are created by the corresponding field"),
    }
}

pub async fn plan(conn: &mut SqliteConnection, choices: &Choices) -> Result<Plan, Error> {
    let selected: Vec<_> = choices.choices.iter().map(|c| c.update_id).collect();
    let rows = pending(conn, Some(&selected)).await?;
    let ids: Vec<_> = rows.iter().map(|r| r.mentor_id).collect();
    let current = current_profiles(conn, &ids).await?;
    let before = mentor::export::profiles(conn, &ids).await?;
    let mut after = before.clone();
    let mut applied = Vec::new();
    for choice in &choices.choices {
        let row = rows
            .iter()
            .find(|r| r.id == choice.update_id)
            .ok_or_else(|| Error::Invalid("待处理更新已变化，请刷新后重新选择。".into()))?;
        let Some(mut displayed) = visible(row, &current[&row.mentor_id]) else {
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
            if let (Field::PrimaryEmail, Value::Text(Some(address))) = (row.field, &row.incoming) {
                let owner = sqlx::query_scalar!(
                    r#"SELECT m.name FROM mentor_email e JOIN mentor m ON m.id = e.mentor_id
                    WHERE e.address = ? AND e.mentor_id <> ?"#,
                    address,
                    row.mentor_id
                )
                .fetch_optional(&mut *conn)
                .await?;
                if let Some(name) = owner {
                    return Err(Error::Invalid(format!(
                        "这个邮箱已经是导师 {name} 的，先合并这两位。"
                    )));
                }
            }
            let mut incoming = row.incoming.clone();
            if let (Field::PrimaryAffiliation, Value::Affiliation(unit)) = (row.field, &incoming)
                && affiliation_position(&after[&row.mentor_id].fields.affiliations, unit).is_none()
            {
                let record = sqlx::query_scalar!(
                    r#"SELECT record AS "record: Json<cloud_api::library::Record>"
                    FROM mentor_library WHERE library_id =
                        (SELECT library_id FROM library_update WHERE id = ?)"#,
                    row.id
                )
                .fetch_one(&mut *conn)
                .await?;
                let latest = crate::profile(&record).map_err(Error::Invalid)?;
                let position = affiliation_position(&latest.fields.affiliations, unit)
                    .ok_or_else(|| Error::Invalid("待处理更新已变化，请刷新后重新选择。".into()))?;
                incoming = Value::Affiliation(latest.fields.affiliations[position].clone());
            }
            take(
                after
                    .get_mut(&row.mentor_id)
                    .unwrap_or_else(|| unreachable!("loaded above")),
                row.field,
                row.unit.as_ref(),
                &incoming,
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

pub(crate) async fn readded(
    conn: &mut SqliteConnection,
    library: &str,
    record: &cloud_api::library::Record,
) -> Result<(), sqlx::Error> {
    sqlx::query!(
        r#"DELETE FROM library_update WHERE library_id = ?
        AND field IN ('name', 'research', 'sourceUrl', 'title')"#,
        library
    )
    .execute(&mut *conn)
    .await?;
    let preferences = sqlx::query!(
        r#"SELECT id, field AS "field: Field", original AS "original: Json<Value>"
        FROM library_update WHERE library_id = ?
        AND field IN ('primaryEmail', 'primaryAffiliation')"#,
        library
    )
    .fetch_all(&mut *conn)
    .await?;
    let affiliations: Vec<_> = record
        .preview
        .units
        .iter()
        .map(crate::affiliation)
        .collect();
    for preference in preferences {
        let incoming = match preference.field {
            Field::PrimaryEmail => record.emails.first().cloned().map(|v| Value::Text(Some(v))),
            Field::PrimaryAffiliation => record
                .preview
                .units
                .first()
                .map(crate::affiliation)
                .map(Value::Affiliation),
            _ => unreachable!("only preference fields selected"),
        };
        if let Some(incoming) =
            incoming.filter(|v| !preference.original.is_current(v, &affiliations))
        {
            sqlx::query!(
                "UPDATE library_update SET incoming = ? WHERE id = ?",
                Json(incoming),
                preference.id
            )
            .execute(&mut *conn)
            .await?;
        } else {
            sqlx::query!("DELETE FROM library_update WHERE id = ?", preference.id)
                .execute(&mut *conn)
                .await?;
        }
    }
    Ok(())
}

pub async fn count(db: &SqlitePool) -> Result<i64, sqlx::Error> {
    let mut tx = db.begin().await?;
    let rows = pending(&mut tx, None).await?;
    let ids: Vec<_> = rows.iter().map(|r| r.mentor_id).collect();
    let profiles = current_profiles(&mut tx, &ids).await?;
    let ids: std::collections::BTreeSet<_> = rows
        .iter()
        .filter_map(|row| visible(row, &profiles[&row.mentor_id]))
        .map(|update| update.id)
        .collect();
    Ok(ids.len() as i64)
}
