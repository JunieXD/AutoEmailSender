// Marks the whole file as test code, so clippy's test allowances cover it.
#![cfg(test)]

use mentor::MentorFields;
use mentor::tags::{self, TagError, TagForm};
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

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn tags_keep_the_users_order(db: SqlitePool) {
    let mentor = MentorFields {
        name: "张伟".into(),
        ..MentorFields::default()
    };
    let id = mentor::create(&db, &mentor, 0).await.unwrap();
    let dream = tags::create(&db, &tag("梦导")).await.unwrap();
    let camp = tags::create(&db, &tag("夏令营")).await.unwrap();
    let safe = tags::create(&db, &tag("保底")).await.unwrap();
    assert!(matches!(
        tags::create(&db, &tag("梦导")).await,
        Err(TagError::NameTaken)
    ));

    tags::set_for_mentor(&db, id, &[safe.id, dream.id])
        .await
        .unwrap();
    assert_eq!(names_on(&db, id).await, ["梦导", "保底"]);
    tags::reorder(&db, &[safe.id, camp.id, dream.id])
        .await
        .unwrap();
    let all: Vec<String> = tags::list(&db)
        .await
        .unwrap()
        .into_iter()
        .map(|t| t.name)
        .collect();
    assert_eq!(all, ["保底", "夏令营", "梦导"]);
    assert_eq!(names_on(&db, id).await, ["保底", "梦导"]);

    tags::set_for_mentor(&db, id, &[camp.id]).await.unwrap();
    assert_eq!(names_on(&db, id).await, ["夏令营"]);
    tags::delete(&db, camp.id).await.unwrap();
    assert!(names_on(&db, id).await.is_empty());
}
