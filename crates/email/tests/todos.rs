#![cfg(test)]

use chrono::{NaiveDate, TimeZone};
use email::todos::{self, TodoForm, TodoQuery};
use sqlx::SqlitePool;

async fn mentor(db: &SqlitePool, name: &str) -> i64 {
    mentor::create(
        db,
        &mentor::MentorFields {
            name: name.into(),
            ..Default::default()
        },
        0,
    )
    .await
    .unwrap()
}

fn at(date: &str, hour: u32, minute: u32) -> i64 {
    let date = NaiveDate::parse_from_str(date, "%Y-%m-%d").unwrap();
    chrono::FixedOffset::east_opt(8 * 3600)
        .unwrap()
        .from_local_datetime(&date.and_hms_opt(hour, minute, 0).unwrap())
        .unwrap()
        .timestamp_millis()
}

fn form(text: &str, date: Option<&str>, time: Option<&str>) -> TodoForm {
    TodoForm {
        text: text.into(),
        due_date: date.map(Into::into),
        due_time: time.map(Into::into),
    }
}

async fn add(
    db: &SqlitePool,
    m: i64,
    text: &str,
    date: Option<&str>,
    time: Option<&str>,
    now: i64,
) -> i64 {
    todos::add(db, m, &form(text, date, time).check().unwrap(), now)
        .await
        .unwrap()
        .unwrap()
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn manual_edits_completion_merge_and_permanent_deletion(db: SqlitePool) {
    let m = mentor(&db, "王老师").await;
    let keep = mentor(&db, "李老师").await;
    let id = add(&db, m, "  发成绩单  ", Some("2026-11-01"), Some("17:00"), 1).await;
    let original = todos::for_mentor(&db, m).await.unwrap().remove(0);
    assert_eq!(original.text, "发成绩单");
    assert_eq!(original.message_id, None);
    assert_eq!(original.created_at, 1);
    assert!(!original.done);
    assert!(todos::set_done(&db, id, true, 2).await.unwrap());
    assert!(todos::for_mentor(&db, m).await.unwrap()[0].done);
    assert_eq!(
        todos::list(
            &db,
            &TodoQuery {
                page: std::num::NonZeroU32::MIN
            }
        )
        .await
        .unwrap()
        .total,
        0
    );
    todos::set_done(&db, id, false, 3).await.unwrap();
    todos::update(&db, id, &form("填报名表", None, None).check().unwrap(), 4)
        .await
        .unwrap();
    mentor::merge::merge(&db, keep, m, &[], 5).await.unwrap();
    assert!(todos::for_mentor(&db, m).await.unwrap().is_empty());
    let saved = todos::for_mentor(&db, keep).await.unwrap().remove(0);
    assert_eq!(saved.id, id);
    assert_eq!(saved.text, "填报名表");
    assert_eq!(saved.due_date, None);
    mentor::trash::trash(&db, &[keep], 6).await.unwrap();
    assert!(
        todos::add(&db, keep, &form("不能加", None, None).check().unwrap(), 7)
            .await
            .unwrap()
            .is_none()
    );
    mentor::trash::delete(&db, &[keep]).await.unwrap();
    assert!(todos::for_mentor(&db, keep).await.unwrap().is_empty());
    let m = mentor(&db, "赵老师").await;
    let id = add(&db, m, "可删", None, None, 8).await;
    assert!(todos::delete(&db, id).await.unwrap());
    assert!(!todos::delete(&db, id).await.unwrap());
}

#[test]
fn manual_input_validates_civil_dates_times_and_text() {
    for value in ["", " \n\t", &"学".repeat(101)] {
        assert!(form(value, None, None).check().is_err());
    }
    assert!(form(&"学".repeat(100), None, None).check().is_ok());
    for date in [
        "2026-02-29",
        "2026-04-31",
        "2026-1-01",
        "2026-01-1",
        "2026-00-01",
        "2026-01-01T00:00:00Z",
    ] {
        assert!(form("事", Some(date), None).check().is_err(), "{date}");
    }
    for time in ["24:00", "12:60", "1:00", "09:00:00"] {
        assert!(
            form("事", Some("2024-02-29"), Some(time)).check().is_err(),
            "{time}"
        );
    }
    assert!(form("事", None, Some("09:00")).check().is_err());
    assert!(
        form("事", Some("2000-01-01"), Some("23:59"))
            .check()
            .is_ok()
    );
    assert!(form("事", Some("2099-12-31"), None).check().is_ok());
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn reminder_time_catch_up_expiration_once_and_deadline_edits(db: SqlitePool) {
    let zone = chrono::FixedOffset::east_opt(8 * 3600).unwrap();
    let m = mentor(&db, "王老师").await;
    let created = at("2026-10-26", 8, 0);
    let id = add(
        &db,
        m,
        "发成绩单",
        Some("2026-10-28"),
        Some("17:00"),
        created,
    )
    .await;
    let before = todos::reminders(&db, at("2026-10-27", 8, 59), &zone)
        .await
        .unwrap();
    assert!(before.messages.is_empty());
    assert_eq!(before.next_at, Some(at("2026-10-27", 9, 0)));
    assert_eq!(
        todos::reminders(&db, at("2026-10-27", 9, 0), &zone)
            .await
            .unwrap()
            .messages,
        ["明天截止：发成绩单"]
    );
    assert!(
        todos::reminders(&db, at("2026-10-27", 10, 0), &zone)
            .await
            .unwrap()
            .messages
            .is_empty()
    );
    todos::update(
        &db,
        id,
        &form("发新版成绩单", Some("2026-10-28"), Some("17:00"))
            .check()
            .unwrap(),
        at("2026-10-27", 10, 1),
    )
    .await
    .unwrap();
    assert!(
        todos::reminders(&db, at("2026-10-27", 10, 2), &zone)
            .await
            .unwrap()
            .messages
            .is_empty()
    );
    todos::update(
        &db,
        id,
        &form("发新版成绩单", Some("2026-10-30"), Some("16:00"))
            .check()
            .unwrap(),
        at("2026-10-27", 10, 3),
    )
    .await
    .unwrap();
    assert_eq!(
        todos::reminders(&db, at("2026-10-30", 15, 59), &zone)
            .await
            .unwrap()
            .messages,
        ["今天截止：发新版成绩单"]
    );
    let expired = add(&db, m, "已过期", Some("2026-11-01"), Some("10:00"), created).await;
    add(&db, m, "整天有效", Some("2026-11-01"), None, created).await;
    assert_eq!(
        todos::reminders(&db, at("2026-11-01", 10, 0), &zone)
            .await
            .unwrap()
            .messages,
        ["今天截止：整天有效"]
    );
    let sent: Option<i64> = sqlx::query_scalar("SELECT reminded_at FROM todo WHERE id=?")
        .bind(expired)
        .fetch_one(&db)
        .await
        .unwrap();
    assert_eq!(sent, None);
    add(&db, m, "整天已过期", Some("2026-11-01"), None, created).await;
    assert!(
        todos::reminders(&db, at("2026-11-02", 0, 0), &zone)
            .await
            .unwrap()
            .messages
            .is_empty()
    );
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn reminders_exclude_newly_seen_done_trashed_and_disabled(db: SqlitePool) {
    let zone = chrono::FixedOffset::east_opt(8 * 3600).unwrap();
    let m = mentor(&db, "王老师").await;
    let trashed = mentor(&db, "李老师").await;
    let before = at("2026-10-26", 8, 0);
    let after = at("2026-10-27", 10, 0);
    add(&db, m, "刚添加", Some("2026-10-28"), None, after).await;
    let edited = add(&db, m, "刚改日期", Some("2026-10-29"), None, before).await;
    todos::update(
        &db,
        edited,
        &form("刚改日期", Some("2026-10-28"), None).check().unwrap(),
        after,
    )
    .await
    .unwrap();
    let done = add(&db, m, "已完成", Some("2026-10-28"), None, before).await;
    todos::set_done(&db, done, true, before).await.unwrap();
    add(&db, trashed, "回收站", Some("2026-10-28"), None, before).await;
    mentor::trash::trash(&db, &[trashed], before).await.unwrap();
    assert!(
        todos::reminders(&db, after, &zone)
            .await
            .unwrap()
            .messages
            .is_empty()
    );
    let live = add(&db, m, "关开关", Some("2026-10-28"), None, before).await;
    sqlx::query("UPDATE profile SET todo_reminders=0")
        .execute(&db)
        .await
        .unwrap();
    assert!(
        todos::reminders(&db, after, &zone)
            .await
            .unwrap()
            .messages
            .is_empty()
    );
    sqlx::query("UPDATE profile SET todo_reminders=1")
        .execute(&db)
        .await
        .unwrap();
    assert_eq!(
        todos::reminders(&db, after, &zone).await.unwrap().messages,
        ["明天截止：关开关"]
    );
    todos::update(
        &db,
        live,
        &form("去掉日期", None, None).check().unwrap(),
        after,
    )
    .await
    .unwrap();
    let (reminded, changed): (Option<i64>, i64) =
        sqlx::query_as("SELECT reminded_at,due_changed_at FROM todo WHERE id=?")
            .bind(live)
            .fetch_one(&db)
            .await
            .unwrap();
    assert_eq!((reminded, changed), (None, after));
}

#[test]
fn skipped_reminder_minute_uses_the_first_existing_time() {
    let zone = chrono_tz::Pacific::Apia;
    // The whole of 2011-12-30 was skipped when Samoa crossed the date line.
    let due = todos::date("2011-12-31").unwrap();
    assert_eq!(
        todos::reminder_at(due, &zone),
        Some(
            zone.with_ymd_and_hms(2011, 12, 31, 0, 0, 0)
                .unwrap()
                .timestamp_millis()
        )
    );
    let zone = chrono_tz::America::New_York;
    for day in [9, 10] {
        let due = NaiveDate::from_ymd_opt(2026, 3, day).unwrap();
        assert_eq!(
            todos::reminder_at(due, &zone),
            Some(
                zone.with_ymd_and_hms(2026, 3, day - 1, 9, 0, 0)
                    .unwrap()
                    .timestamp_millis()
            )
        );
    }
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn list_orders_local_deadlines_before_undated_and_paginates(db: SqlitePool) {
    let m = mentor(&db, "王老师").await;
    let other = mentor(&db, "李老师").await;
    let none = add(&db, m, "没日期", None, None, 1).await;
    let end = add(&db, m, "全天", Some("2026-10-27"), None, 2).await;
    let late = add(&db, m, "夜晚", Some("2026-10-27"), Some("23:59"), 3).await;
    let early = add(&db, m, "早晨", Some("2026-10-27"), Some("09:00"), 4).await;
    let expired = add(&db, other, "过期", Some("2026-10-26"), None, 5).await;
    for i in 0..51 {
        add(&db, m, &format!("以后{i}"), Some("2026-11-01"), None, 6 + i).await;
    }
    let done = add(&db, m, "已完成", None, None, 60).await;
    todos::set_done(&db, done, true, 61).await.unwrap();
    let page = |n| TodoQuery {
        page: std::num::NonZeroU32::new(n).unwrap(),
    };
    let first = todos::list(&db, &page(1)).await.unwrap();
    assert_eq!(first.total, 56);
    assert_eq!(first.todos.len(), 50);
    assert_eq!(
        first
            .todos
            .iter()
            .take(4)
            .map(|r| r.todo.id)
            .collect::<Vec<_>>(),
        [expired, early, late, end]
    );
    assert_eq!(first.todos[0].mentor_name, "李老师");
    assert_eq!(first.todos[0].mentor_id, other);
    let last = todos::list(&db, &page(2)).await.unwrap();
    assert_eq!(last.todos.len(), 6);
    assert_eq!(last.todos.last().unwrap().todo.id, none);
    assert!(todos::list(&db, &page(3)).await.unwrap().todos.is_empty());
    mentor::trash::trash(&db, &[other], 100).await.unwrap();
    assert_eq!(todos::list(&db, &page(1)).await.unwrap().total, 55);
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn storage_rejects_invalid_combinations(db: SqlitePool) {
    let m = mentor(&db, "王老师").await;
    for (text, date, time) in [
        ("", None, None),
        (" padded ", None, None),
        ("事", None, Some("09:00")),
        ("事", Some("2026-02-30"), None),
        ("事", Some("2026-01-01"), Some("24:00")),
    ] {
        assert!(
            sqlx::query(
                r#"INSERT INTO todo (mentor_id, text, due_date, due_time, created_at,
                    due_changed_at)
                VALUES (?, ?, ?, ?, 0, 0)"#
            )
            .bind(m)
            .bind(text)
            .bind(date)
            .bind(time)
            .execute(&db)
            .await
            .is_err()
        );
    }
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn reminders_find_the_next_eligible_date_and_skip_only_recently_seen_items(db: SqlitePool) {
    let zone = chrono::FixedOffset::east_opt(8 * 3600).unwrap();
    let m = mentor(&db, "王老师").await;
    let old = at("2026-10-26", 8, 0);
    let now = at("2026-10-27", 10, 0);
    add(&db, m, "已过期", Some("2026-10-26"), None, old).await;
    add(&db, m, "刚添加的明天待办", Some("2026-10-28"), None, now).await;
    let first = add(&db, m, "明天待办", Some("2026-10-28"), None, old).await;
    add(&db, m, "后天待办", Some("2026-10-29"), None, old).await;
    add(&db, m, "更晚的待办", Some("2026-10-30"), None, old).await;
    let result = todos::reminders(&db, now, &zone).await.unwrap();
    assert_eq!(result.messages, ["明天截止：明天待办"]);
    assert_eq!(result.next_at, Some(at("2026-10-28", 9, 0)));
    todos::update(
        &db,
        first,
        &form("改钟点", Some("2026-10-28"), Some("18:00"))
            .check()
            .unwrap(),
        now + 1,
    )
    .await
    .unwrap();
    let reset: Option<i64> = sqlx::query_scalar("SELECT reminded_at FROM todo WHERE id=?")
        .bind(first)
        .fetch_one(&db)
        .await
        .unwrap();
    assert_eq!(reset, None);
    let result = todos::reminders(&db, at("2026-10-28", 9, 0), &zone)
        .await
        .unwrap();
    assert_eq!(result.messages, ["明天截止：后天待办"]);
    assert_eq!(result.next_at, Some(at("2026-10-29", 9, 0)));
}
