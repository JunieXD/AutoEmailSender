// Marks the whole file as test code, so clippy's test allowances cover the helpers too.
#![cfg(test)]

use sqlx::SqlitePool;
use template::{Fill, TemplateForm};

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

fn fill<'a>(mentor_name: &'a str, research: Option<&'a str>) -> Fill<'a> {
    Fill {
        mentor_name,
        mentor_title: Some("教授"),
        university: Some("清华大学"),
        school: None,
        department: None,
        research,
        my_name: Some("林晓"),
    }
}

#[test]
fn missing_values_leave_their_placeholders() {
    let subject = fill("王伟", None).subject("{学校}{学院}硕士申请——{我的姓名}{未知}");
    assert_eq!(subject, "清华大学{学院}硕士申请——林晓{未知}");
}

#[test]
fn values_in_the_body_are_plain_text() {
    let research = "[点我](https://example.com) *重点*\n1. 第二行";
    let body = fill("{学校}", Some(research)).body("{导师姓名}老师：\n\n{导师研究方向}");
    assert_eq!(
        body,
        "{学校}老师：\n\n\\[点我\\](https://example.com) \\*重点\\* 1. 第二行"
    );
    let body = fill("王伟", Some("1. 机器学习")).body("{导师研究方向}");
    assert_eq!(body, "1\\. 机器学习");
}
