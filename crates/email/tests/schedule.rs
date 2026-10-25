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
        let s = plan(&w, &[], at(26, 8), &Utc, &mut |_| random).unwrap();
        assert_eq!(s.planned_at, at(26, 9) + random);
        assert_eq!(s.latest_at, at(26, 11));
    }
    let s = plan(&w, &[at(26, 9)], at(26, 10), &Utc, &mut |n| n - 1).unwrap();
    assert_eq!(s.planned_at, at(26, 11) - 1);
    assert!(
        plan(&w, &[at(26, 9), at(26, 10)], at(26, 8), &Utc, &mut |_| 0)
            .unwrap()
            .planned_at
            == at(27, 9)
    );
    assert_eq!(
        plan(
            &w,
            &[at(26, 9), at(26, 10), at(27, 9), at(27, 10)],
            at(26, 8),
            &Utc,
            &mut |_| 0
        ),
        None
    );
    assert_eq!(plan(&w, &[], at(27, 11), &Utc, &mut |_| 0), None);
}

#[test]
fn sorts_dates_and_translates_local_time_to_utc() {
    let mut w = window();
    w.dates.reverse();
    w.dates.push("2026-10-26".into());
    let zone = FixedOffset::east_opt(8 * 3600).unwrap();
    let s = plan(&w, &[], at(25, 23), &zone, &mut |_| 0).unwrap();
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
    let s = plan(&w, &[], now, &zone, &mut |_| 0).unwrap();
    assert_eq!(
        s.planned_at,
        Utc.with_ymd_and_hms(2026, 3, 9, 6, 30, 0)
            .unwrap()
            .timestamp_millis()
    );
    w.dates = vec!["2026-11-01".into()];
    w.start = "01:30".into();
    w.end = "02:30".into();
    let s = plan(&w, &[], now, &zone, &mut |_| 0).unwrap();
    assert_eq!(
        s.planned_at,
        Utc.with_ymd_and_hms(2026, 11, 1, 5, 30, 0)
            .unwrap()
            .timestamp_millis()
    );
    assert_eq!(s.latest_at - s.planned_at, 2 * 3_600_000);
}
