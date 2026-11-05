use cloud_api::{Empty, library as wire, path};
use mentor::{
    export::Profile,
    supplement::{add_missing, affiliation_position, comparable},
};
use reqwest::Method;
use sqlx::{SqliteConnection, SqlitePool, types::Json};
use tokio::sync::{Mutex, MutexGuard};

use crate::{
    Error,
    updates::{Field, Value, same_text},
};

const DAY: i64 = 24 * 60 * 60 * 1000;

#[derive(Default)]
pub struct Synchronizer(Mutex<()>);

impl Synchronizer {
    pub async fn acquire(&self) -> MutexGuard<'_, ()> {
        self.0.lock().await
    }

    pub async fn add(
        &self,
        client: &account::Client,
        db: &SqlitePool,
        input: &crate::add::Selection,
        now: i64,
    ) -> Result<crate::add::Added, Error> {
        let _guard = self.acquire().await;
        let fetched = crate::add::fetch(client, db, &input.ids).await?;
        crate::add::write(db, input, &fetched, now).await
    }

    pub async fn check(
        &self,
        client: &account::Client,
        db: &SqlitePool,
        now: i64,
        force: bool,
    ) -> Result<u32, Error> {
        let _guard = self.acquire().await;
        let Some(account) = sqlx::query!(
            r#"SELECT email, token AS "token!", library_revision, library_checked_at, terms
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
        // Paused, not failing, until the terms are agreed to.
        if account::outdated(account.terms.as_deref()) {
            return if force {
                Err(account::Error::TermsOutdated.into())
            } else {
                Ok(0)
            };
        }
        if !force && account.library_checked_at.is_some_and(|at| now - at < DAY) {
            return Ok(0);
        }
        let result = changes(client, db, &account.email, account.library_revision, now).await;
        if let Err(error @ (Error::Account(_) | Error::Invalid(_))) = &result {
            // Kept for the session that failed, not one that replaced it or ended meanwhile, and
            // under the lock, so that a later success clears it.
            let message = error.message();
            sqlx::query!(
                r#"UPDATE account SET library_failed_at = coalesce(library_failed_at, ?),
                    library_error = ?
                WHERE email = ? AND token = ?"#,
                now,
                message,
                account.email,
                account.token
            )
            .execute(db)
            .await?;
        }
        result
    }
}

/// Takes the changes since `after`, and on reaching the end clears any failure.
async fn changes(
    client: &account::Client,
    db: &SqlitePool,
    email: &str,
    mut after: i64,
    now: i64,
) -> Result<u32, Error> {
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
            check_account(&mut tx, email).await?;
            process(&mut tx, change, now).await?;
            tx.commit().await?;
            count += 1;
        }
        let mut tx = db.begin_with("BEGIN IMMEDIATE").await?;
        check_account(&mut tx, email).await?;
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
    check_account(&mut tx, email).await?;
    sqlx::query!(
        "UPDATE account SET library_checked_at = ?, library_failed_at = NULL, library_error = NULL",
        now
    )
    .execute(&mut *tx)
    .await?;
    tx.commit().await?;
    tracing::info!(count, "synchronized mentor library");
    Ok(count)
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
        let mut delta = additions(&old, &new);
        let owned = sqlx::query_scalar!(
            r#"SELECT address FROM mentor_email
            WHERE mentor_id <> ? AND address IN (SELECT value FROM json_each(?))"#,
            row.mentor_id,
            Json(&new.fields.emails)
        )
        .fetch_all(&mut *conn)
        .await?;
        let approved = if before.fields.emails.is_empty()
            && (!delta.fields.emails.is_empty()
                || old.fields.emails.first() != new.fields.emails.first())
        {
            sqlx::query_scalar!(
                r#"SELECT EXISTS (SELECT 1 FROM email
                WHERE mentor_id = ? AND state = 'approved') AS "approved!: bool""#,
                row.mentor_id
            )
            .fetch_one(&mut *conn)
            .await?
        } else {
            false
        };
        let emails = std::mem::take(&mut delta.fields.emails);
        if !approved {
            for address in emails {
                if !owned.iter().any(|a| a.eq_ignore_ascii_case(&address))
                    && !after
                        .fields
                        .emails
                        .iter()
                        .any(|a| a.eq_ignore_ascii_case(&address))
                {
                    after.fields.emails.push(address);
                }
            }
        }
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
            let different = !same_text(local.as_deref(), b.as_deref());
            pending(
                conn,
                &change.id,
                field,
                None,
                Value::Text(a),
                Value::Text(b),
                different,
            )
            .await?;
        }
        for unit in &new.fields.affiliations {
            if let Some(old_position) = affiliation_position(&old.fields.affiliations, unit) {
                let old_unit = &old.fields.affiliations[old_position];
                move_title(conn, &change.id, old_unit, unit).await?;
                if old_unit.title != unit.title
                    && let Some(position) = affiliation_position(&after.fields.affiliations, unit)
                {
                    let local = &mut after.fields.affiliations[position];
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
                        !same_text(local.title.as_deref(), unit.title.as_deref()),
                    )
                    .await?;
                }
            }
        }
        if new.fields.emails.is_empty() {
            clear_preference(conn, &change.id, Field::PrimaryEmail).await?;
        } else if (approved || old.fields.emails.first() != new.fields.emails.first())
            && let Some(email) = new.fields.emails.first()
        {
            if after.fields.emails.is_empty()
                && !approved
                && !owned.iter().any(|a| a.eq_ignore_ascii_case(email))
            {
                after.fields.emails.push(email.clone());
            }
            pending(
                conn,
                &change.id,
                Field::PrimaryEmail,
                None,
                Value::Text(old.fields.emails.first().cloned()),
                Value::Text(Some(email.clone())),
                !same_text(after.fields.emails.first().map(String::as_str), Some(email)),
            )
            .await?;
        }
        if new.fields.affiliations.is_empty() {
            clear_preference(conn, &change.id, Field::PrimaryAffiliation).await?;
        } else if let Some(unit) = new.fields.affiliations.first()
            && affiliation_position(&old.fields.affiliations, unit) != Some(0)
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
            pending(
                conn,
                &change.id,
                Field::PrimaryAffiliation,
                None,
                original,
                Value::Affiliation(unit.clone()),
                affiliation_position(&after.fields.affiliations, unit) != Some(0),
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
    delta.fields.affiliations.retain(|b| {
        affiliation_position(&old.fields.affiliations, b).is_none_or(|position| {
            let mut a = old.fields.affiliations[position].clone();
            let mut b = b.clone();
            a.title = None;
            b.title = None;
            a != b
        })
    });
    delta
}

async fn pending(
    conn: &mut SqliteConnection,
    library: &str,
    field: Field,
    unit: Option<&mentor::Affiliation>,
    original: Value,
    incoming: Value,
    different: bool,
) -> Result<(), sqlx::Error> {
    let [university, school, department] = unit_key(unit);
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
        different
    )
    .execute(conn)
    .await?;
    Ok(())
}

fn unit_key(unit: Option<&mentor::Affiliation>) -> [String; 3] {
    [
        unit.and_then(|a| a.university.as_deref()),
        unit.and_then(|a| a.school.as_deref()),
        unit.and_then(|a| a.department.as_deref()),
    ]
    .map(|value| value.map(comparable).unwrap_or_default())
}

async fn move_title(
    conn: &mut SqliteConnection,
    library: &str,
    old: &mentor::Affiliation,
    new: &mentor::Affiliation,
) -> Result<(), sqlx::Error> {
    let old = unit_key(Some(old));
    let new = unit_key(Some(new));
    if old != new {
        let [university, school, department] = new;
        let [old_university, old_school, old_department] = old;
        sqlx::query!(
            r#"UPDATE library_update SET university = ?, school = ?, department = ?
            WHERE library_id = ? AND field = 'title'
                AND university = ? AND school = ? AND department = ?"#,
            university,
            school,
            department,
            library,
            old_university,
            old_school,
            old_department
        )
        .execute(conn)
        .await?;
    }
    Ok(())
}

async fn clear_preference(
    conn: &mut SqliteConnection,
    library: &str,
    field: Field,
) -> Result<(), sqlx::Error> {
    sqlx::query!(
        "DELETE FROM library_update WHERE library_id = ? AND field = ?",
        library,
        field
    )
    .execute(conn)
    .await?;
    Ok(())
}
