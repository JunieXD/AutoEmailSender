use cloud_api::{Empty, library as wire, path};
use mentor::{
    export::Profile,
    supplement::{add_missing, comparable},
};
use reqwest::Method;
use sqlx::{SqliteConnection, SqlitePool, types::Json};
use tokio::sync::Mutex;

use crate::{
    Error,
    updates::{Field, Value, same_unit},
};

const DAY: i64 = 24 * 60 * 60 * 1000;

#[derive(Default)]
pub struct Synchronizer(Mutex<()>);

impl Synchronizer {
    pub async fn check(
        &self,
        client: &account::Client,
        db: &SqlitePool,
        now: i64,
        force: bool,
    ) -> Result<u32, Error> {
        let _guard = self.0.lock().await;
        let Some(account) = sqlx::query!(
            r#"SELECT email, library_revision, library_checked_at
            FROM account WHERE token IS NOT NULL"#
        )
        .fetch_optional(db)
        .await?
        else {
            return if force {
                Err(account::Error::SignedOut.into())
            } else {
                Ok(0)
            };
        };
        if !force && account.library_checked_at.is_some_and(|at| now - at < DAY) {
            return Ok(0);
        }
        let mut after = account.library_revision;
        let mut count = 0;
        loop {
            let path = format!("{}?after={after}", path::LIBRARY_CHANGES);
            let page: wire::Changes = client
                .authenticated(db, Method::GET, &path, None::<&Empty>)
                .await?;
            // A malformed page must not advance the cursor or keep the process looping forever.
            if page.revision < after
                || page.more && page.revision == after
                || page
                    .changes
                    .iter()
                    .any(|c| c.revision <= after || c.revision > page.revision)
            {
                return Err(Error::Invalid(
                    "服务器返回的更新进度不正确，请稍后重试或升级应用。".into(),
                ));
            }
            for change in &page.changes {
                let mut tx = db.begin_with("BEGIN IMMEDIATE").await?;
                check_account(&mut tx, &account.email).await?;
                process(&mut tx, change, now).await?;
                tx.commit().await?;
                count += 1;
            }
            let mut tx = db.begin_with("BEGIN IMMEDIATE").await?;
            check_account(&mut tx, &account.email).await?;
            sqlx::query!("UPDATE account SET library_revision = ?", page.revision)
                .execute(&mut *tx)
                .await?;
            tx.commit().await?;
            after = page.revision;
            if !page.more {
                break;
            }
        }
        let mut tx = db.begin_with("BEGIN IMMEDIATE").await?;
        check_account(&mut tx, &account.email).await?;
        sqlx::query!("UPDATE account SET library_checked_at = ?", now)
            .execute(&mut *tx)
            .await?;
        tx.commit().await?;
        tracing::info!(count, "synchronized mentor library");
        Ok(count)
    }
}

async fn check_account(conn: &mut SqliteConnection, email: &str) -> Result<(), Error> {
    let matches = sqlx::query_scalar!(
        r#"SELECT EXISTS (SELECT 1 FROM account
            WHERE email = ? AND token IS NOT NULL) AS "matches!: bool""#,
        email
    )
    .fetch_one(conn)
    .await?;
    if !matches {
        return Err(Error::Invalid(
            "登录账号已变化，请重新检查导师库更新。".into(),
        ));
    }
    Ok(())
}

pub async fn process(
    conn: &mut SqliteConnection,
    change: &wire::Change,
    now: i64,
) -> Result<(), Error> {
    let Some(row) = sqlx::query!(
        r#"SELECT mentor_id, record AS "record: Json<wire::Record>"
        FROM mentor_library WHERE library_id = ?"#,
        change.id
    )
    .fetch_optional(&mut *conn)
    .await?
    else {
        return Ok(());
    };
    if let Some(record) = change.data.record() {
        if record.preview.summary.id != change.id || record.revision != change.revision {
            return Err(Error::Invalid(
                "服务器返回的更新记录不正确，请稍后重试或升级应用。".into(),
            ));
        }
        if record.revision < row.record.revision {
            return Ok(());
        }
        let before = mentor::export::profiles(conn, &[row.mentor_id])
            .await?
            .remove(&row.mentor_id)
            .unwrap_or_else(|| unreachable!("foreign key retains the mentor"));
        let old = crate::profile(&row.record).map_err(Error::Invalid)?;
        let new = crate::profile(record).map_err(Error::Invalid)?;
        let mut after = before.clone();
        let delta = additions(&old, &new);
        add_missing(
            &mut after.fields,
            &mut after.papers,
            &delta.fields,
            &delta.papers,
        );
        for (field, a, b) in [
            (
                Field::Name,
                Some(old.fields.name),
                Some(new.fields.name.clone()),
            ),
            (
                Field::Research,
                old.fields.research,
                new.fields.research.clone(),
            ),
            (Field::SourceUrl, old.source_url, new.source_url.clone()),
        ] {
            if a == b {
                continue;
            }
            let mut local = match field {
                Field::Name => Some(after.fields.name.clone()),
                Field::Research => after.fields.research.clone(),
                Field::SourceUrl => after.source_url.clone(),
                _ => unreachable!("scalar fields above"),
            };
            if local.is_none() {
                match field {
                    Field::Research => after.fields.research.clone_from(&b),
                    Field::SourceUrl => after.source_url.clone_from(&b),
                    _ => unreachable!("names cannot be empty"),
                }
                local.clone_from(&b);
            }
            pending(
                conn,
                &change.id,
                field,
                None,
                Value::Text(a),
                Value::Text(b),
                Value::Text(local),
            )
            .await?;
        }
        for unit in &new.fields.affiliations {
            if let Some(old_unit) = old.fields.affiliations.iter().find(|a| same_unit(a, unit))
                && old_unit.title != unit.title
                && let Some(local) = after
                    .fields
                    .affiliations
                    .iter_mut()
                    .find(|a| same_unit(a, unit))
            {
                if local.title.is_none() {
                    local.title.clone_from(&unit.title);
                }
                pending(
                    conn,
                    &change.id,
                    Field::Title,
                    Some(unit),
                    Value::Text(old_unit.title.clone()),
                    Value::Text(unit.title.clone()),
                    Value::Text(local.title.clone()),
                )
                .await?;
            }
        }
        if old.fields.emails.first() != new.fields.emails.first()
            && let Some(email) = new.fields.emails.first()
        {
            if after.fields.emails.is_empty() {
                after.fields.emails.push(email.clone());
            }
            pending(
                conn,
                &change.id,
                Field::PrimaryEmail,
                None,
                Value::Text(old.fields.emails.first().cloned()),
                Value::Text(Some(email.clone())),
                Value::Text(after.fields.emails.first().cloned()),
            )
            .await?;
        }
        if old.fields.affiliations.first() != new.fields.affiliations.first()
            && let Some(unit) = new.fields.affiliations.first()
            && !old
                .fields
                .affiliations
                .first()
                .is_some_and(|a| same_unit(a, unit))
        {
            if after.fields.affiliations.is_empty() {
                after.fields.affiliations.push(unit.clone());
            }
            let original = old
                .fields
                .affiliations
                .first()
                .cloned()
                .map(Value::Affiliation)
                .unwrap_or(Value::Text(None));
            let local = after
                .fields
                .affiliations
                .first()
                .cloned()
                .map(Value::Affiliation)
                .unwrap_or(Value::Text(None));
            pending(
                conn,
                &change.id,
                Field::PrimaryAffiliation,
                None,
                original,
                Value::Affiliation(unit.clone()),
                local,
            )
            .await?;
        }
        mentor::intake::apply(conn, vec![mentor::intake::Change::edit(before, after)], now).await?;
        sqlx::query!(
            "UPDATE mentor_library SET status = ?, record = ? WHERE library_id = ?",
            status(change.data.status()),
            Json(record),
            change.id
        )
        .execute(&mut *conn)
        .await?;
    } else {
        sqlx::query!(
            "UPDATE mentor_library SET status = 'removed' WHERE library_id = ?",
            change.id
        )
        .execute(&mut *conn)
        .await?;
        sqlx::query!("DELETE FROM library_update WHERE library_id = ?", change.id)
            .execute(conn)
            .await?;
    }
    Ok(())
}

fn status(status: wire::Status) -> &'static str {
    match status {
        wire::Status::Active => "active",
        wire::Status::Retired => "retired",
        wire::Status::Departed => "departed",
        wire::Status::Deceased => "deceased",
        wire::Status::Removed => "removed",
    }
}

fn additions(old: &Profile, new: &Profile) -> Profile {
    let mut delta = new.clone();
    let difference = |a: &[String], b: &[String]| {
        b.iter()
            .filter(|b| !a.iter().any(|a| comparable(a) == comparable(b)))
            .cloned()
            .collect()
    };
    delta
        .fields
        .emails
        .retain(|b| !old.fields.emails.iter().any(|a| a.eq_ignore_ascii_case(b)));
    delta.fields.aliases = difference(&old.fields.aliases, &new.fields.aliases);
    delta.fields.homepages = difference(&old.fields.homepages, &new.fields.homepages);
    delta.papers = difference(&old.papers, &new.papers);
    delta
        .fields
        .affiliations
        .retain(|b| !old.fields.affiliations.iter().any(|a| same_unit(a, b)));
    delta
}

async fn pending(
    conn: &mut SqliteConnection,
    library: &str,
    field: Field,
    unit: Option<&mentor::Affiliation>,
    original: Value,
    incoming: Value,
    local: Value,
) -> Result<(), sqlx::Error> {
    let university = unit
        .and_then(|a| a.university.as_deref())
        .map(comparable)
        .unwrap_or_default();
    let school = unit
        .and_then(|a| a.school.as_deref())
        .map(comparable)
        .unwrap_or_default();
    let department = unit
        .and_then(|a| a.department.as_deref())
        .map(comparable)
        .unwrap_or_default();
    sqlx::query!(
        r#"INSERT INTO library_update (library_id, field, university, school, department, original, incoming)
        SELECT ?1, ?2, ?3, ?4, ?5, ?6, ?7
        WHERE ?8 OR EXISTS (SELECT 1 FROM library_update
            WHERE library_id = ?1 AND field = ?2 AND university = ?3
                AND school = ?4 AND department = ?5)
        ON CONFLICT (library_id, field, university, school, department)
        DO UPDATE SET incoming = excluded.incoming"#,
        library,
        field,
        university,
        school,
        department,
        Json(original),
        Json(&incoming),
        !local.same(&incoming)
    )
    .execute(conn)
    .await?;
    Ok(())
}
