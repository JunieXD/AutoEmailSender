#![cfg(test)]

use cloud_api::library as wire;
use library::{add, sync, updates};
use mentor::MentorFields;
use serde::Serialize;
use sqlx::{SqlitePool, types::Json};
use test_fixtures::provider::{Provider, Response};

fn record(id: &str, name: &str, email: &str) -> wire::Record {
    let unit = wire::Unit {
        university: Some("示例大学".into()),
        title: Some("副教授".into()),
        ..Default::default()
    };
    wire::Record {
        preview: wire::Preview {
            summary: wire::Summary {
                id: id.into(),
                name: name.into(),
                aliases: vec![],
                unit: unit.clone(),
                research: None,
                email_count: 1,
                fetched: true,
            },
            units: vec![unit],
            papers: vec![],
            homepages: vec![],
            source_url: None,
            verified_at: Some(100),
        },
        emails: vec![email.into()],
        revision: 1,
    }
}

async fn setup(db: &SqlitePool) -> (account::Client, Provider) {
    sqlx::query(
        "INSERT INTO account(id,email,token) VALUES(1,'student@example.invalid','test-token')",
    )
    .execute(db)
    .await
    .unwrap();
    let server = Provider::start().await.unwrap();
    let client = account::Client::with_address(
        &net::Net::new(vec![]).unwrap(),
        server.address.strip_suffix("/v1").unwrap().into(),
    );
    (client, server)
}

async fn respond(server: &mut Provider, path: &str, body: &impl Serialize) {
    let request = server.requests.recv().await.unwrap();
    assert_eq!(request.authorization, "Bearer test-token");
    assert!(request.line.contains(path), "{}", request.line);
    request
        .respond
        .send(Response::Raw {
            status: 200,
            content: serde_json::to_string(body).unwrap(),
        })
        .unwrap();
    request.completed.await.unwrap();
}

async fn adding(
    client: &account::Client,
    server: &mut Provider,
    db: &SqlitePool,
    records: Vec<wire::Record>,
    missing: Vec<String>,
) -> add::Added {
    let input = add::Selection {
        ids: records
            .iter()
            .map(|r| r.preview.summary.id.clone())
            .chain(missing.iter().cloned())
            .collect(),
        decisions: None,
    };
    let body = wire::Fetched {
        records,
        missing,
        remaining: Some(199),
    };
    let library = sync::Synchronizer::default();
    let (result, ()) = tokio::join!(
        library.add(client, db, &input, 100),
        respond(server, "/v1/library/fetch", &body)
    );
    result.unwrap()
}

async fn change(db: &SqlitePool, mut record: wire::Record, revision: i64) {
    record.revision = revision;
    let change = wire::Change {
        id: record.preview.summary.id.clone(),
        revision,
        data: wire::ChangeData::Active(record),
    };
    let mut tx = db.begin_with("BEGIN IMMEDIATE").await.unwrap();
    sync::process(&mut tx, &change, 200).await.unwrap();
    tx.commit().await.unwrap();
}

async fn mentor_id(db: &SqlitePool, library: &str) -> i64 {
    sqlx::query_scalar("SELECT mentor_id FROM mentor_library WHERE library_id=?")
        .bind(library)
        .fetch_one(db)
        .await
        .unwrap()
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn adding_matches_emails_skips_trash_and_can_be_repeated(db: SqlitePool) {
    let (client, mut server) = setup(&db).await;
    let existing = mentor::create(
        &db,
        &MentorFields {
            name: "已有导师".into(),
            emails: vec!["same@demo.edu.example".into()],
            ..Default::default()
        },
        0,
    )
    .await
    .unwrap();
    let trashed = mentor::create(
        &db,
        &MentorFields {
            name: "回收站导师".into(),
            emails: vec!["trash@demo.edu.example".into()],
            ..Default::default()
        },
        0,
    )
    .await
    .unwrap();
    mentor::trash::trash(&db, &[trashed], 1).await.unwrap();
    let rows = vec![
        record("mentor_new", "新增导师", "new@demo.edu.example"),
        record("mentor_existing", "已有导师", "same@demo.edu.example"),
        record("mentor_trash", "回收站导师", "trash@demo.edu.example"),
    ];
    let result = adding(
        &client,
        &mut server,
        &db,
        rows.clone(),
        vec!["mentor_missing".into()],
    )
    .await;
    assert_eq!(
        (result.created, result.supplemented, result.missing),
        (1, 1, 1)
    );
    assert_eq!(mentor_id(&db, "mentor_existing").await, existing);
    let count: i64 = sqlx::query_scalar("SELECT count(*) FROM mentor_library")
        .fetch_one(&db)
        .await
        .unwrap();
    assert_eq!(count, 2);
    assert_eq!(
        mentor::get(&db, existing)
            .await
            .unwrap()
            .unwrap()
            .affiliations
            .len(),
        1
    );
    let again = adding(&client, &mut server, &db, rows, vec![]).await;
    assert_eq!((again.created, again.supplemented), (0, 0));
    let detail = mentor::get(&db, mentor_id(&db, "mentor_new").await)
        .await
        .unwrap()
        .unwrap();
    assert_eq!(detail.library[0].status, wire::Status::Active);
    assert_eq!(detail.library[0].verified_at, Some(100));
    let input = add::Selection {
        ids: vec!["mentor_trash".into()],
        decisions: Some(mentor::import::Decisions {
            restore: vec![trashed],
            ..Default::default()
        }),
    };
    let body = wire::Fetched {
        records: vec![record(
            "mentor_trash",
            "回收站导师",
            "trash@demo.edu.example",
        )],
        missing: vec![],
        remaining: Some(199),
    };
    let library = sync::Synchronizer::default();
    let (result, ()) = tokio::join!(
        library.add(&client, &db, &input, 200),
        respond(&mut server, "/v1/library/fetch", &body)
    );
    assert_eq!(result.unwrap().supplemented, 1);
    assert_eq!(mentor_id(&db, "mentor_trash").await, trashed);
    assert!(!mentor::get(&db, trashed).await.unwrap().unwrap().trashed);
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn local_marks_use_associations_and_names_with_the_same_school(db: SqlitePool) {
    let (client, mut server) = setup(&db).await;
    let added = record("mentor_added", "已添加", "added@demo.edu.example");
    let trashed = record("mentor_trashed", "回收站", "trashed@demo.edu.example");
    adding(
        &client,
        &mut server,
        &db,
        vec![added.clone(), trashed.clone()],
        vec![],
    )
    .await;
    mentor::trash::trash(&db, &[mentor_id(&db, "mentor_trashed").await], 1)
        .await
        .unwrap();
    let possible = mentor::create(
        &db,
        &MentorFields {
            name: "同名".into(),
            aliases: vec!["Ming".into()],
            affiliations: vec![mentor::Affiliation {
                university: Some("示例大学".into()),
                ..Default::default()
            }],
            ..Default::default()
        },
        0,
    )
    .await
    .unwrap();
    let mut same = record("mentor_possible", "Ming", "other@demo.edu.example");
    same.preview.summary.aliases.push("另一个别名".into());
    let mut elsewhere = record("mentor_elsewhere", "同名", "elsewhere@demo.edu.example");
    elsewhere.preview.summary.unit.university = Some("另一所大学".into());
    let body = wire::List {
        total: 4,
        mentors: vec![
            added.preview.summary,
            trashed.preview.summary,
            same.preview.summary,
            elsewhere.preview.summary,
        ],
    };
    let input = wire::ListRequest {
        page: 1,
        org: None,
        q: Some("同 名".into()),
    };
    let (list, ()) = tokio::join!(
        library::list(&client, &db, &input),
        respond(&mut server, "q=%E5%90%8C+%E5%90%8D", &body)
    );
    let list = list.unwrap();
    assert!(matches!(list.mentors[0].local, Some(library::Local::Added)));
    assert!(matches!(
        list.mentors[1].local,
        Some(library::Local::Trashed)
    ));
    assert!(
        matches!(list.mentors[2].local, Some(library::Local::MaybeExisting {id, ..}) if id==possible)
    );
    assert!(list.mentors[3].local.is_none());
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn sync_only_changes_library_deltas_and_preserves_removed_list_items(db: SqlitePool) {
    let (client, mut server) = setup(&db).await;
    let mut row = record("mentor_one", "测试导师", "personal@example.com");
    row.preview.homepages = vec!["https://example.com/old".into()];
    adding(&client, &mut server, &db, vec![row.clone()], vec![]).await;
    let id = mentor_id(&db, "mentor_one").await;
    sqlx::query("UPDATE mentor_affiliation SET title='本机职称' WHERE mentor_id=?")
        .bind(id)
        .execute(&db)
        .await
        .unwrap();
    row.emails.push("new@demo.edu.example".into());
    row.preview.homepages.clear();
    row.preview.summary.research = Some("新研究方向".into());
    change(&db, row.clone(), 2).await;
    let local = mentor::get(&db, id).await.unwrap().unwrap();
    assert_eq!(
        local.emails,
        ["personal@example.com", "new@demo.edu.example"]
    );
    assert_eq!(local.homepages, ["https://example.com/old"]);
    assert_eq!(local.research.as_deref(), Some("新研究方向"));
    assert_eq!(local.affiliations[0].title.as_deref(), Some("本机职称"));
    assert!(updates::list(&db).await.unwrap().is_empty());
    change(&db, row.clone(), 2).await;
    assert!(updates::list(&db).await.unwrap().is_empty());
    row.preview.units[0].title = Some("教授".into());
    change(&db, row, 3).await;
    let groups = updates::list(&db).await.unwrap();
    assert_eq!(groups.len(), 1);
    assert_eq!(groups[0].field, updates::Field::Title);
    assert!(!groups[0].differences[0].take);
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn pending_keeps_earliest_value_and_applies_names_and_email_order(db: SqlitePool) {
    let (client, mut server) = setup(&db).await;
    let mut row = record("mentor_one", "原名", "old@demo.edu.example");
    row.emails.push("second@demo.edu.example".into());
    adding(&client, &mut server, &db, vec![row.clone()], vec![]).await;
    let id = mentor_id(&db, "mentor_one").await;
    row.preview.summary.name = "中间名".into();
    row.emails.swap(0, 1);
    change(&db, row.clone(), 2).await;
    row.preview.summary.name = "新名".into();
    change(&db, row.clone(), 3).await;
    let groups = updates::list(&db).await.unwrap();
    let choices: Vec<_> = groups.into_iter().flat_map(|g| g.differences).collect();
    assert_eq!(choices.len(), 2);
    assert!(choices.iter().all(|c| c.take));
    let names = choices
        .iter()
        .find(|c| c.field == updates::Field::Name)
        .unwrap();
    assert_eq!((&*names.local, &*names.incoming), ("原名", "新名"));
    let original: Json<String> =
        sqlx::query_scalar("SELECT original FROM library_update WHERE field='name'")
            .fetch_one(&db)
            .await
            .unwrap();
    assert_eq!(original.0, "原名");
    assert_eq!(
        updates::apply(&db, &updates::Choices { choices }, 300)
            .await
            .unwrap(),
        1
    );
    let local = mentor::get(&db, id).await.unwrap().unwrap();
    assert_eq!(local.name, "新名");
    assert!(local.aliases.contains(&"原名".into()));
    assert_eq!(local.emails[0], "second@demo.edu.example");
    assert!(updates::list(&db).await.unwrap().is_empty());
    row.emails.swap(0, 1);
    change(&db, row, 4).await;
    let mut choice = updates::list(&db)
        .await
        .unwrap()
        .remove(0)
        .differences
        .remove(0);
    choice.take = false;
    updates::apply(
        &db,
        &updates::Choices {
            choices: vec![choice],
        },
        301,
    )
    .await
    .unwrap();
    assert!(updates::list(&db).await.unwrap().is_empty());
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn changed_local_default_is_preserved_and_equal_new_values_are_hidden(db: SqlitePool) {
    let (client, mut server) = setup(&db).await;
    let mut row = record("mentor_one", "测试导师", "old@demo.edu.example");
    row.emails.push("next@demo.edu.example".into());
    adding(&client, &mut server, &db, vec![row.clone()], vec![]).await;
    let id = mentor_id(&db, "mentor_one").await;
    mentor::add_email(&db, id, "personal@example.com", 1)
        .await
        .unwrap();
    sqlx::query("UPDATE mentor_email SET position=position+1 WHERE mentor_id=?")
        .bind(id)
        .execute(&db)
        .await
        .unwrap();
    sqlx::query("UPDATE mentor_email SET position=0 WHERE address='personal@example.com'")
        .execute(&db)
        .await
        .unwrap();
    row.emails.swap(0, 1);
    change(&db, row.clone(), 2).await;
    let groups = updates::list(&db).await.unwrap();
    assert!(!groups[0].differences[0].take);
    sqlx::query("UPDATE mentor_email SET position=-1 WHERE address='next@demo.edu.example'")
        .execute(&db)
        .await
        .unwrap();
    assert!(updates::list(&db).await.unwrap().is_empty());
    assert_eq!(updates::count(&db).await.unwrap(), 0);
    // A later change back to the local value must replace the pending new value too.
    row.emails[0] = "personal@example.com".into();
    change(&db, row, 3).await;
    assert_eq!(
        updates::list(&db).await.unwrap()[0].differences[0].incoming,
        "personal@example.com"
    );
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn trash_syncs_but_hides_updates_and_removal_clears_them(db: SqlitePool) {
    let (client, mut server) = setup(&db).await;
    let mut row = record("mentor_one", "原名", "one@demo.edu.example");
    adding(&client, &mut server, &db, vec![row.clone()], vec![]).await;
    let id = mentor_id(&db, "mentor_one").await;
    mentor::trash::trash(&db, &[id], 10).await.unwrap();
    row.preview.summary.name = "新名".into();
    row.preview.summary.research = Some("新方向".into());
    change(&db, row, 2).await;
    assert!(updates::list(&db).await.unwrap().is_empty());
    assert_eq!(updates::count(&db).await.unwrap(), 0);
    assert_eq!(
        mentor::get(&db, id)
            .await
            .unwrap()
            .unwrap()
            .research
            .as_deref(),
        Some("新方向")
    );
    mentor::trash::restore(&db, &[id], 11).await.unwrap();
    assert_eq!(updates::count(&db).await.unwrap(), 1);
    let removed = wire::Change {
        id: "mentor_one".into(),
        revision: 3,
        data: wire::ChangeData::Removed(()),
    };
    let mut tx = db.begin_with("BEGIN IMMEDIATE").await.unwrap();
    sync::process(&mut tx, &removed, 20).await.unwrap();
    tx.commit().await.unwrap();
    assert!(updates::list(&db).await.unwrap().is_empty());
    let detail = mentor::get(&db, id).await.unwrap().unwrap();
    assert_eq!(detail.name, "原名");
    assert_eq!(detail.library[0].status, wire::Status::Removed);
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn paginated_sync_advances_only_completed_pages_and_checks_once_a_day(db: SqlitePool) {
    let (client, mut server) = setup(&db).await;
    let mut row = record("mentor_one", "原名", "one@demo.edu.example");
    adding(&client, &mut server, &db, vec![row.clone()], vec![]).await;
    row.preview.summary.name = "新名".into();
    row.revision = 2;
    let pages = [
        wire::Changes {
            changes: vec![wire::Change {
                id: "mentor_one".into(),
                revision: 2,
                data: wire::ChangeData::Retired(row),
            }],
            revision: 2,
            more: true,
        },
        wire::Changes {
            changes: vec![wire::Change {
                id: "mentor_absent".into(),
                revision: 4,
                data: wire::ChangeData::Removed(()),
            }],
            revision: 4,
            more: false,
        },
    ];
    let sync = sync::Synchronizer::default();
    let calls = sync.check(&client, &db, 1000, false);
    let replies = async {
        respond(&mut server, "after=0", &pages[0]).await;
        let saved: (i64, Option<i64>) =
            sqlx::query_as("SELECT library_revision,library_checked_at FROM account")
                .fetch_one(&db)
                .await
                .unwrap();
        // The next request is emitted only after the previous page committed.
        let request = server.requests.recv().await.unwrap();
        assert!(request.line.contains("after=2"));
        let cursor: i64 = sqlx::query_scalar("SELECT library_revision FROM account")
            .fetch_one(&db)
            .await
            .unwrap();
        assert_eq!(cursor, 2);
        assert!(saved.1.is_none());
        request
            .respond
            .send(Response::Raw {
                status: 200,
                content: serde_json::to_string(&pages[1]).unwrap(),
            })
            .unwrap();
        request.completed.await.unwrap();
    };
    let (result, ()) = tokio::join!(calls, replies);
    assert_eq!(result.unwrap(), 2);
    let saved: (i64, Option<i64>) =
        sqlx::query_as("SELECT library_revision,library_checked_at FROM account")
            .fetch_one(&db)
            .await
            .unwrap();
    assert_eq!(saved, (4, Some(1000)));
    assert_eq!(sync.check(&client, &db, 1001, false).await.unwrap(), 0);
    let id = mentor_id(&db, "mentor_one").await;
    assert_eq!(
        mentor::get(&db, id).await.unwrap().unwrap().library[0].status,
        wire::Status::Retired
    );
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn merged_mentors_keep_library_records_and_pending_choices(db: SqlitePool) {
    let (client, mut server) = setup(&db).await;
    let first = record("mentor_first", "保留", "first@demo.edu.example");
    let mut second = record("mentor_second", "合并", "second@demo.edu.example");
    adding(
        &client,
        &mut server,
        &db,
        vec![first, second.clone()],
        vec![],
    )
    .await;
    let keep = mentor_id(&db, "mentor_first").await;
    let other = mentor_id(&db, "mentor_second").await;
    second.preview.units[0].title = Some("教授".into());
    change(&db, second, 2).await;
    mentor::merge::merge(&db, keep, other, 300).await.unwrap();
    assert_eq!(mentor_id(&db, "mentor_second").await, keep);
    let groups = updates::list(&db).await.unwrap();
    assert_eq!(groups[0].differences[0].id, keep);
    assert_eq!(
        mentor::get(&db, keep).await.unwrap().unwrap().library.len(),
        2
    );
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn interrupted_page_replays_without_duplicating_additions_or_pending_rows(db: SqlitePool) {
    let (client, mut server) = setup(&db).await;
    let mut row = record("mentor_one", "原名", "one@demo.edu.example");
    adding(&client, &mut server, &db, vec![row.clone()], vec![]).await;
    row.preview.summary.name = "新名".into();
    row.emails.push("two@demo.edu.example".into());
    row.revision = 2;
    // The record committed, but the page cursor did not, as after a process interruption.
    change(&db, row.clone(), 2).await;
    let page = wire::Changes {
        changes: vec![wire::Change {
            id: "mentor_one".into(),
            revision: 2,
            data: wire::ChangeData::Active(row),
        }],
        revision: 2,
        more: true,
    };
    let sync = sync::Synchronizer::default();
    let replies = async {
        respond(&mut server, "after=0", &page).await;
        let request = server.requests.recv().await.unwrap();
        assert!(request.line.contains("after=2"));
        request
            .respond
            .send(Response::Raw {
                status: 503,
                content: serde_json::to_string(&cloud_api::ApiError {
                    code: cloud_api::ErrorCode::Internal,
                    retry_after_seconds: None,
                })
                .unwrap(),
            })
            .unwrap();
        request.completed.await.unwrap();
    };
    let (result, ()) = tokio::join!(sync.check(&client, &db, 1000, true), replies);
    assert!(result.is_err());
    let progress: (i64, Option<i64>) =
        sqlx::query_as("SELECT library_revision, library_checked_at FROM account")
            .fetch_one(&db)
            .await
            .unwrap();
    assert_eq!(progress, (2, None));
    let count: i64 = sqlx::query_scalar("SELECT count(*) FROM library_update")
        .fetch_one(&db)
        .await
        .unwrap();
    assert_eq!(count, 1);
    let local = mentor::get(&db, mentor_id(&db, "mentor_one").await)
        .await
        .unwrap()
        .unwrap();
    assert_eq!(
        local.emails,
        ["one@demo.edu.example", "two@demo.edu.example"]
    );
    let empty = wire::Changes {
        changes: vec![],
        revision: 2,
        more: false,
    };
    let (result, ()) = tokio::join!(
        sync.check(&client, &db, 2000, true),
        respond(&mut server, "after=2", &empty)
    );
    assert_eq!(result.unwrap(), 0);
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn primary_unit_changes_are_selected_and_titles_for_removed_local_units_are_hidden(
    db: SqlitePool,
) {
    let (client, mut server) = setup(&db).await;
    let mut row = record("mentor_one", "导师", "one@demo.edu.example");
    row.preview.units.push(wire::Unit {
        university: Some("另一大学".into()),
        title: Some("讲师".into()),
        ..Default::default()
    });
    adding(&client, &mut server, &db, vec![row.clone()], vec![]).await;
    let id = mentor_id(&db, "mentor_one").await;
    row.preview.units.swap(0, 1);
    row.preview.units[1].title = Some("教授".into());
    change(&db, row.clone(), 2).await;
    let groups = updates::list(&db).await.unwrap();
    assert_eq!(groups.len(), 2);
    assert_eq!(updates::count(&db).await.unwrap(), 1);
    assert!(groups.iter().flat_map(|g| &g.differences).all(|d| d.take));
    let primary = groups
        .into_iter()
        .find(|g| g.field == updates::Field::PrimaryAffiliation)
        .unwrap()
        .differences;
    updates::apply(&db, &updates::Choices { choices: primary }, 300)
        .await
        .unwrap();
    assert_eq!(
        mentor::get(&db, id).await.unwrap().unwrap().affiliations[0]
            .university
            .as_deref(),
        Some("另一大学")
    );
    sqlx::query("DELETE FROM mentor_affiliation WHERE mentor_id=? AND university='示例大学'")
        .bind(id)
        .execute(&db)
        .await
        .unwrap();
    assert!(updates::list(&db).await.unwrap().is_empty());
    assert_eq!(updates::count(&db).await.unwrap(), 0);
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn latest_pending_value_matching_local_is_hidden_and_stale_choices_are_rejected(
    db: SqlitePool,
) {
    let (client, mut server) = setup(&db).await;
    let mut row = record("mentor_one", "原名", "one@demo.edu.example");
    adding(&client, &mut server, &db, vec![row.clone()], vec![]).await;
    row.preview.summary.name = "中间名".into();
    change(&db, row.clone(), 2).await;
    let old = updates::list(&db).await.unwrap().remove(0).differences;
    row.preview.summary.name = "新名".into();
    change(&db, row.clone(), 3).await;
    assert!(
        updates::apply(&db, &updates::Choices { choices: old }, 300)
            .await
            .is_err()
    );
    row.preview.summary.name = "原名".into();
    change(&db, row, 4).await;
    assert!(updates::list(&db).await.unwrap().is_empty());
    assert_eq!(updates::count(&db).await.unwrap(), 0);
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn quota_failure_is_reported_and_expired_sessions_stop_sync(db: SqlitePool) {
    let (client, mut server) = setup(&db).await;
    let selection = add::Selection {
        ids: vec!["mentor_one".into()],
        decisions: None,
    };
    let response = async {
        let request = server.requests.recv().await.unwrap();
        request
            .respond
            .send(Response::Raw {
                status: 403,
                content: serde_json::to_string(&cloud_api::ApiError {
                    code: cloud_api::ErrorCode::QuotaExceeded,
                    retry_after_seconds: None,
                })
                .unwrap(),
            })
            .unwrap();
        request.completed.await.unwrap();
    };
    let library = sync::Synchronizer::default();
    let (result, ()) = tokio::join!(library.add(&client, &db, &selection, 0), response);
    assert!(result.unwrap_err().message().contains("少选几位或明天再来"));
    let response = async {
        let request = server.requests.recv().await.unwrap();
        request
            .respond
            .send(Response::Raw {
                status: 401,
                content: serde_json::to_string(&cloud_api::ApiError {
                    code: cloud_api::ErrorCode::SessionExpired,
                    retry_after_seconds: None,
                })
                .unwrap(),
            })
            .unwrap();
        request.completed.await.unwrap();
    };
    let sync = sync::Synchronizer::default();
    let (result, ()) = tokio::join!(sync.check(&client, &db, 1000, true), response);
    assert!(matches!(
        result,
        Err(library::Error::Account(account::Error::SignedOut))
    ));
    let saved: (Option<String>, i64, Option<i64>) =
        sqlx::query_as("SELECT token,library_revision,library_checked_at FROM account")
            .fetch_one(&db)
            .await
            .unwrap();
    assert_eq!(saved, (None, 0, None));
    assert_eq!(sync.check(&client, &db, 2000, false).await.unwrap(), 0);
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn changed_primary_values_fill_empty_local_lists(db: SqlitePool) {
    let (client, mut server) = setup(&db).await;
    let mut row = record("mentor_one", "导师", "one@demo.edu.example");
    row.emails.push("two@demo.edu.example".into());
    row.preview.units.push(wire::Unit {
        university: Some("另一大学".into()),
        ..Default::default()
    });
    adding(&client, &mut server, &db, vec![row.clone()], vec![]).await;
    let id = mentor_id(&db, "mentor_one").await;
    let mut fields = mentor::export::profiles(&mut db.acquire().await.unwrap(), &[id])
        .await
        .unwrap()
        .remove(&id)
        .unwrap()
        .fields;
    fields.emails.clear();
    fields.affiliations.clear();
    mentor::update(&db, id, &fields, 100).await.unwrap();
    row.emails.swap(0, 1);
    row.preview.units.swap(0, 1);
    change(&db, row, 2).await;
    let local = mentor::get(&db, id).await.unwrap().unwrap();
    assert_eq!(local.emails, ["two@demo.edu.example"]);
    assert_eq!(
        local.affiliations[0].university.as_deref(),
        Some("另一大学")
    );
    assert!(updates::list(&db).await.unwrap().is_empty());
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn intake_rows_keep_selected_ids_when_fetched_order_changes_and_errors_include_names(
    db: SqlitePool,
) {
    let (client, mut server) = setup(&db).await;
    mentor::create(
        &db,
        &MentorFields {
            name: "可能已有".into(),
            affiliations: vec![mentor::Affiliation {
                university: Some("示例大学".into()),
                ..Default::default()
            }],
            ..Default::default()
        },
        0,
    )
    .await
    .unwrap();
    let bad = record("mentor_bad", "邮箱有问题的导师", "bad@");
    let good = record("mentor_good", "可能已有", "good@demo.edu.example");
    let mut input = add::Selection {
        ids: vec![
            "mentor_missing".into(),
            "mentor_bad".into(),
            "mentor_good".into(),
        ],
        decisions: None,
    };
    let mut fetched = wire::Fetched {
        records: vec![good, bad],
        missing: vec!["mentor_missing".into()],
        remaining: Some(198),
    };
    let (preview, ()) = tokio::join!(
        add::preview(&client, &db, &input),
        respond(&mut server, "/v1/library/fetch", &fetched)
    );
    let preview = preview.unwrap();
    assert_eq!(preview.intake.errors[0].row, 2);
    assert_eq!(preview.intake.errors[0].name, "邮箱有问题的导师");
    assert_eq!(preview.intake.maybe_same[0].row, 3);
    input.decisions = Some(mentor::import::Decisions {
        matches: vec![mentor::import::GroupChoice { row: 3, id: None }],
        ..Default::default()
    });
    fetched.records.reverse();
    let library = sync::Synchronizer::default();
    let (result, ()) = tokio::join!(
        library.add(&client, &db, &input, 200),
        respond(&mut server, "/v1/library/fetch", &fetched)
    );
    let result = result.unwrap();
    assert_eq!(
        (result.created, result.supplemented, result.missing),
        (1, 0, 1)
    );
    let count: i64 = sqlx::query_scalar("SELECT count(*) FROM mentor_library")
        .fetch_one(&db)
        .await
        .unwrap();
    assert_eq!(count, 1);
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn auto_filling_cleared_values_also_replaces_older_pending_values(db: SqlitePool) {
    let (client, mut server) = setup(&db).await;
    let mut row = record("mentor_one", "导师", "one@demo.edu.example");
    row.preview.summary.research = Some("原方向".into());
    adding(&client, &mut server, &db, vec![row.clone()], vec![]).await;
    let id = mentor_id(&db, "mentor_one").await;
    row.preview.summary.research = Some("中间方向".into());
    row.preview.units[0].title = Some("教授".into());
    change(&db, row.clone(), 2).await;
    let mut fields = mentor::export::profiles(&mut db.acquire().await.unwrap(), &[id])
        .await
        .unwrap()
        .remove(&id)
        .unwrap()
        .fields;
    fields.research = None;
    fields.affiliations[0].title = None;
    mentor::update(&db, id, &fields, 250).await.unwrap();
    row.preview.summary.research = Some("新方向".into());
    row.preview.units[0].title = Some("研究员".into());
    change(&db, row, 3).await;
    let local = mentor::get(&db, id).await.unwrap().unwrap();
    assert_eq!(local.research.as_deref(), Some("新方向"));
    assert_eq!(local.affiliations[0].title.as_deref(), Some("研究员"));
    assert!(updates::list(&db).await.unwrap().is_empty());
    assert_eq!(updates::count(&db).await.unwrap(), 0);
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn sync_appends_emails_without_changing_an_approved_recipient(db: SqlitePool) {
    let (client, mut server) = setup(&db).await;
    let mut row = record("mentor_one", "导师", "personal@example.com");
    adding(&client, &mut server, &db, vec![row.clone()], vec![]).await;
    let id = mentor_id(&db, "mentor_one").await;
    test_fixtures::mail::first_email(&db, id, "approved", 0)
        .await
        .unwrap();
    row.emails.push("new@demo.edu.example".into());
    change(&db, row.clone(), 2).await;
    assert_eq!(
        mentor::get(&db, id).await.unwrap().unwrap().emails,
        ["personal@example.com", "new@demo.edu.example"]
    );
    let mut fields = mentor::export::profiles(&mut db.acquire().await.unwrap(), &[id])
        .await
        .unwrap()
        .remove(&id)
        .unwrap()
        .fields;
    fields.emails.clear();
    mentor::update(&db, id, &fields, 10).await.unwrap();
    row.emails.push("third@demo.edu.example".into());
    change(&db, row, 3).await;
    assert!(
        mentor::get(&db, id)
            .await
            .unwrap()
            .unwrap()
            .emails
            .is_empty()
    );
    let groups = updates::list(&db).await.unwrap();
    assert_eq!(groups[0].field, updates::Field::PrimaryEmail);
    assert_eq!(groups[0].differences[0].incoming, "personal@example.com");
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn compatible_local_units_receive_title_updates_and_prefer_exact_matches(db: SqlitePool) {
    let (client, mut server) = setup(&db).await;
    let mut row = record("mentor_one", "导师", "one@demo.edu.example");
    adding(&client, &mut server, &db, vec![row.clone()], vec![]).await;
    let id = mentor_id(&db, "mentor_one").await;
    let mut fields = mentor::export::profiles(&mut db.acquire().await.unwrap(), &[id])
        .await
        .unwrap()
        .remove(&id)
        .unwrap()
        .fields;
    fields.affiliations[0].school = Some("本机补充的学院".into());
    mentor::update(&db, id, &fields, 10).await.unwrap();
    row.preview.units[0].title = Some("教授".into());
    change(&db, row.clone(), 2).await;
    let groups = updates::list(&db).await.unwrap();
    assert_eq!(groups.len(), 1);
    assert_eq!(updates::count(&db).await.unwrap(), 1);
    assert_eq!(groups[0].differences[0].local, "副教授");
    updates::apply(
        &db,
        &updates::Choices {
            choices: groups.into_iter().flat_map(|g| g.differences).collect(),
        },
        20,
    )
    .await
    .unwrap();
    let local = mentor::get(&db, id).await.unwrap().unwrap();
    assert_eq!(
        local.affiliations[0].school.as_deref(),
        Some("本机补充的学院")
    );
    assert_eq!(local.affiliations[0].title.as_deref(), Some("教授"));
    fields.affiliations[0].title = Some("教授".into());
    fields.affiliations.push(mentor::Affiliation {
        university: Some("示例大学".into()),
        title: Some("副教授".into()),
        ..Default::default()
    });
    mentor::update(&db, id, &fields, 30).await.unwrap();
    row.preview.units[0].title = Some("研究员".into());
    change(&db, row, 3).await;
    let mut choices: Vec<_> = updates::list(&db)
        .await
        .unwrap()
        .into_iter()
        .flat_map(|g| g.differences)
        .collect();
    assert_eq!(choices[0].local, "副教授");
    choices[0].take = true;
    updates::apply(&db, &updates::Choices { choices }, 40)
        .await
        .unwrap();
    let local = mentor::get(&db, id).await.unwrap().unwrap();
    assert_eq!(local.affiliations[0].title.as_deref(), Some("教授"));
    assert_eq!(local.affiliations[1].title.as_deref(), Some("研究员"));
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn readding_current_profiles_clears_scalar_pending_updates(db: SqlitePool) {
    let (client, mut server) = setup(&db).await;
    let mut row = record("mentor_one", "原名", "one@demo.edu.example");
    row.preview.summary.research = Some("原方向".into());
    row.preview.source_url = Some("https://example.com/old".into());
    adding(&client, &mut server, &db, vec![row.clone()], vec![]).await;
    row.preview.summary.name = "中间名".into();
    row.preview.summary.research = Some("中间方向".into());
    row.preview.source_url = Some("https://example.com/mid".into());
    row.preview.units[0].title = Some("教授".into());
    change(&db, row.clone(), 2).await;
    row.revision = 3;
    row.preview.summary.name = "新名".into();
    row.preview.summary.research = Some("新方向".into());
    row.preview.source_url = Some("https://example.com/new".into());
    row.preview.units[0].title = Some("研究员".into());
    let fetched = wire::Fetched {
        records: vec![row],
        missing: vec![],
        remaining: Some(199),
    };
    let mut input = add::Selection {
        ids: vec!["mentor_one".into()],
        decisions: None,
    };
    let preview = add::calculate(&mut db.acquire().await.unwrap(), &fetched, &input)
        .await
        .unwrap()
        .0;
    input.decisions = Some(mentor::import::Decisions {
        take: preview.intake.differences,
        ..Default::default()
    });
    let library = sync::Synchronizer::default();
    let (result, ()) = tokio::join!(
        library.add(&client, &db, &input, 30),
        respond(&mut server, "/v1/library/fetch", &fetched)
    );
    result.unwrap();
    assert!(updates::list(&db).await.unwrap().is_empty());
    let count: i64 = sqlx::query_scalar("SELECT count(*) FROM library_update")
        .fetch_one(&db)
        .await
        .unwrap();
    assert_eq!(count, 0);
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn readding_realigns_preferences_and_clears_empty_or_reverted_targets(db: SqlitePool) {
    let (client, mut server) = setup(&db).await;
    let mut original = record("mentor_one", "导师", "one@demo.edu.example");
    original.emails.push("two@demo.edu.example".into());
    original.preview.units.push(wire::Unit {
        university: Some("另一大学".into()),
        ..Default::default()
    });
    adding(&client, &mut server, &db, vec![original.clone()], vec![]).await;
    let mut changed = original.clone();
    changed.emails.swap(0, 1);
    changed.preview.units.swap(0, 1);
    change(&db, changed.clone(), 2).await;
    changed.revision = 3;
    changed.emails.insert(0, "latest@demo.edu.example".into());
    changed.preview.units.insert(
        0,
        wire::Unit {
            university: Some("最新大学".into()),
            ..Default::default()
        },
    );
    adding(&client, &mut server, &db, vec![changed], vec![]).await;
    let groups = updates::list(&db).await.unwrap();
    let email = groups
        .iter()
        .find(|g| g.field == updates::Field::PrimaryEmail)
        .unwrap();
    assert_eq!(email.differences[0].incoming, "latest@demo.edu.example");
    let unit = groups
        .iter()
        .find(|g| g.field == updates::Field::PrimaryAffiliation)
        .unwrap();
    assert_eq!(unit.differences[0].incoming, "最新大学");
    let earliest: Json<String> =
        sqlx::query_scalar("SELECT original FROM library_update WHERE field='primaryEmail'")
            .fetch_one(&db)
            .await
            .unwrap();
    assert_eq!(earliest.0, "one@demo.edu.example");
    original.revision = 4;
    adding(&client, &mut server, &db, vec![original.clone()], vec![]).await;
    assert!(updates::list(&db).await.unwrap().is_empty());
    original.emails.swap(0, 1);
    original.preview.units.swap(0, 1);
    change(&db, original.clone(), 5).await;
    original.revision = 6;
    original.emails.clear();
    original.preview.units.clear();
    adding(&client, &mut server, &db, vec![original], vec![]).await;
    let pending: i64 = sqlx::query_scalar("SELECT count(*) FROM library_update")
        .fetch_one(&db)
        .await
        .unwrap();
    assert_eq!(pending, 0);
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn empty_library_lists_clear_preferred_value_suggestions(db: SqlitePool) {
    let (client, mut server) = setup(&db).await;
    let mut row = record("mentor_one", "导师", "one@demo.edu.example");
    row.emails.push("two@demo.edu.example".into());
    row.preview.units.push(wire::Unit {
        university: Some("另一大学".into()),
        ..Default::default()
    });
    adding(&client, &mut server, &db, vec![row.clone()], vec![]).await;
    let id = mentor_id(&db, "mentor_one").await;
    row.emails.swap(0, 1);
    row.preview.units.swap(0, 1);
    change(&db, row.clone(), 2).await;
    assert_eq!(updates::list(&db).await.unwrap().len(), 2);
    row.emails.clear();
    row.preview.units.clear();
    change(&db, row, 3).await;
    assert!(updates::list(&db).await.unwrap().is_empty());
    let local = mentor::get(&db, id).await.unwrap().unwrap();
    assert_eq!(local.emails.len(), 2);
    assert_eq!(local.affiliations.len(), 2);
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn restoring_a_primary_unit_uses_its_latest_library_title(db: SqlitePool) {
    let (client, mut server) = setup(&db).await;
    let mut row = record("mentor_one", "导师", "one@demo.edu.example");
    row.preview.units.push(wire::Unit {
        university: Some("另一大学".into()),
        title: Some("讲师".into()),
        ..Default::default()
    });
    adding(&client, &mut server, &db, vec![row.clone()], vec![]).await;
    let id = mentor_id(&db, "mentor_one").await;
    row.preview.units.swap(0, 1);
    change(&db, row.clone(), 2).await;
    row.preview.units[0].title = Some("教授".into());
    change(&db, row, 3).await;
    let mut fields = mentor::export::profiles(&mut db.acquire().await.unwrap(), &[id])
        .await
        .unwrap()
        .remove(&id)
        .unwrap()
        .fields;
    fields
        .affiliations
        .retain(|a| a.university.as_deref() != Some("另一大学"));
    mentor::update(&db, id, &fields, 10).await.unwrap();
    let choices = updates::list(&db)
        .await
        .unwrap()
        .into_iter()
        .flat_map(|g| g.differences)
        .collect();
    updates::apply(&db, &updates::Choices { choices }, 20)
        .await
        .unwrap();
    let local = mentor::get(&db, id).await.unwrap().unwrap();
    assert_eq!(
        local.affiliations[0].university.as_deref(),
        Some("另一大学")
    );
    assert_eq!(local.affiliations[0].title.as_deref(), Some("教授"));
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn restoring_a_primary_unit_missing_from_the_latest_record_is_rejected(db: SqlitePool) {
    let (client, mut server) = setup(&db).await;
    let mut row = record("mentor_one", "导师", "one@demo.edu.example");
    row.preview.units.push(wire::Unit {
        university: Some("另一大学".into()),
        ..Default::default()
    });
    adding(&client, &mut server, &db, vec![row.clone()], vec![]).await;
    let id = mentor_id(&db, "mentor_one").await;
    row.preview.units.swap(0, 1);
    change(&db, row.clone(), 2).await;
    let mut fields = mentor::export::profiles(&mut db.acquire().await.unwrap(), &[id])
        .await
        .unwrap()
        .remove(&id)
        .unwrap()
        .fields;
    fields
        .affiliations
        .retain(|a| a.university.as_deref() != Some("另一大学"));
    mentor::update(&db, id, &fields, 30).await.unwrap();
    row.preview.units.clear();
    row.preview.summary.unit = Default::default();
    sqlx::query("UPDATE mentor_library SET record=?")
        .bind(Json(row))
        .execute(&db)
        .await
        .unwrap();
    let choices = updates::list(&db)
        .await
        .unwrap()
        .into_iter()
        .flat_map(|g| g.differences)
        .collect();
    let error = updates::apply(&db, &updates::Choices { choices }, 40)
        .await
        .unwrap_err();
    assert!(matches!(
        error,
        library::Error::Invalid(ref message) if message.contains("刷新")
    ));
    assert_eq!(
        mentor::get(&db, id).await.unwrap().unwrap().affiliations,
        fields.affiliations
    );
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn owned_emails_do_not_block_sync_and_cannot_be_applied_to_another_mentor(db: SqlitePool) {
    let (client, mut server) = setup(&db).await;
    let mut first = record("mentor_one", "导师一", "one@demo.edu.example");
    let mut second = record("mentor_two", "导师二", "owned@demo.edu.example");
    adding(
        &client,
        &mut server,
        &db,
        vec![first.clone(), second.clone()],
        vec![],
    )
    .await;
    first.emails.insert(0, "owned@demo.edu.example".into());
    first.revision = 2;
    second.preview.summary.research = Some("仍应更新的方向".into());
    second.revision = 3;
    let page = wire::Changes {
        changes: vec![
            wire::Change {
                id: "mentor_one".into(),
                revision: 2,
                data: wire::ChangeData::Active(first),
            },
            wire::Change {
                id: "mentor_two".into(),
                revision: 3,
                data: wire::ChangeData::Active(second),
            },
        ],
        revision: 3,
        more: false,
    };
    let sync = sync::Synchronizer::default();
    let (result, ()) = tokio::join!(
        sync.check(&client, &db, 100, true),
        respond(&mut server, "after=0", &page)
    );
    assert_eq!(result.unwrap(), 2);
    let second_id = mentor_id(&db, "mentor_two").await;
    assert_eq!(
        mentor::get(&db, second_id)
            .await
            .unwrap()
            .unwrap()
            .research
            .as_deref(),
        Some("仍应更新的方向")
    );
    let choices = updates::list(&db)
        .await
        .unwrap()
        .into_iter()
        .flat_map(|g| g.differences)
        .map(|mut d| {
            d.take = true;
            d
        })
        .collect();
    let error = updates::apply(&db, &updates::Choices { choices }, 20)
        .await
        .unwrap_err();
    assert!(
        matches!(error, library::Error::Invalid(ref message) if message.contains("导师二") && message.contains("合并"))
    );
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn count_does_not_decode_unneeded_profile_fields(db: SqlitePool) {
    let (client, mut server) = setup(&db).await;
    let mut row = record("mentor_one", "原名", "one@demo.edu.example");
    adding(&client, &mut server, &db, vec![row.clone()], vec![]).await;
    row.preview.summary.name = "新名".into();
    change(&db, row, 2).await;
    sqlx::query("UPDATE mentor_detail SET papers = '{}'")
        .execute(&db)
        .await
        .unwrap();
    assert_eq!(updates::count(&db).await.unwrap(), 1);
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn applying_choices_does_not_load_unselected_mentors(db: SqlitePool) {
    let (client, mut server) = setup(&db).await;
    let mut first = record("mentor_one", "原名一", "one@demo.edu.example");
    let mut second = record("mentor_two", "原名二", "two@demo.edu.example");
    adding(
        &client,
        &mut server,
        &db,
        vec![first.clone(), second.clone()],
        vec![],
    )
    .await;
    first.preview.summary.name = "新名一".into();
    second.preview.summary.name = "新名二".into();
    change(&db, first, 2).await;
    change(&db, second, 3).await;
    let first_id = mentor_id(&db, "mentor_one").await;
    let second_id = mentor_id(&db, "mentor_two").await;
    let choices = updates::list(&db)
        .await
        .unwrap()
        .into_iter()
        .flat_map(|g| g.differences)
        .filter(|d| d.id == first_id)
        .collect();
    sqlx::query("UPDATE mentor_detail SET papers = '{}' WHERE mentor_id=?")
        .bind(second_id)
        .execute(&db)
        .await
        .unwrap();
    assert_eq!(
        updates::apply(&db, &updates::Choices { choices }, 20)
            .await
            .unwrap(),
        1
    );
    assert_eq!(
        mentor::get(&db, first_id).await.unwrap().unwrap().name,
        "新名一"
    );
}
