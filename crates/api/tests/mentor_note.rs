use api::{Context, call};
use serde_json::json;
use sqlx::SqlitePool;

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn a_note_saved_is_returned_as_stored(db: SqlitePool) {
    let id = mentor::create(
        &db,
        &mentor::MentorFields {
            name: "王老师".into(),
            ..Default::default()
        },
        0,
    )
    .await
    .unwrap();
    let ctx = Context::new(db, net::Net::new(vec![]).unwrap(), std::path::Path::new(""));
    // The interface shows what this returns, so it must be what a reload reads, whitespace and all.
    for note in [" 先看论文 ", "\u{85}", "\u{feff}", " \n\t"] {
        let saved = call(&ctx, "setMentorNote", json!({"id": id, "note": note}))
            .await
            .unwrap();
        let mentor = call(&ctx, "getMentor", json!({"id": id})).await.unwrap();
        assert_eq!(saved, mentor["note"], "{note:?}");
    }
}
