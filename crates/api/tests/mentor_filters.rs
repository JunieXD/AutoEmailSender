#![cfg(test)]

use api::{Context, ErrorCode, call};
use serde_json::json;
use sqlx::SqlitePool;

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn list_and_export_validate_filters_at_the_boundary(db: SqlitePool) {
    mentor::create(
        &db,
        &mentor::MentorFields {
            name: "甲".into(),
            affiliations: vec![mentor::Affiliation {
                university: Some("A".into()),
                school: Some("X".into()),
                department: Some("D".into()),
                title: Some("教授".into()),
            }],
            ..Default::default()
        },
        0,
    )
    .await
    .unwrap();
    let ctx = Context::new(db, net::Net::new(vec![]).unwrap(), Default::default());
    for score in [0, 101, 255] {
        let filter = json!({"matching": {"kind": "atLeast", "score": score}});
        let mut query = filter.clone();
        query["page"] = json!(1);
        assert!(matches!(
            call(&ctx, "listMentors", query).await.unwrap_err().code,
            ErrorCode::Invalid
        ));
        let input = serde_json::from_value(json!({
            "scope": {"kind": "filter", "value": filter}, "format": "csv", "note": false
        }))
        .unwrap();
        assert!(matches!(
            api::export_mentors(&ctx, input).await.unwrap_err().code,
            ErrorCode::Invalid
        ));
    }
    for score in [1, 100] {
        call(
            &ctx,
            "listMentors",
            json!({"page": 1, "matching": {"kind": "atLeast", "score": score}}),
        )
        .await
        .unwrap();
    }
    for (field, value) in [
        ("universities", "A"),
        ("schools", "X"),
        ("departments", "D"),
        ("titles", "教授"),
    ] {
        let mut query = json!({"page": 1});
        query[field] = json!([" \t　"]);
        assert!(matches!(
            call(&ctx, "listMentors", query.clone())
                .await
                .unwrap_err()
                .code,
            ErrorCode::Invalid
        ));
        let input = serde_json::from_value(json!({
            "scope": {"kind": "filter", "value": query}, "format": "csv", "note": false
        }))
        .unwrap();
        assert!(matches!(
            api::export_mentors(&ctx, input).await.unwrap_err().code,
            ErrorCode::Invalid
        ));
        query[field] = json!([format!(" {value}　")]);
        assert_eq!(
            call(&ctx, "listMentors", query.clone()).await.unwrap()["total"],
            1
        );
        let input = serde_json::from_value(json!({
            "scope": {"kind": "filter", "value": query}, "format": "csv", "note": false
        }))
        .unwrap();
        let bytes = api::export_mentors(&ctx, input).await.unwrap();
        let rows = sheet::read("mentors.csv", &bytes).unwrap();
        assert_eq!(rows[0].rows[1][0], "甲");
    }
    let options = call(&ctx, "mentorFilterOptions", json!({})).await.unwrap();
    assert_eq!(options["universities"]["values"][0]["value"], "A");
    assert_eq!(options["untagged"], 1);
}
