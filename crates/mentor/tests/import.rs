#![cfg(test)]

use mentor::export::{self, Scope};
use mentor::import::{self, Decisions, Field, GroupChoice, Setup};
use mentor::{Affiliation, MentorFields};
use sheet::Sheet;
use sqlx::SqlitePool;

fn table(rows: &[&[&str]]) -> Vec<Sheet> {
    let sheet = Sheet {
        name: "导师".into(),
        rows: rows
            .iter()
            .map(|r| r.iter().map(|s| (*s).into()).collect())
            .collect(),
    };
    sheet::read("导师.xlsx", &sheet::write_xlsx(&[sheet]).unwrap()).unwrap()
}
async fn add(db: &SqlitePool, name: &str, emails: &[&str], university: &str) -> i64 {
    mentor::create(
        db,
        &MentorFields {
            name: name.into(),
            emails: emails.iter().map(|s| (*s).into()).collect(),
            affiliations: vec![Affiliation {
                university: Some(university.into()),
                ..Default::default()
            }],
            ..Default::default()
        },
        0,
    )
    .await
    .unwrap()
}
async fn apply(db: &SqlitePool, sheets: &[Sheet], decisions: &Decisions) -> import::Imported {
    import::import(db, sheets, &Setup::default(), decisions, 1)
        .await
        .unwrap()
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn headers_mapping_validation_and_physical_rows(db: SqlitePool) {
    let mut sheets = table(&[
        &["", ""],
        &["ＮＡＭＥ １", "Ｅ－ＭＡＩＬ", "个人主页", "额外资料"],
        &[
            "王明",
            "ｗａｎｇ＠ｐｋｕ．ｅｄｕ．ｃｎ",
            "https://example.edu.cn/wang",
            "方向",
        ],
        &["", "", "", ""],
        &["", "bad@pku", "", ""],
        &["李明", "bad@pku", "", ""],
        &["赵明", "", "bad-url", ""],
    ]);
    sheets.insert(
        0,
        Sheet {
            name: "说明".into(),
            rows: vec![vec!["如何填写".into()]],
        },
    );
    let p = import::preview(&db, &sheets, &Setup::default())
        .await
        .unwrap();
    assert_eq!(p.sheet, 1);
    assert_eq!(p.created, 1);
    assert_eq!(p.columns[0].field, Some(Field::Name));
    assert_eq!(p.columns[1].field, Some(Field::Email));
    assert_eq!(
        p.errors.iter().map(|e| e.row).collect::<Vec<_>>(),
        [5, 6, 7]
    );
    assert!(p.errors[0].reason.contains("姓名"));
    assert!(p.errors[1].reason.contains("邮箱"));
    assert!(p.errors[2].reason.contains("主页"));
    let setup = Setup {
        sheet: Some(1),
        columns: Some(vec![
            Some(Field::Name),
            Some(Field::Email),
            Some(Field::Homepage),
            Some(Field::Research),
        ]),
    };
    let p = import::preview(&db, &sheets, &setup).await.unwrap();
    assert_eq!(p.columns[3].field, Some(Field::Research));
    import::import(&db, &sheets, &setup, &Decisions::default(), 1)
        .await
        .unwrap();
    let rows = export::export(&db, &Scope::Ids(vec![1]), true)
        .await
        .unwrap();
    assert!(rows[1].contains(&"方向".to_owned()));
    assert!(rows[1].contains(&"wang@pku.edu.cn".to_owned()));
    let setup = Setup {
        sheet: Some(1),
        columns: Some(vec![Some(Field::Name), Some(Field::Name)]),
    };
    assert!(
        import::preview(&db, &sheets, &setup)
            .await
            .unwrap()
            .issue
            .unwrap()
            .contains("只能对应一列")
    );
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn matching_groups_and_ambiguous_emails(db: SqlitePool) {
    let wang = add(&db, "王明", &["wang@pku.edu.cn"], "北京大学").await;
    let li = add(&db, "李明", &[], "北京大学").await;
    let sheets = table(&[
        &["姓名", "学校", "邮箱", "主页"],
        &["表中姓名", "北京大学", "WANG@pku.edu.cn", ""],
        &[
            "表中姓名",
            "北京大学",
            "wang@pku.edu.cn;new@gmail.com",
            "https://example.cn",
        ],
        &["李明", "北京大学", "", ""],
        &["王明", "北京大学", "another@pku.edu.cn", ""],
        &["新导师", "", "x@a.cn", ""],
        &["新导师", "", "x@a.cn;y@a.cn", ""],
    ]);
    let p = import::preview(&db, &sheets, &Setup::default())
        .await
        .unwrap();
    assert_eq!(p.created, 2);
    assert_eq!(p.maybe_same.len(), 1);
    assert_eq!(p.maybe_same[0].selected, Some(li));
    let done = apply(&db, &sheets, &Decisions::default()).await;
    assert_eq!((done.created, done.updated), (2, 1));
    let p = mentor::get(&db, wang).await.unwrap().unwrap();
    assert_eq!(p.emails, ["wang@pku.edu.cn", "new@gmail.com"]);
    assert_eq!(p.homepages, ["https://example.cn"]);
    let second = apply(&db, &sheets, &Decisions::default()).await;
    assert_eq!((second.created, second.updated), (0, 0));
    let bad = table(&[
        &["姓名", "邮箱"],
        &["错误", "wang@pku.edu.cn;another@pku.edu.cn"],
    ]);
    let p = import::preview(&db, &bad, &Setup::default()).await.unwrap();
    assert!(p.errors[0].reason.contains("两位"));
    let separate = table(&[&["姓名", "学校"], &["李明", "北京大学"]]);
    assert_eq!(
        apply(
            &db,
            &separate,
            &Decisions {
                matches: vec![GroupChoice { row: 2, id: None }],
                ..Default::default()
            }
        )
        .await
        .created,
        1
    );
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn additions_conflicts_and_stale_decisions(db: SqlitePool) {
    let id = add(&db, "王明", &["wang@gmail.com"], "北京大学").await;
    let mut fields = MentorFields {
        name: "王明".into(),
        emails: vec!["wang@gmail.com".into()],
        research: Some("机器学习".into()),
        affiliations: vec![Affiliation {
            university: Some("北京大学".into()),
            title: Some("副教授".into()),
            ..Default::default()
        }],
        ..Default::default()
    };
    mentor::update(&db, id, &fields, 0).await.unwrap();
    let sheets = table(&[
        &[
            "姓名",
            "邮箱",
            "学校",
            "学院",
            "职称",
            "研究方向",
            "来源",
            "备注",
            "标签",
            "论文",
        ],
        &[
            "Ming Wang",
            "wang@gmail.com;wang@pku.edu.cn",
            "北京大学",
            "计算机学院",
            "教授",
            "多模态学习",
            "https://example.cn",
            "请联系",
            "优先;已收藏",
            "论文一",
        ],
    ]);
    let p = import::preview(&db, &sheets, &Setup::default())
        .await
        .unwrap();
    assert_eq!(p.differences.len(), 3);
    let done = apply(&db, &sheets, &Decisions::default()).await;
    assert_eq!(done.updated, 1);
    let m = mentor::get(&db, id).await.unwrap().unwrap();
    assert_eq!(m.name, "王明");
    assert_eq!(m.research.as_deref(), Some("机器学习"));
    assert_eq!(m.emails, ["wang@pku.edu.cn", "wang@gmail.com"]);
    assert_eq!(m.affiliations.len(), 1);
    assert_eq!(m.affiliations[0].school.as_deref(), Some("计算机学院"));
    assert_eq!(m.tags.len(), 2);
    // A changed local value invalidates only that decision, leaving the other choices usable.
    fields.emails = m.emails;
    fields.affiliations = m.affiliations;
    fields.research = Some("刚修改的方向".into());
    mentor::update(&db, id, &fields, 2).await.unwrap();
    assert_eq!(
        apply(
            &db,
            &sheets,
            &Decisions {
                take: p.differences,
                ..Default::default()
            }
        )
        .await
        .updated,
        1
    );
    let m = mentor::get(&db, id).await.unwrap().unwrap();
    assert_eq!(m.name, "Ming Wang");
    assert!(m.aliases.contains(&"王明".into()));
    assert_eq!(m.research.as_deref(), Some("刚修改的方向"));
    assert_eq!(m.affiliations[0].title.as_deref(), Some("教授"));
    let done = apply(&db, &sheets, &Decisions::default()).await;
    assert_eq!((done.created, done.updated), (0, 0));
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn trash_and_round_trip(db: SqlitePool) {
    let id = add(&db, "王明", &["w@a.cn"], "北京大学").await;
    mentor::trash::trash(&db, &[id], 1).await.unwrap();
    let sheets = table(&[
        &[
            "姓名",
            "邮箱",
            "学校",
            "学院",
            "学校 2",
            "邮箱 2",
            "别名",
            "主页",
            "研究方向",
            "来源网址",
            "代表论文",
            "标签",
            "备注",
        ],
        &[
            "王明",
            "w@a.cn",
            "北京大学",
            "计算机学院",
            "清华大学",
            "w@b.cn",
            "Ming",
            "https://example.cn",
            "多模态",
            "https://source.cn",
            "论文一\n论文二",
            "优先",
            "'=私人备注",
        ],
    ]);
    let p = import::preview(&db, &sheets, &Setup::default())
        .await
        .unwrap();
    assert_eq!(p.trashed.len(), 1);
    assert_eq!(apply(&db, &sheets, &Decisions::default()).await.updated, 0);
    assert_eq!(
        apply(
            &db,
            &sheets,
            &Decisions {
                restore: vec![id],
                ..Default::default()
            }
        )
        .await
        .updated,
        1
    );
    let rows = export::export(&db, &Scope::Ids(vec![id]), true)
        .await
        .unwrap();
    let bytes = sheet::write_csv(&rows);
    let exported = sheet::read("a.csv", &bytes).unwrap();
    assert_eq!(
        apply(&db, &exported, &Decisions::default()).await.updated,
        0
    );
    // Importing into an empty database also preserves all profile fields.
    let other = SqlitePool::connect("sqlite::memory:").await.unwrap();
    db::MIGRATOR.run(&other).await.unwrap();
    assert_eq!(
        apply(&other, &exported, &Decisions::default())
            .await
            .created,
        1
    );
    assert_eq!(
        export::export(&other, &Scope::Ids(vec![1]), true)
            .await
            .unwrap(),
        rows
    );
    other.close().await;
    let no_note = export::export(&db, &Scope::Ids(vec![id]), false)
        .await
        .unwrap();
    assert!(!no_note[0].contains(&"备注".into()));
    mentor::trash::trash(&db, &[id], 4).await.unwrap();
    assert_eq!(
        export::export(&db, &Scope::Ids(vec![id]), true)
            .await
            .unwrap()
            .len(),
        1
    );
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn failed_import_rolls_back_profiles_and_tags(db: SqlitePool) {
    let id = add(&db, "王明", &["w@a.cn"], "北京大学").await;
    sqlx::query("CREATE TRIGGER refuse_import BEFORE INSERT ON mentor WHEN new.name = '拒绝写入' BEGIN SELECT RAISE(ABORT, 'interrupted'); END").execute(&db).await.unwrap();
    let sheets = table(&[
        &["姓名", "邮箱", "备注", "标签"],
        &["王明", "w@a.cn", "新增备注", "新标签"],
        &["拒绝写入", "", "", ""],
    ]);
    assert!(
        import::import(&db, &sheets, &Setup::default(), &Decisions::default(), 1)
            .await
            .is_err()
    );
    let m = mentor::get(&db, id).await.unwrap().unwrap();
    assert_eq!(m.note, None);
    assert!(m.tags.is_empty());
    assert!(mentor::tags::list(&db).await.unwrap().is_empty());
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn unrelated_edits_do_not_invalidate_a_choice_and_whitespace_is_ignored(db: SqlitePool) {
    let id = add(&db, "Ming  Wang", &[], "北京大学").await;
    let sheets = table(&[
        &["姓名", "学校", "邮箱", "研究方向", "备注", "来源网址"],
        &[
            "Ming Wang",
            "北京大学",
            "",
            "图神经网络",
            "联系前阅读论文",
            "https://example.cn",
        ],
    ]);
    let p = import::preview(&db, &sheets, &Setup::default())
        .await
        .unwrap();
    assert_eq!(p.maybe_same.len(), 1);
    assert!(p.differences.is_empty());
    apply(&db, &sheets, &Decisions::default()).await;
    let fields = MentorFields {
        name: "Ming  Wang".into(),
        emails: vec!["w@a.cn".into()],
        research: Some("另一方向".into()),
        affiliations: vec![Affiliation {
            university: Some("北京大学".into()),
            ..Default::default()
        }],
        ..Default::default()
    };
    mentor::update(&db, id, &fields, 1).await.unwrap();
    let sheets = table(&[
        &["姓名", "邮箱", "研究方向", "备注", "来源网址"],
        &[
            "Ming Wang",
            "w@a.cn",
            "图神经网络",
            "修改的备注",
            "https://new.cn",
        ],
    ]);
    let p = import::preview(&db, &sheets, &Setup::default())
        .await
        .unwrap();
    let mut fields = fields;
    fields.name = "王明".into();
    mentor::update(&db, id, &fields, 2).await.unwrap();
    apply(
        &db,
        &sheets,
        &Decisions {
            take: p.differences,
            ..Default::default()
        },
    )
    .await;
    let m = mentor::get(&db, id).await.unwrap().unwrap();
    assert_eq!(m.name, "王明");
    assert_eq!(m.research.as_deref(), Some("图神经网络"));
    assert_eq!(m.note.as_deref(), Some("修改的备注"));
    assert_eq!(m.source_url.as_deref(), Some("https://new.cn"));
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn export_uses_all_filtered_pages_and_list_order(db: SqlitePool) {
    let mut ids = Vec::new();
    for i in 0..55 {
        ids.push(add(&db, &format!("王明{i}"), &[], "北京大学").await);
    }
    let other = add(&db, "其他导师", &[], "清华大学").await;
    sqlx::query("UPDATE mentor SET match_state = NULL, match_score = 95, matched_at = 1, match_model = 'm' WHERE id = ?").bind(ids[54]).execute(&db).await.unwrap();
    mentor::trash::trash(&db, &[ids[0]], 1).await.unwrap();
    let rows = export::export(
        &db,
        &Scope::Filter(export::Filter {
            status: None,
            keyword: Some("北京大学".into()),
        }),
        true,
    )
    .await
    .unwrap();
    assert_eq!(rows.len(), 55);
    assert_eq!(rows[1][0], "王明54");
    assert_eq!(rows[2][0], "王明1");
    assert!(!rows.iter().any(|r| r[0] == "其他导师"));
    let selected = export::export(
        &db,
        &Scope::Ids(vec![other, ids[0], ids[1], ids[54]]),
        false,
    )
    .await
    .unwrap();
    assert_eq!(
        selected
            .iter()
            .skip(1)
            .map(|r| r[0].as_str())
            .collect::<Vec<_>>(),
        ["王明54", "王明1", "其他导师"]
    );
    let template = export::export(&db, &Scope::Ids(vec![]), true)
        .await
        .unwrap();
    assert_eq!(template.len(), 1);
    assert!(template[0].contains(&"职称".into()));
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn grouped_new_rows_keep_order_and_existing_units_round_trip(db: SqlitePool) {
    let sheets = table(&[
        &["姓名", "邮箱", "学校"],
        &["王明", "w@gmail.com", "北京大学"],
        &["Ming Wang", "w@gmail.com;w@pku.edu.cn", "清华大学"],
    ]);
    assert_eq!(apply(&db, &sheets, &Decisions::default()).await.created, 1);
    let mut m = mentor::get(&db, 1).await.unwrap().unwrap();
    assert_eq!(m.emails, ["w@gmail.com", "w@pku.edu.cn"]);
    assert_eq!(m.aliases, ["Ming Wang"]);
    m.affiliations.push(Affiliation {
        university: Some("北京大学".into()),
        school: Some("计算机学院".into()),
        ..Default::default()
    });
    mentor::update(
        &db,
        1,
        &MentorFields {
            name: m.name,
            emails: m.emails,
            aliases: m.aliases,
            affiliations: m.affiliations,
            ..Default::default()
        },
        2,
    )
    .await
    .unwrap();
    let rows = export::export(&db, &Scope::Ids(vec![1]), true)
        .await
        .unwrap();
    let sheets = sheet::read(
        "a.xlsx",
        &sheet::write_xlsx(&[Sheet {
            name: "导师".into(),
            rows,
        }])
        .unwrap(),
    )
    .unwrap();
    assert_eq!(apply(&db, &sheets, &Decisions::default()).await.updated, 0);
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn trashed_rows_are_one_mentor_and_skipped_as_a_group(db: SqlitePool) {
    add(&db, "无关导师", &[], "清华大学").await;
    let id = add(&db, "王明", &["w@a.cn"], "北京大学").await;
    mentor::trash::trash(&db, &[id], 1).await.unwrap();
    let sheets = table(&[
        &["姓名", "邮箱", "备注", "研究方向"],
        &["王明", "w@a.cn;new@a.cn", "选这位", ""],
        &["王明", "new@a.cn", "", "一起导入的研究"],
        &["王明", "w@a.cn", "", ""],
    ]);
    let p = import::preview(&db, &sheets, &Setup::default())
        .await
        .unwrap();
    assert_eq!(p.trashed.len(), 1);
    assert_eq!(p.trashed[0].rows, [2, 3, 4]);
    assert!(p.differences.is_empty());
    let skipped = apply(&db, &sheets, &Decisions::default()).await;
    assert_eq!((skipped.created, skipped.updated), (0, 0));
    assert!(
        mentor::get(&db, id)
            .await
            .unwrap()
            .unwrap()
            .research
            .is_none()
    );
    let restored = apply(
        &db,
        &sheets,
        &Decisions {
            restore: vec![id],
            ..Default::default()
        },
    )
    .await;
    assert_eq!((restored.created, restored.updated), (0, 1));
    let m = mentor::get(&db, id).await.unwrap().unwrap();
    assert!(!m.trashed);
    assert_eq!(m.research.as_deref(), Some("一起导入的研究"));
    assert_eq!(m.note.as_deref(), Some("选这位"));
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn restore_approval_does_not_follow_a_different_target(db: SqlitePool) {
    add(&db, "无关导师", &[], "清华大学").await;
    let a = add(&db, "王明", &["w@a.cn"], "北京大学").await;
    let b = add(&db, "王敏", &["b@a.cn"], "北京大学").await;
    mentor::trash::trash(&db, &[a, b], 1).await.unwrap();
    let sheets = table(&[&["姓名", "邮箱", "备注"], &["王明", "w@a.cn", "表格补充"]]);
    let p = import::preview(&db, &sheets, &Setup::default())
        .await
        .unwrap();
    assert_eq!(p.trashed[0].id, a);
    for (id, name, emails) in [
        (a, "王明", vec![]),
        (b, "王敏", vec!["b@a.cn".into(), "w@a.cn".into()]),
    ] {
        mentor::update(
            &db,
            id,
            &MentorFields {
                name: name.into(),
                emails,
                ..Default::default()
            },
            2,
        )
        .await
        .unwrap();
    }
    let result = apply(
        &db,
        &sheets,
        &Decisions {
            restore: vec![a],
            ..Default::default()
        },
    )
    .await;
    assert_eq!((result.created, result.updated), (0, 0));
    for id in [a, b] {
        let m = mentor::get(&db, id).await.unwrap().unwrap();
        assert!(m.trashed);
        assert!(m.note.is_none());
    }
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn shared_new_email_and_trash_separate(db: SqlitePool) {
    let id = add(&db, "王明", &[], "北京大学").await;
    mentor::trash::trash(&db, &[id], 1).await.unwrap();
    let sheets = table(&[
        &["姓名", "邮箱", "备注"],
        &["王明", "new@pku.edu.cn", "第一行备注"],
        &["王明", "new@pku.edu.cn", "第二行备注"],
    ]);
    let initial = import::preview(&db, &sheets, &Setup::default())
        .await
        .unwrap();
    assert_eq!(initial.maybe_same.len(), 1);
    assert_eq!(initial.maybe_same[0].rows, [2, 3]);
    assert_eq!(initial.trashed[0].rows, [2, 3]);
    let skipped = apply(&db, &sheets, &Decisions::default()).await;
    assert_eq!((skipped.created, skipped.updated), (0, 0));
    let choices = Decisions {
        matches: vec![GroupChoice { row: 2, id: None }],
        ..Default::default()
    };
    let p = import::preview_decisions(&db, &sheets, &Setup::default(), &choices)
        .await
        .unwrap();
    assert_eq!(p.created, 1);
    assert!(p.trashed.is_empty());
    assert_eq!(p.maybe_same.len(), 1);
    assert_eq!(p.maybe_same[0].rows, [2, 3]);
    let result = apply(&db, &sheets, &choices).await;
    assert_eq!((result.created, result.updated), (1, 0));
    assert!(mentor::get(&db, id).await.unwrap().unwrap().trashed);
    let rows = export::export(
        &db,
        &Scope::Filter(export::Filter {
            status: None,
            keyword: None,
        }),
        true,
    )
    .await
    .unwrap();
    assert_eq!(rows.len(), 2);
    assert!(rows[1].contains(&"第一行备注".to_owned()));
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn shared_email_must_precede_fuzzy_candidate(db: SqlitePool) {
    let a = add(&db, "王明", &[], "北京大学").await;
    let b = add(&db, "Ming Wang", &["wang@pku.edu.cn"], "北京大学").await;
    mentor::trash::trash(&db, &[a], 1).await.unwrap();
    let sheets = table(&[
        &["姓名", "邮箱", "备注"],
        &["王明", "new@pku.edu.cn", "补充资料"],
        &["Ming Wang", "new@pku.edu.cn;wang@pku.edu.cn", ""],
    ]);
    let p = import::preview(&db, &sheets, &Setup::default())
        .await
        .unwrap();
    assert!(p.errors.is_empty(), "{:?}", p.errors);
    assert!(p.maybe_same.is_empty());
    assert!(p.trashed.is_empty());
    let result = apply(&db, &sheets, &Decisions::default()).await;
    assert_eq!((result.created, result.updated), (0, 1));
    assert!(mentor::get(&db, a).await.unwrap().unwrap().trashed);
    let m = mentor::get(&db, b).await.unwrap().unwrap();
    assert_eq!(m.emails, ["wang@pku.edu.cn", "new@pku.edu.cn"]);
    assert_eq!(m.note.as_deref(), Some("补充资料"));
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn shared_email_chain_rejects_only_real_owner_conflicts(db: SqlitePool) {
    add(&db, "王明", &["w@a.cn"], "北京大学").await;
    add(&db, "李明", &["l@a.cn"], "北京大学").await;
    let sheets = table(&[
        &["姓名", "邮箱"],
        &["王明", "w@a.cn;new@a.cn"],
        &["中间行", "NEW@a.cn;another@a.cn"],
        &["李明", "another@a.cn;l@a.cn"],
        &["独立新导师", "other@a.cn"],
    ]);
    let p = import::preview(&db, &sheets, &Setup::default())
        .await
        .unwrap();
    assert_eq!(
        p.errors.iter().map(|e| e.row).collect::<Vec<_>>(),
        [2, 3, 4]
    );
    assert_eq!(p.created, 1);
    let result = apply(&db, &sheets, &Decisions::default()).await;
    assert_eq!((result.created, result.updated), (1, 0));
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn splitting_rows_recalculates_the_full_preview(db: SqlitePool) {
    let sheets = table(&[
        &["姓名", "学校", "邮箱", "职称"],
        &["王明", "北京大学", "a@a.cn", "教授"],
        &["王明", "北京大学", "b@a.cn", "研究员"],
    ]);
    let id = mentor::create(
        &db,
        &MentorFields {
            name: "王明".into(),
            affiliations: vec![Affiliation {
                university: Some("北京大学".into()),
                title: Some("讲师".into()),
                ..Default::default()
            }],
            ..Default::default()
        },
        0,
    )
    .await
    .unwrap();
    let first = import::preview(&db, &sheets, &Setup::default())
        .await
        .unwrap();
    assert_eq!(
        (
            first.created,
            first.differences[0].row,
            first.differences[0].incoming.as_str()
        ),
        (0, 2, "教授")
    );
    let mut choices = Decisions {
        matches: vec![GroupChoice { row: 2, id: None }],
        ..Default::default()
    };
    let next = import::preview_decisions(&db, &sheets, &Setup::default(), &choices)
        .await
        .unwrap();
    assert_eq!(
        (
            next.created,
            next.differences[0].row,
            next.differences[0].incoming.as_str()
        ),
        (1, 3, "研究员")
    );
    choices.take = next.differences;
    let result = apply(&db, &sheets, &choices).await;
    assert_eq!((result.created, result.updated), (1, 1));
    assert_eq!(
        mentor::get(&db, id).await.unwrap().unwrap().affiliations[0]
            .title
            .as_deref(),
        Some("研究员")
    );
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn valid_homepage_with_semicolon_survives_export_import(db: SqlitePool) {
    let url = "https://faculty.example.edu/profile;jsessionid=abc123";
    let fields = mentor::MentorForm {
        name: "王明".into(),
        aliases: vec![],
        emails: vec![],
        affiliations: vec![],
        homepages: vec![url.into()],
        research: String::new(),
    }
    .check()
    .unwrap();
    let id = mentor::create(&db, &fields, 0).await.unwrap();
    let rows = export::export(&db, &Scope::Ids(vec![id]), true)
        .await
        .unwrap();
    for name in ["导师.xlsx", "导师.csv"] {
        let bytes = if name.ends_with("xlsx") {
            sheet::write_xlsx(&[Sheet {
                name: "导师".into(),
                rows: rows.clone(),
            }])
            .unwrap()
        } else {
            sheet::write_csv(&rows)
        };
        let sheets = sheet::read(name, &bytes).unwrap();
        let preview = import::preview(&db, &sheets, &Setup::default())
            .await
            .unwrap();
        assert!(preview.errors.is_empty(), "{:?}", preview.errors);
        assert_eq!(preview.unchanged, 1);
    }
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn exported_papers_keep_semicolons_and_flatten_item_newlines(db: SqlitePool) {
    let id = add(&db, "王明", &["w@a.cn"], "北京大学").await;
    sqlx::query("UPDATE mentor_detail SET papers = ? WHERE mentor_id = ?")
        .bind(sqlx::types::Json(vec![
            "Learning; Reasoning, and Planning",
            "First line\r\nSecond line",
        ]))
        .bind(id)
        .execute(&db)
        .await
        .unwrap();
    let rows = export::export(&db, &Scope::Ids(vec![id]), true)
        .await
        .unwrap();
    for name in ["导师.xlsx", "导师.csv"] {
        let other = SqlitePool::connect("sqlite::memory:").await.unwrap();
        db::MIGRATOR.run(&other).await.unwrap();
        let bytes = if name.ends_with("xlsx") {
            sheet::write_xlsx(&[Sheet {
                name: "导师".into(),
                rows: rows.clone(),
            }])
            .unwrap()
        } else {
            sheet::write_csv(&rows)
        };
        let sheets = sheet::read(name, &bytes).unwrap();
        assert_eq!(
            apply(&other, &sheets, &Decisions::default()).await.created,
            1
        );
        assert_eq!(
            mentor::get(&other, 1).await.unwrap().unwrap().papers,
            [
                "Learning; Reasoning, and Planning",
                "First line Second line"
            ]
        );
        other.close().await;
    }
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn quotes_survive_xlsx_and_csv_round_trip(db: SqlitePool) {
    let id = add(&db, "'=王明", &[], "北京大学").await;
    sqlx::query("UPDATE mentor_detail SET note = ? WHERE mentor_id = ?")
        .bind("'=这是需要原样保留的备注")
        .bind(id)
        .execute(&db)
        .await
        .unwrap();
    let rows = export::export(&db, &Scope::Ids(vec![id]), true)
        .await
        .unwrap();
    for name in ["导师.xlsx", "导师.csv"] {
        let bytes = if name.ends_with("xlsx") {
            sheet::write_xlsx(&[Sheet {
                name: "导师".into(),
                rows: rows.clone(),
            }])
            .unwrap()
        } else {
            sheet::write_csv(&rows)
        };
        let sheets = sheet::read(name, &bytes).unwrap();
        let other = SqlitePool::connect("sqlite::memory:").await.unwrap();
        db::MIGRATOR.run(&other).await.unwrap();
        apply(&other, &sheets, &Decisions::default()).await;
        let m = mentor::get(&other, 1).await.unwrap().unwrap();
        assert_eq!(m.name, "'=王明", "{name}");
        assert_eq!(
            m.note.as_deref(),
            Some("'=这是需要原样保留的备注"),
            "{name}"
        );
        other.close().await;
    }
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn repeated_name_only_import_must_not_duplicate(db: SqlitePool) {
    let sheets = table(&[&["姓名", "研究方向"], &["王明", "图神经网络"]]);
    assert_eq!(apply(&db, &sheets, &Decisions::default()).await.created, 1);
    let p = import::preview(&db, &sheets, &Setup::default())
        .await
        .unwrap();
    assert_eq!(p.maybe_same.len(), 1);
    let result = apply(&db, &sheets, &Decisions::default()).await;
    assert_eq!((result.created, result.updated), (0, 0));
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn full_width_email_exports_round_trip_without_duplicates(db: SqlitePool) {
    let fields = mentor::MentorForm {
        name: "王伟".into(),
        aliases: vec![],
        emails: vec!["ｗａｎｇ@pku.edu.cn".into()],
        affiliations: vec![Affiliation {
            university: Some("北京大学".into()),
            school: Some("计算机学院".into()),
            ..Default::default()
        }],
        homepages: vec![],
        research: "图学习".into(),
    }
    .check()
    .unwrap();
    let id = mentor::create(&db, &fields, 0).await.unwrap();
    assert_eq!(
        mentor::get(&db, id).await.unwrap().unwrap().emails,
        ["wang@pku.edu.cn"]
    );
    let scope = Scope::Filter(export::Filter {
        status: None,
        keyword: None,
    });
    let rows = export::export(&db, &scope, true).await.unwrap();
    for name in ["导师.csv", "导师.xlsx"] {
        let bytes = if name.ends_with("csv") {
            sheet::write_csv(&rows)
        } else {
            sheet::write_xlsx(&[Sheet {
                name: "导师".into(),
                rows: rows.clone(),
            }])
            .unwrap()
        };
        let sheets = sheet::read(name, &bytes).unwrap();
        let preview = import::preview(&db, &sheets, &Setup::default())
            .await
            .unwrap();
        assert!(preview.errors.is_empty(), "{name}");
        assert_eq!(
            (preview.created, preview.updated, preview.unchanged),
            (0, 0, 1),
            "{name}"
        );
        assert!(preview.differences.is_empty(), "{name}");
        let result = apply(&db, &sheets, &Decisions::default()).await;
        assert_eq!((result.created, result.updated), (0, 0), "{name}");
        assert_eq!(
            export::export(&db, &scope, true).await.unwrap(),
            rows,
            "{name}"
        );
    }
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn full_width_name_exports_round_trip_without_duplicates(db: SqlitePool) {
    let fields = mentor::MentorForm {
        name: "Ｗａｎｇ Ｗｅｉ".into(),
        aliases: vec![],
        emails: vec![],
        affiliations: vec![Affiliation {
            university: Some("北京大学".into()),
            school: Some("计算机学院".into()),
            ..Default::default()
        }],
        homepages: vec![],
        research: "图学习".into(),
    }
    .check()
    .unwrap();
    let id = mentor::create(&db, &fields, 0).await.unwrap();
    let scope = Scope::Filter(export::Filter {
        status: None,
        keyword: None,
    });
    let rows = export::export(&db, &scope, true).await.unwrap();
    for name in ["导师.csv", "导师.xlsx"] {
        let bytes = if name.ends_with("csv") {
            sheet::write_csv(&rows)
        } else {
            sheet::write_xlsx(&[Sheet {
                name: "导师".into(),
                rows: rows.clone(),
            }])
            .unwrap()
        };
        let sheets = sheet::read(name, &bytes).unwrap();
        let preview = import::preview(&db, &sheets, &Setup::default())
            .await
            .unwrap();
        assert!(preview.errors.is_empty(), "{name}");
        assert_eq!(preview.maybe_same.len(), 1, "{name}");
        assert_eq!(preview.maybe_same[0].selected, Some(id), "{name}");
        assert_eq!(
            (preview.created, preview.updated, preview.unchanged),
            (0, 0, 1),
            "{name}"
        );
        assert!(preview.differences.is_empty(), "{name}");
        let result = apply(&db, &sheets, &Decisions::default()).await;
        assert_eq!((result.created, result.updated), (0, 0), "{name}");
        assert_eq!(
            export::export(&db, &scope, true).await.unwrap(),
            rows,
            "{name}"
        );

        let other = SqlitePool::connect("sqlite::memory:").await.unwrap();
        db::MIGRATOR.run(&other).await.unwrap();
        assert_eq!(
            apply(&other, &sheets, &Decisions::default()).await.created,
            1
        );
        assert_eq!(
            export::export(&other, &scope, true).await.unwrap(),
            rows,
            "{name}"
        );
        other.close().await;
    }
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn half_width_names_and_aliases_match_without_changing_full_width_values(db: SqlitePool) {
    let fields = mentor::MentorForm {
        name: "Ｗａｎｇ Ｗｅｉ".into(),
        aliases: vec!["Ｗｅｉ Ｗａｎｇ".into()],
        emails: vec![],
        affiliations: vec![Affiliation {
            university: Some("ＰＫＵ １".into()),
            title: Some("教授Ａ１".into()),
            ..Default::default()
        }],
        homepages: vec!["https://example.cn/ｗ１".into()],
        research: "方向Ａ１".into(),
    }
    .check()
    .unwrap();
    let id = mentor::create(&db, &fields, 0).await.unwrap();
    for name in ["Wang Wei", "Wei Wang"] {
        let sheets = table(&[
            &["姓名", "别名", "学校", "职称", "主页", "研究方向"],
            &[
                name,
                "Wei Wang",
                "PKU 1",
                "教授A1",
                "https://example.cn/w1",
                "方向A1",
            ],
        ]);
        let preview = import::preview(&db, &sheets, &Setup::default())
            .await
            .unwrap();
        assert!(preview.errors.is_empty(), "{name}");
        assert_eq!(preview.maybe_same.len(), 1, "{name}");
        assert_eq!(preview.maybe_same[0].selected, Some(id), "{name}");
        assert_eq!(preview.maybe_same[0].candidates[0].id, id, "{name}");
        let same_name = name == "Wang Wei";
        assert_eq!(
            (preview.created, preview.updated, preview.unchanged),
            (0, 0, u32::from(same_name)),
            "{name}"
        );
        assert_eq!(
            preview
                .differences
                .iter()
                .map(|d| d.field)
                .collect::<Vec<_>>(),
            if same_name { vec![] } else { vec![Field::Name] },
            "{name}"
        );
        let result = apply(&db, &sheets, &Decisions::default()).await;
        assert_eq!((result.created, result.updated), (0, 0), "{name}");
    }
    let saved = mentor::get(&db, id).await.unwrap().unwrap();
    assert_eq!(saved.name, fields.name);
    assert_eq!(saved.aliases, fields.aliases);
    assert_eq!(saved.affiliations, fields.affiliations);
    assert_eq!(saved.homepages, fields.homepages);
    assert_eq!(saved.research, fields.research);
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn namesake_exports_round_trip_without_crossing_profiles(db: SqlitePool) {
    let mut ids = Vec::new();
    for (school, research, homepage) in [
        ("计算机学院", "图学习", "https://cs.example.cn"),
        ("数学学院", "代数学", "https://math.example.cn"),
    ] {
        ids.push(
            mentor::create(
                &db,
                &MentorFields {
                    name: "王伟".into(),
                    affiliations: vec![Affiliation {
                        university: Some("北京大学".into()),
                        school: Some(school.into()),
                        ..Default::default()
                    }],
                    research: Some(research.into()),
                    homepages: vec![homepage.into()],
                    ..Default::default()
                },
                0,
            )
            .await
            .unwrap(),
        );
    }
    let rows = export::export(&db, &Scope::Ids(ids.clone()), true)
        .await
        .unwrap();
    for name in ["导师.csv", "导师.xlsx"] {
        let bytes = if name.ends_with("csv") {
            sheet::write_csv(&rows)
        } else {
            sheet::write_xlsx(&[Sheet {
                name: "导师".into(),
                rows: rows.clone(),
            }])
            .unwrap()
        };
        let sheets = sheet::read(name, &bytes).unwrap();
        let preview = import::preview(&db, &sheets, &Setup::default())
            .await
            .unwrap();
        assert!(preview.errors.is_empty(), "{name}");
        assert_eq!(preview.maybe_same.len(), 2, "{name}");
        for (group, id) in preview.maybe_same.iter().zip(&ids) {
            assert_eq!(group.selected, Some(*id), "{name}");
            assert_eq!(
                group.candidates.iter().map(|c| c.id).collect::<Vec<_>>(),
                ids,
                "{name}"
            );
        }
        assert_eq!(
            (preview.created, preview.updated, preview.unchanged),
            (0, 0, 2),
            "{name}"
        );
        assert!(preview.differences.is_empty(), "{name}");
        let result = apply(&db, &sheets, &Decisions::default()).await;
        assert_eq!((result.created, result.updated), (0, 0), "{name}");
        assert_eq!(
            export::export(&db, &Scope::Ids(ids.clone()), true)
                .await
                .unwrap(),
            rows,
            "{name}"
        );
    }
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn mixed_missing_email_import_is_unchanged_when_repeated(db: SqlitePool) {
    let sheets = table(&[
        &["姓名", "邮箱", "研究方向"],
        &["王明", "a@a.cn", ""],
        &["王明", "", "图学习"],
    ]);
    assert_eq!(apply(&db, &sheets, &Decisions::default()).await.created, 2);
    let rows = export::export(&db, &Scope::Ids(vec![1, 2]), true)
        .await
        .unwrap();
    let preview = import::preview(&db, &sheets, &Setup::default())
        .await
        .unwrap();
    assert_eq!(
        (preview.created, preview.updated, preview.unchanged),
        (0, 0, 2)
    );
    let result = apply(&db, &sheets, &Decisions::default()).await;
    assert_eq!((result.created, result.updated), (0, 0));
    assert_eq!(
        export::export(&db, &Scope::Ids(vec![1, 2]), true)
            .await
            .unwrap(),
        rows
    );
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn namesake_defaults_and_explicit_targets_keep_differences(db: SqlitePool) {
    let mut ids = Vec::new();
    for research in ["图学习", "代数学"] {
        ids.push(
            mentor::create(
                &db,
                &MentorFields {
                    name: "王伟".into(),
                    aliases: vec!["Wei Wang".into()],
                    research: Some(research.into()),
                    ..Default::default()
                },
                0,
            )
            .await
            .unwrap(),
        );
    }
    let sheets = table(&[
        &["姓名", "研究方向", "备注"],
        &["王伟", "图学习", ""],
        &["王伟", "数论", "第二位的备注"],
        &["王伟", "", ""],
    ]);
    let p = import::preview(&db, &sheets, &Setup::default())
        .await
        .unwrap();
    assert_eq!(
        p.maybe_same.iter().map(|m| m.selected).collect::<Vec<_>>(),
        [Some(ids[0]), None, None]
    );
    assert_eq!((p.created, p.updated, p.unchanged), (2, 0, 1));
    let mut choices = Decisions {
        matches: vec![
            GroupChoice {
                row: 3,
                id: Some(ids[1]),
            },
            GroupChoice {
                row: 4,
                id: Some(ids[0]),
            },
        ],
        ..Default::default()
    };
    let p = import::preview_decisions(&db, &sheets, &Setup::default(), &choices)
        .await
        .unwrap();
    assert_eq!((p.created, p.updated, p.unchanged), (0, 1, 1));
    assert_eq!(p.differences.len(), 1);
    assert_eq!(
        (
            p.differences[0].id,
            p.differences[0].row,
            p.differences[0].field
        ),
        (ids[1], 3, Field::Research)
    );
    assert_eq!(p.differences[0].incoming, "数论");
    choices.take = p.differences;
    let result = apply(&db, &sheets, &choices).await;
    assert_eq!((result.created, result.updated), (0, 1));
    assert_eq!(
        mentor::get(&db, ids[0])
            .await
            .unwrap()
            .unwrap()
            .research
            .as_deref(),
        Some("图学习")
    );
    let b = mentor::get(&db, ids[1]).await.unwrap().unwrap();
    assert_eq!(b.research.as_deref(), Some("数论"));
    assert_eq!(b.note.as_deref(), Some("第二位的备注"));
    let alias = table(&[&["姓名"], &["Wei Wang"]]);
    let p = import::preview(&db, &alias, &Setup::default())
        .await
        .unwrap();
    assert_eq!(p.maybe_same[0].candidates.len(), 2);
    assert_eq!(p.maybe_same[0].selected, None);
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn stale_namesake_target_recalculates_default_before_import(db: SqlitePool) {
    let a = add(&db, "王明", &[], "北京大学").await;
    let b = add(&db, "王明", &[], "北京大学").await;
    let sheets = table(&[
        &["姓名", "学校", "邮箱", "备注", "研究方向"],
        &["王明", "北京大学", "new@pku.edu.cn", "补充", ""],
        &["王明", "北京大学", "new@pku.edu.cn", "", "图学习"],
    ]);
    let choices = Decisions {
        matches: vec![GroupChoice {
            row: 2,
            id: Some(b),
        }],
        ..Default::default()
    };
    let p = import::preview_decisions(&db, &sheets, &Setup::default(), &choices)
        .await
        .unwrap();
    assert_eq!(p.maybe_same.len(), 1);
    assert_eq!(p.maybe_same[0].rows, [2, 3]);
    assert_eq!(p.maybe_same[0].selected, Some(b));
    assert_eq!((p.created, p.updated), (0, 1));
    mentor::update(
        &db,
        b,
        &MentorFields {
            name: "李明".into(),
            ..Default::default()
        },
        2,
    )
    .await
    .unwrap();
    let p = import::preview_decisions(&db, &sheets, &Setup::default(), &choices)
        .await
        .unwrap();
    assert_eq!(p.maybe_same[0].selected, Some(a));
    let result = apply(&db, &sheets, &choices).await;
    assert_eq!((result.created, result.updated), (0, 1));
    let m = mentor::get(&db, a).await.unwrap().unwrap();
    assert_eq!(m.emails, ["new@pku.edu.cn"]);
    assert_eq!(m.research.as_deref(), Some("图学习"));
    assert_eq!(m.note.as_deref(), Some("补充"));
    let m = mentor::get(&db, b).await.unwrap().unwrap();
    assert!(m.emails.is_empty());
    assert!(m.note.is_none());
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn list_fields_use_their_own_separators(db: SqlitePool) {
    let sheets = table(&[
        &["姓名", "邮箱", "主页", "别名", "标签", "代表论文"],
        &[
            "王明",
            "w@a.cn;w@b.cn，w@c.cn、w@d.cn w@e.cn\nw@f.cn",
            "https://a.cn/p;q https://b.cn；https://c.cn\nhttps://d.cn",
            "Ming Wang;小王、王老师\nWang Ming",
            "优先;夏令营、直博\n联系",
            "Learning; Reasoning, and Planning\nSecond Paper",
        ],
    ]);
    let p = import::preview(&db, &sheets, &Setup::default())
        .await
        .unwrap();
    assert!(p.errors.is_empty());
    apply(&db, &sheets, &Decisions::default()).await;
    let m = mentor::get(&db, 1).await.unwrap().unwrap();
    assert_eq!(
        m.emails,
        ["w@a.cn", "w@b.cn", "w@c.cn", "w@d.cn", "w@e.cn", "w@f.cn"]
    );
    assert_eq!(
        m.homepages,
        [
            "https://a.cn/p;q",
            "https://b.cn",
            "https://c.cn",
            "https://d.cn"
        ]
    );
    assert_eq!(m.aliases, ["Ming Wang", "小王", "王老师", "Wang Ming"]);
    assert_eq!(
        m.tags.iter().map(|t| t.name.as_str()).collect::<Vec<_>>(),
        ["优先", "夏令营", "直博", "联系"]
    );
    assert_eq!(
        m.papers,
        ["Learning; Reasoning, and Planning", "Second Paper"]
    );
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn candidate_index_matches_aliases_and_checks_school_and_email(db: SqlitePool) {
    let id = mentor::create(
        &db,
        &MentorFields {
            name: "王明".into(),
            aliases: vec!["Ming  Wang".into()],
            emails: vec!["local@a.cn".into()],
            affiliations: vec![Affiliation {
                university: Some("北京大学".into()),
                ..Default::default()
            }],
            ..Default::default()
        },
        0,
    )
    .await
    .unwrap();
    let sheets = table(&[
        &["姓名", "学校", "邮箱"],
        &["Ming Wang", "", ""],
        &["Ming Wang", "清华大学", ""],
        &["Ming Wang", "北京大学", "other@a.cn"],
        &["其他姓名", "北京大学", ""],
    ]);
    let p = import::preview(&db, &sheets, &Setup::default())
        .await
        .unwrap();
    assert_eq!(p.maybe_same.len(), 1);
    assert_eq!(
        (p.maybe_same[0].row, p.maybe_same[0].selected.unwrap()),
        (2, id)
    );
    assert_eq!(p.created, 3);
}
