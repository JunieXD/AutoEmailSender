// Marks the whole file as test code, so clippy's test allowances cover it.
#![cfg(test)]

use mentor::MentorFields;
use mentor::tags::{self, Direction, TagError, TagForm};
use sqlx::SqlitePool;

fn tag(name: &str) -> tags::TagFields {
    TagForm {
        name: name.into(),
        color: "#991B1B".into(),
    }
    .check()
    .unwrap()
}

async fn names_on(db: &SqlitePool, mentor: i64) -> Vec<String> {
    let m = mentor::get(db, mentor).await.unwrap().unwrap();
    m.tags.into_iter().map(|t| t.name).collect()
}

#[test]
fn tag_form_is_checked() {
    assert_eq!(tag(" 梦导 ").name, "梦导");
    assert_eq!(tag("梦导").color, "#991b1b");
    for color in ["991b1b", "#991b1", "#99zz1b", "red"] {
        let form = TagForm {
            name: "梦导".into(),
            color: color.into(),
        };
        assert!(form.check().is_err(), "{color}");
    }
}

#[test]
fn tag_names_reject_import_separators_for_create_and_rename() {
    for name in ["AI;机器人", "AI；机器人", "AI、机器人"] {
        assert!(
            TagForm {
                name: name.into(),
                color: "#991b1b".into()
            }
            .check()
            .is_err()
        );
        assert!(tags::checked_name(name).is_err());
    }
    assert_eq!(tags::checked_name(" AI, 机器人 ").unwrap(), "AI, 机器人");
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn tags_keep_the_users_order(db: SqlitePool) {
    let mentor = MentorFields {
        name: "张伟".into(),
        ..MentorFields::default()
    };
    let id = mentor::create(&db, &mentor, 0).await.unwrap();
    let dream = tags::create(&db, &tag("梦导"), &[]).await.unwrap();
    let camp = tags::create(&db, &tag("夏令营"), &[id]).await.unwrap();
    let safe = tags::create(&db, &tag("保底"), &[]).await.unwrap();
    assert!(matches!(
        tags::create(&db, &tag("梦导"), &[id]).await,
        Err(TagError::NameTaken)
    ));

    for tag in [safe.id, dream.id, dream.id] {
        tags::tag_mentors(&db, tag, &[id], true).await.unwrap();
    }
    assert_eq!(names_on(&db, id).await, ["梦导", "夏令营", "保底"]);
    // Two quick moves both count; moving past either end does nothing.
    for (tag, direction) in [
        (safe.id, Direction::Up),
        (safe.id, Direction::Up),
        (safe.id, Direction::Up),
        (dream.id, Direction::Down),
        (dream.id, Direction::Down),
    ] {
        tags::move_tag(&db, tag, direction).await.unwrap();
    }
    let all: Vec<String> = tags::list(&db)
        .await
        .unwrap()
        .into_iter()
        .map(|t| t.name)
        .collect();
    assert_eq!(all, ["保底", "夏令营", "梦导"]);
    assert_eq!(names_on(&db, id).await, ["保底", "夏令营", "梦导"]);
    assert!(matches!(
        tags::rename(&db, camp.id, "梦导").await,
        Err(TagError::NameTaken)
    ));

    tags::tag_mentors(&db, camp.id, &[id], true).await.unwrap();
    for tag in [safe.id, dream.id] {
        tags::tag_mentors(&db, tag, &[id], false).await.unwrap();
    }
    assert_eq!(names_on(&db, id).await, ["夏令营"]);
    tags::delete(&db, camp.id).await.unwrap();
    assert!(names_on(&db, id).await.is_empty());
}
