#![cfg(test)]

use std::num::NonZeroU32;

use mentor::filters::{MatchFilter, MentorFilter};
use mentor::{Affiliation, ContactStatus, MentorFields, MentorQuery};
use sqlx::SqlitePool;

fn affiliation(
    university: Option<&str>,
    school: Option<&str>,
    department: Option<&str>,
    title: Option<&str>,
) -> Affiliation {
    Affiliation {
        university: university.map(Into::into),
        school: school.map(Into::into),
        department: department.map(Into::into),
        title: title.map(Into::into),
    }
}

async fn seed(db: &SqlitePool) -> [i64; 6] {
    let mut ids = Vec::new();
    for (name, affiliations) in [
        (
            "甲",
            vec![
                affiliation(Some("A"), Some("X"), Some("D"), Some("教授")),
                affiliation(Some("B"), Some("Y"), None, None),
                affiliation(Some("B"), Some("X"), None, None),
            ],
        ),
        (
            "乙",
            vec![affiliation(Some("B"), Some("X"), Some("E"), Some("讲师"))],
        ),
        ("丙", vec![]),
        ("丁", vec![affiliation(None, None, Some("D"), None)]),
        ("戊", vec![affiliation(Some("C"), None, None, None)]),
        (
            "回收",
            vec![affiliation(Some("Z"), Some("Z"), Some("Z"), Some("Z"))],
        ),
    ] {
        ids.push(
            mentor::create(
                db,
                &MentorFields {
                    name: name.into(),
                    affiliations,
                    research: Some("机器学习".into()),
                    ..Default::default()
                },
                0,
            )
            .await
            .unwrap(),
        );
    }
    for (i, score) in [(0, 80), (1, 79), (2, 0), (4, 100), (5, 99)] {
        sqlx::query(
            r#"UPDATE mentor
            SET match_score = ?, matched_at = 0, match_model = 'test'
            WHERE id = ?"#,
        )
        .bind(score)
        .bind(ids[i])
        .execute(db)
        .await
        .unwrap();
    }
    // Queued work does not hide the previous result, or turn it into an unanalyzed mentor.
    sqlx::query(
        r#"UPDATE mentor
        SET match_state = 'queued', match_queued_at = 0, match_priority = 0
        WHERE id IN (?, ?)"#,
    )
    .bind(ids[0])
    .bind(ids[3])
    .execute(db)
    .await
    .unwrap();
    for (name, mentors) in [
        ("A 标签", vec![ids[0]]),
        ("B 标签", vec![ids[0], ids[1]]),
        ("C 标签", vec![ids[1]]),
        ("空标签", vec![]),
        ("回收标签", vec![ids[5]]),
    ] {
        mentor::tags::create(
            db,
            &mentor::tags::TagFields {
                name: name.into(),
                color: "#991b1b".into(),
            },
            &mentors,
        )
        .await
        .unwrap();
    }
    mentor::trash::trash(db, &[ids[5]], 1).await.unwrap();
    ids.try_into().unwrap()
}

async fn ids(db: &SqlitePool, filter: MentorFilter) -> Vec<i64> {
    let found = mentor::list(
        db,
        &MentorQuery {
            filter,
            page: NonZeroU32::MIN,
        },
        0,
    )
    .await
    .unwrap();
    assert_eq!(found.total, found.mentors.len() as i64);
    let mut ids: Vec<_> = found.mentors.into_iter().map(|m| m.id).collect();
    ids.sort();
    ids
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn affiliation_filters_use_any_value_and_independent_affiliations(db: SqlitePool) {
    let [a, b, c, d, e, _] = seed(&db).await;
    assert_eq!(ids(&db, MentorFilter::default()).await, [a, b, c, d, e]);
    assert_eq!(
        ids(
            &db,
            MentorFilter {
                universities: vec![Some("A".into()), Some("C".into())],
                ..Default::default()
            }
        )
        .await,
        [a, e]
    );
    assert_eq!(
        ids(
            &db,
            MentorFilter {
                universities: vec![Some("A".into())],
                schools: vec![Some("Y".into())],
                departments: vec![Some("D".into())],
                titles: vec![Some("教授".into())],
                ..Default::default()
            }
        )
        .await,
        [a]
    );
    assert!(
        ids(
            &db,
            MentorFilter {
                universities: vec![Some("C".into())],
                schools: vec![Some("X".into())],
                ..Default::default()
            }
        )
        .await
        .is_empty()
    );
    assert_eq!(
        ids(
            &db,
            MentorFilter {
                universities: vec![Some("B".into()), None],
                ..Default::default()
            }
        )
        .await,
        [a, b, c, d]
    );
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn missing_means_no_affiliation_fills_the_field(db: SqlitePool) {
    let [_, _, c, d, e, _] = seed(&db).await;
    for (filter, expected) in [
        (
            MentorFilter {
                universities: vec![None],
                ..Default::default()
            },
            vec![c, d],
        ),
        (
            MentorFilter {
                schools: vec![None],
                ..Default::default()
            },
            vec![c, d, e],
        ),
        (
            MentorFilter {
                departments: vec![None],
                ..Default::default()
            },
            vec![c, e],
        ),
        (
            MentorFilter {
                titles: vec![None],
                ..Default::default()
            },
            vec![c, d, e],
        ),
    ] {
        assert_eq!(ids(&db, filter).await, expected);
    }
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn tags_scores_and_existing_filters_also_apply_to_export(db: SqlitePool) {
    let [a, b, c, d, e, _] = seed(&db).await;
    assert_eq!(
        ids(
            &db,
            MentorFilter {
                tags: vec![Some(1), Some(3)],
                ..Default::default()
            }
        )
        .await,
        [a, b]
    );
    assert_eq!(
        ids(
            &db,
            MentorFilter {
                tags: vec![None],
                ..Default::default()
            }
        )
        .await,
        [c, d, e]
    );
    assert_eq!(
        ids(
            &db,
            MentorFilter {
                tags: vec![Some(1), None],
                ..Default::default()
            }
        )
        .await,
        [a, c, d, e]
    );
    assert_eq!(
        ids(
            &db,
            MentorFilter {
                matching: Some(MatchFilter::Unanalyzed),
                ..Default::default()
            }
        )
        .await,
        [d]
    );
    test_fixtures::mail::first_email(&db, a, "sent", 0)
        .await
        .unwrap();
    let filter = MentorFilter {
        status: Some(ContactStatus::Contacted),
        keyword: Some("学习".into()),
        matching: Some(MatchFilter::AtLeast { score: 80 }),
        universities: vec![Some("B".into())],
        schools: vec![Some("Y".into())],
        departments: vec![Some("D".into())],
        titles: vec![Some("教授".into())],
        tags: vec![Some(1)],
        ..Default::default()
    };
    assert_eq!(ids(&db, filter.clone()).await, [a]);
    let rows = mentor::export::export(&db, &mentor::export::Scope::Filter(filter), false, 0)
        .await
        .unwrap();
    assert_eq!(rows.len(), 2);
    assert_eq!(rows[1][0], "甲");
    assert_eq!(
        ids(
            &db,
            MentorFilter {
                matching: Some(MatchFilter::AtLeast { score: 80 }),
                ..Default::default()
            }
        )
        .await,
        [a, e]
    );
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn options_count_distinct_active_mentors_and_sort_by_count_then_value(db: SqlitePool) {
    seed(&db).await;
    let options = mentor::filters::options(&db).await.unwrap();
    for (field, expected, missing) in [
        (options.universities, vec![("B", 2), ("A", 1), ("C", 1)], 2),
        (options.schools, vec![("X", 2), ("Y", 1)], 3),
        (options.departments, vec![("D", 2), ("E", 1)], 2),
        (options.titles, vec![("教授", 1), ("讲师", 1)], 3),
    ] {
        let actual: Vec<_> = field
            .values
            .iter()
            .map(|v| (v.value.as_str(), v.count))
            .collect();
        assert_eq!(actual, expected);
        assert_eq!(field.missing, missing);
    }
    let tags: Vec<_> = options
        .tags
        .iter()
        .map(|t| (t.id, t.name.as_str(), t.count))
        .collect();
    assert_eq!(
        tags,
        [
            (2, "B 标签", 2),
            (1, "A 标签", 1),
            (3, "C 标签", 1),
            (5, "回收标签", 0),
            (4, "空标签", 0),
        ]
    );
    assert_eq!(options.untagged, 3);
}
