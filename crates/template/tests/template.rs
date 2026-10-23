// Marks the whole file as test code, so clippy's test allowances cover the helpers too.
#![cfg(test)]

use sqlx::SqlitePool;
use template::TemplateForm;

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn the_body_is_saved_as_written(db: SqlitePool) {
    let body = "　　尊敬的{导师姓名}老师：\n\n　　您好！";
    let form = TemplateForm {
        name: " 首封 ".into(),
        subject: "硕士申请——{我的姓名}\n".into(),
        body_md: body.into(),
    };
    let id = template::create(&db, &form.check().unwrap(), 0)
        .await
        .unwrap();
    let saved = template::get(&db, id).await.unwrap().unwrap();
    assert_eq!(saved.name, "首封");
    assert_eq!(saved.subject, "硕士申请——{我的姓名}");
    assert_eq!(saved.body_md, body);
}
