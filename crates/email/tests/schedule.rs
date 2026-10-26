#![cfg(test)]
use chrono::{FixedOffset, TimeZone, Utc};
use email::schedule::{Window, plan};

fn window() -> Window {
    Window {
        dates: vec!["2026-10-26".into(), "2026-10-27".into()],
        start: "09:00".into(),
        end: "11:00".into(),
        per_window: 2,
    }
}
fn at(day: u32, hour: u32) -> i64 {
    Utc.with_ymd_and_hms(2026, 10, day, hour, 0, 0)
        .unwrap()
        .timestamp_millis()
}

#[test]
fn evenly_divides_remaining_capacity_and_randomizes_only_the_first_segment() {
    let w = window();
    for random in [0, 1, 3_599_999] {
        let s = plan(&w.validate().unwrap(), &[], at(26, 8), &Utc, &mut |_| {
            random
        })
        .unwrap();
        assert_eq!(s.planned_at, at(26, 9) + random);
        assert_eq!(s.latest_at, at(26, 11));
    }
    let s = plan(
        &w.validate().unwrap(),
        &[at(26, 9)],
        at(26, 10),
        &Utc,
        &mut |n| n - 1,
    )
    .unwrap();
    assert_eq!(s.planned_at, at(26, 11) - 1);
    assert!(
        plan(
            &w.validate().unwrap(),
            &[at(26, 9), at(26, 10)],
            at(26, 8),
            &Utc,
            &mut |_| 0
        )
        .unwrap()
        .planned_at
            == at(27, 9)
    );
    assert_eq!(
        plan(
            &w.validate().unwrap(),
            &[at(26, 9), at(26, 10), at(27, 9), at(27, 10)],
            at(26, 8),
            &Utc,
            &mut |_| 0
        ),
        None
    );
    assert_eq!(
        plan(&w.validate().unwrap(), &[], at(27, 11), &Utc, &mut |_| 0),
        None
    );
}

#[test]
fn sorts_dates_and_translates_local_time_to_utc() {
    let mut w = window();
    w.dates.reverse();
    w.dates.push("2026-10-26".into());
    let zone = FixedOffset::east_opt(8 * 3600).unwrap();
    let s = plan(&w.validate().unwrap(), &[], at(25, 23), &zone, &mut |_| 0).unwrap();
    assert_eq!(s.planned_at, at(26, 1));
    assert_eq!(s.latest_at, at(26, 3));
}

#[test]
fn handles_daylight_saving_gaps_and_repeated_times() {
    let zone = chrono_tz::America::New_York;
    let mut w = Window {
        dates: vec!["2026-03-08".into(), "2026-03-09".into()],
        start: "02:30".into(),
        end: "04:30".into(),
        per_window: 1,
    };
    let now = Utc
        .with_ymd_and_hms(2026, 3, 8, 0, 0, 0)
        .unwrap()
        .timestamp_millis();
    let s = plan(&w.validate().unwrap(), &[], now, &zone, &mut |_| 0).unwrap();
    assert_eq!(
        s.planned_at,
        Utc.with_ymd_and_hms(2026, 3, 9, 6, 30, 0)
            .unwrap()
            .timestamp_millis()
    );
    w.dates = vec!["2026-11-01".into()];
    w.start = "01:30".into();
    w.end = "02:30".into();
    let s = plan(&w.validate().unwrap(), &[], now, &zone, &mut |_| 0).unwrap();
    assert_eq!(
        s.planned_at,
        Utc.with_ymd_and_hms(2026, 11, 1, 5, 30, 0)
            .unwrap()
            .timestamp_millis()
    );
    assert_eq!(s.latest_at - s.planned_at, 2 * 3_600_000);
}

#[test]
fn capacity_counts_only_unexpired_distinct_windows_in_the_planning_zone() {
    let mut w = window();
    w.dates.push("2026-10-26".into());
    let zone = FixedOffset::east_opt(8 * 3600).unwrap();
    for (now, windows, next_change) in [
        (at(26, 0), 2, Some(at(26, 3))),
        (at(26, 2), 2, Some(at(26, 3))),
        (at(26, 3), 1, Some(at(27, 3))),
        (at(27, 3), 0, None),
    ] {
        let capacity = w.validate().unwrap().capacity(now, &zone);
        assert_eq!(capacity.windows, windows);
        assert_eq!(capacity.emails, windows as u64 * 2);
        assert_eq!(capacity.next_change_at, next_change);
    }
}

#[test]
fn capacity_and_planning_skip_the_same_missing_dst_boundaries() {
    let zone = chrono_tz::America::New_York;
    let now = Utc
        .with_ymd_and_hms(2026, 3, 8, 0, 0, 0)
        .unwrap()
        .timestamp_millis();
    for (start, end, end_utc_hour) in [("02:30", "04:30", 8), ("01:30", "02:30", 6)] {
        let mut w = Window {
            dates: vec!["2026-03-08".into()],
            start: start.into(),
            end: end.into(),
            per_window: 2,
        };
        assert_eq!(w.validate().unwrap().capacity(now, &zone).emails, 0);
        assert_eq!(
            w.validate().unwrap().capacity(now, &zone).next_change_at,
            None
        );
        assert_eq!(
            plan(&w.validate().unwrap(), &[], now, &zone, &mut |_| 0),
            None
        );
        w.dates.push("2026-03-09".into());
        assert_eq!(w.validate().unwrap().capacity(now, &zone).windows, 1);
        assert_eq!(w.validate().unwrap().capacity(now, &zone).emails, 2);
        assert_eq!(
            w.validate().unwrap().capacity(now, &zone).next_change_at,
            Some(
                Utc.with_ymd_and_hms(2026, 3, 9, end_utc_hour, 30, 0)
                    .unwrap()
                    .timestamp_millis()
            )
        );
        assert!(plan(&w.validate().unwrap(), &[], now, &zone, &mut |_| 0).is_some());
    }
}

#[test]
fn invalid_window_text_is_rejected_before_capacity_or_planning() {
    for (dates, start, end, count) in [
        (vec!["2026-02-30"], "09:00", "11:00", 2),
        (vec!["2026-2-03"], "09:00", "11:00", 2),
        (vec![], "09:00", "11:00", 2),
        (vec!["2026-10-26"], "9:00", "11:00", 2),
        (vec!["2026-10-26"], "09:00", "24:00", 2),
        (vec!["2026-10-26"], "11:00", "09:00", 2),
        (vec!["2026-10-26"], "09:00", "11:00", 0),
    ] {
        let window = Window {
            dates: dates.into_iter().map(str::to_owned).collect(),
            start: start.into(),
            end: end.into(),
            per_window: count,
        };
        assert!(window.validate().is_err(), "{window:?}");
    }
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn invalid_stored_window_text_is_a_decode_error(db: sqlx::SqlitePool) {
    let task = sqlx::query_scalar(
        r#"INSERT INTO task (name, kind, writer, schedule, state, created_at,
            window_dates, window_start, window_end, per_window)
        VALUES ('test', 'first', 'ai', 'window', 'active', 0,
            '["2026-02-30"]', '09:00', '11:00', 2)
        RETURNING id"#,
    )
    .fetch_one(&db)
    .await
    .unwrap();
    assert!(matches!(
        email::schedule::get(&mut db.acquire().await.unwrap(), task).await,
        Err(sqlx::Error::Decode(_))
    ));
}
