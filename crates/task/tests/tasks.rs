#![cfg(test)]
use email::tasks::{self as queue, DraftState};
use mentor::MentorFields;
use sqlx::SqlitePool;
use task::*;
const T: i64 = 1_800_000_000_000;
async fn mentor(db: &SqlitePool, n: i64, email: bool) -> i64 {
    mentor::create(
        db,
        &MentorFields {
            name: format!("导师{n}"),
            emails: if email {
                vec![format!("m{n}@example.com")]
            } else {
                vec![]
            },
            ..Default::default()
        },
        T,
    )
    .await
    .unwrap()
}
async fn form(db: &SqlitePool, ids: Vec<i64>) -> NewTask {
    let t = template::create(
        db,
        &template::TemplateForm {
            name: "首封".into(),
            subject: "给{导师姓名}".into(),
            body_md: "{导师姓名}老师：我是{我的姓名}。".into(),
        }
        .check()
        .unwrap(),
        T,
    )
    .await
    .unwrap();
    NewTask {
        name: "首封 10月25日".into(),
        ids,
        writer: Writer::Template,
        template_id: Some(t),
        model_config_id: None,
        references: vec![],
        attachments: vec![],
    }
}
async fn items(db: &SqlitePool, task: i64) -> Vec<TaskDraft> {
    let page = drafts(
        db,
        &DraftQuery {
            task_id: task,
            filter: DraftFilter::All,
            page: std::num::NonZeroU32::MIN,
        },
        T,
    )
    .await
    .unwrap();
    let mut result = vec![];
    for d in page.drafts {
        result.push(
            draft(
                db,
                &TaskEmail {
                    task_id: task,
                    id: d.id,
                },
            )
            .await
            .unwrap()
            .unwrap(),
        );
    }
    result
}
fn approval(d: &TaskDraft) -> RenderedApproval {
    RenderedApproval {
        attachments: d.attachments.clone(),
        id: d.id,
        subject: d.subject.clone(),
        body_md: d.body_md.clone(),
        html: format!("<p>{}</p>", d.body_md),
        text: d.body_md.clone(),
    }
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn eligibility_is_explicit_and_contacted_mentors_remain_eligible(db: SqlitePool) {
    let mut ids = vec![];
    for n in 1..=6 {
        ids.push(mentor(&db, n, n != 1).await);
    }
    let f = form(&db, vec![ids[1]]).await;
    create(&db, &f, T).await.unwrap();
    email::save_first_draft(&db, ids[2], "申请", "老师好", &[], T)
        .await
        .unwrap();
    mentor::trash::trash(&db, &[ids[3]], T).await.unwrap();
    let a = email::Approval {
        subject: "申请",
        body_md: "您好",
        html: "<p>您好</p>",
        text: "您好",
        attachments: &[],
    };
    email::approve_first(&db, ids[4], &a, T).await.unwrap();
    sqlx::query("UPDATE email SET state='sent',from_address='me@example.com',to_address='m5@example.com',message_id='<sent@example.com>',sent_at=? WHERE mentor_id=?").bind(T).bind(ids[4]).execute(&db).await.unwrap();
    let f = form(&db, ids.clone()).await;
    let p = plan(&db, &f).await.unwrap();
    for id in &ids[..4] {
        assert!(
            p.mentors
                .iter()
                .find(|m| m.id == *id)
                .unwrap()
                .reason
                .is_some()
        );
    }
    let contacted = p.mentors.iter().find(|m| m.id == ids[4]).unwrap();
    assert_eq!(contacted.contact_count, 1);
    assert_eq!(contacted.last_contact_at, Some(T));
    assert!(contacted.reason.is_none());
    assert!(create(&db, &f, T).await.is_err());
    assert_eq!(
        list(
            &db,
            &TaskQuery {
                page: std::num::NonZeroU32::MIN
            }
        )
        .await
        .unwrap()
        .total,
        1
    );
}
#[sqlx::test(migrator = "db::MIGRATOR")]
async fn template_snapshot_and_unique_members_and_reapply_only_one(db: SqlitePool) {
    let a = mentor(&db, 1, true).await;
    let b = mentor(&db, 2, true).await;
    let f = form(&db, vec![a, b, a]).await;
    let task = create(&db, &f, T).await.unwrap();
    let ds = items(&db, task).await;
    assert_eq!(ds.len(), 2);
    assert!(ds.iter().all(|d| d.state == DraftState::Review));
    assert!(ds[0].body_md.starts_with("导师1老师："));
    template::update(
        &db,
        f.template_id.unwrap(),
        &template::TemplateForm {
            name: "改过".into(),
            subject: "新模板".into(),
            body_md: "另一份模板".into(),
        }
        .check()
        .unwrap(),
        T + 1,
    )
    .await
    .unwrap();
    for d in &ds {
        save(
            &db,
            &DraftEdit {
                task_id: task,
                id: d.id,
                subject: "人工主题".into(),
                body_md: "人工稿".into(),
                attachments: vec![],
            },
            T + 2,
        )
        .await
        .unwrap();
    }
    reapply(
        &db,
        &TaskEmail {
            task_id: task,
            id: ds[0].id,
        },
        T + 3,
    )
    .await
    .unwrap();
    let ds = items(&db, task).await;
    assert_eq!(ds[0].subject, "给导师1");
    assert_eq!(ds[1].subject, "人工主题");
    assert!(sqlx::query("INSERT INTO email (task_id,mentor_id,kind,state,created_at,updated_at) VALUES (?,?,'first','review',0,0)").bind(task).bind(a).execute(&db).await.is_err());
}
#[sqlx::test(migrator = "db::MIGRATOR")]
async fn group_approval_only_uses_listed_current_review_drafts(db: SqlitePool) {
    let a = mentor(&db, 1, true).await;
    let b = mentor(&db, 2, true).await;
    let c = mentor(&db, 3, true).await;
    let task = create(&db, &form(&db, vec![a, b, c]).await, T)
        .await
        .unwrap();
    let ds = items(&db, task).await;
    let request = TaskApprovals {
        task_id: task,
        emails: vec![approval(&ds[0])],
    };
    assert_eq!(approve(&db, &request, T).await.unwrap(), 1);
    assert_eq!(approve(&db, &request, T).await.unwrap(), 0);
    let request = TaskApprovals {
        task_id: task,
        emails: vec![approval(&ds[0]), approval(&ds[1])],
    };
    assert_eq!(approve(&db, &request, T).await.unwrap(), 1);
    let ds = items(&db, task).await;
    assert_eq!(ds[2].state, DraftState::Review);
    let stale = approval(&ds[2]);
    save(
        &db,
        &DraftEdit {
            task_id: task,
            id: ds[2].id,
            subject: "新主题".into(),
            body_md: "新人工稿".into(),
            attachments: vec![],
        },
        T,
    )
    .await
    .unwrap();
    assert!(
        approve(
            &db,
            &TaskApprovals {
                task_id: task,
                emails: vec![stale]
            },
            T
        )
        .await
        .is_err()
    );
    assert_eq!(items(&db, task).await[2].body_md, "新人工稿");
}
#[sqlx::test(migrator = "db::MIGRATOR")]
async fn pause_blocks_both_selection_and_final_handover_then_resume_sends_once(db: SqlitePool) {
    let pki = fakemail::pki();
    let (port, inbox) = fakemail::smtp(&pki, false).await;
    let server = |port| mailbox::Server {
        host: "localhost".into(),
        port,
        security: mailbox::Security::Tls,
    };
    let mailbox = mailbox::MailboxForm {
        address: fakemail::ADDRESS.into(),
        sender_name: "我".into(),
        provider: "custom".into(),
        smtp: server(port),
        imap: server(993),
        daily_limit: 50,
        min_interval_s: 60,
    };
    mailbox::create(&db, &mailbox.check().unwrap(), fakemail::SECRET, T)
        .await
        .unwrap();
    let a = mentor(&db, 1, true).await;
    let task = create(&db, &form(&db, vec![a]).await, T).await.unwrap();
    let ds = items(&db, task).await;
    pause(&db, task, T).await.unwrap();
    assert_eq!(
        approve(
            &db,
            &TaskApprovals {
                task_id: task,
                emails: vec![approval(&ds[0])]
            },
            T
        )
        .await
        .unwrap(),
        1
    );
    let net = net::Net::new(vec![pki.root.clone()]).unwrap();
    assert_eq!(
        send::step(&db, &net, std::path::Path::new("unused"), || T)
            .await
            .unwrap(),
        send::Next::Idle
    );
    assert!(
        email::start_sending(
            &mut db.acquire().await.unwrap(),
            ds[0].id,
            1,
            fakemail::ADDRESS,
            "<test@example.com>",
            T
        )
        .await
        .unwrap()
        .is_none()
    );
    resume(&db, task).await.unwrap();
    assert_eq!(
        send::step(&db, &net, std::path::Path::new("unused"), || T)
            .await
            .unwrap(),
        send::Next::Now
    );
    assert_eq!(
        send::step(&db, &net, std::path::Path::new("unused"), || T)
            .await
            .unwrap(),
        send::Next::Idle
    );
    assert_eq!(inbox.lock().unwrap().len(), 1);
}
#[sqlx::test(migrator = "db::MIGRATOR")]
async fn stop_remove_trash_and_delete_preserve_only_delivery_evidence(db: SqlitePool) {
    let a = mentor(&db, 1, true).await;
    let b = mentor(&db, 2, true).await;
    let task = create(&db, &form(&db, vec![a, b]).await, T).await.unwrap();
    let ds = items(&db, task).await;
    remove(
        &db,
        &TaskEmail {
            task_id: task,
            id: ds[0].id,
        },
        T,
    )
    .await
    .unwrap();
    assert_eq!(items(&db, task).await[0].error.as_deref(), Some("移出任务"));
    stop(&db, task, T).await.unwrap();
    resume(&db, task).await.unwrap();
    assert_eq!(
        get(&db, task).await.unwrap().unwrap().state,
        TaskState::Stopped
    );
    assert_eq!(items(&db, task).await[1].error.as_deref(), Some("任务停止"));
    delete(&db, task).await.unwrap();
    assert_eq!(
        sqlx::query_scalar::<_, i64>("SELECT count(*) FROM email")
            .fetch_one(&db)
            .await
            .unwrap(),
        0
    );
    let task = create(&db, &form(&db, vec![a, b]).await, T).await.unwrap();
    let ds = items(&db, task).await;
    approve(
        &db,
        &TaskApprovals {
            task_id: task,
            emails: ds.iter().map(approval).collect(),
        },
        T,
    )
    .await
    .unwrap();
    // No mailbox row is required for an email's durable addresses.
    sqlx::query("UPDATE email SET state='sending',from_address='me@example.com',to_address='m1@example.com',message_id='<out@example.com>',sent_at=? WHERE id=?").bind(T).bind(ds[0].id).execute(&db).await.unwrap();
    assert!(delete(&db, task).await.is_err());
    assert_eq!(items(&db, task).await.len(), 2);
    email::sent(&mut db.acquire().await.unwrap(), ds[0].id, T)
        .await
        .unwrap();
    mentor::trash::trash(&db, &[b], T).await.unwrap();
    assert_eq!(items(&db, task).await[1].state, DraftState::Canceled);
    delete(&db, task).await.unwrap();
    assert_eq!(
        sqlx::query_scalar::<_, i64>(
            "SELECT count(*) FROM email WHERE task_id IS NULL AND state='sent'"
        )
        .fetch_one(&db)
        .await
        .unwrap(),
        1
    );
}
#[sqlx::test(migrator = "db::MIGRATOR")]
async fn stale_generation_results_cannot_revive_removed_or_regenerated_items(db: SqlitePool) {
    let a = mentor(&db, 1, true).await;
    let mut f = form(&db, vec![a]).await;
    let config=sqlx::query_scalar::<_,i64>("INSERT INTO model_config(provider,base_url,model,api_key,agreed,created_at) VALUES ('custom','http://localhost:1/v1','m','',1,0) RETURNING id").fetch_one(&db).await.unwrap();
    f.writer = Writer::Ai;
    f.model_config_id = Some(config);
    let task = create(&db, &f, T).await.unwrap();
    let old = queue::next(&db).await.unwrap().unwrap();
    queue::recover(&db).await.unwrap();
    let new = queue::next(&db).await.unwrap().unwrap();
    assert_ne!(old, new);
    assert!(
        !queue::complete(&mut db.acquire().await.unwrap(), old, "旧", "旧稿", "m", T)
            .await
            .unwrap()
    );
    assert!(
        !generation::requeue(&db, old, Some(("旧错误", None)), T)
            .await
            .unwrap()
    );
    assert!(
        queue::complete(&mut db.acquire().await.unwrap(), new, "新", "新稿", "m", T)
            .await
            .unwrap()
    );
    regenerate(&db, task, Some(new.id), false, T).await.unwrap();
    let next = queue::next(&db).await.unwrap().unwrap();
    remove(
        &db,
        &TaskEmail {
            task_id: task,
            id: new.id,
        },
        T,
    )
    .await
    .unwrap();
    assert!(
        !queue::complete(
            &mut db.acquire().await.unwrap(),
            next,
            "迟到",
            "迟到稿",
            "m",
            T
        )
        .await
        .unwrap()
    );
    assert_eq!(
        get(&db, task).await.unwrap().unwrap().state,
        TaskState::Completed
    );
}
#[sqlx::test(migrator = "db::MIGRATOR")]
async fn task_name_checks_and_problem_counts_agree(db: SqlitePool) {
    sqlx::query("UPDATE profile SET name='林晓'")
        .execute(&db)
        .await
        .unwrap();
    let a = mentor(&db, 1, true).await;
    let b = mentor(&db, 2, true).await;
    let task = create(&db, &form(&db, vec![a, b]).await, T).await.unwrap();
    let ds = items(&db, task).await;
    let edit = DraftEdit {
        task_id: task,
        id: ds[0].id,
        subject: "致导师2".into(),
        body_md: "导师1老师：您好".into(),
        attachments: vec![],
    };
    save(&db, &edit, T).await.unwrap();
    let p = check(&db, &edit, T).await.unwrap();
    assert!(
        p.iter()
            .any(|p| matches!(p,check::Problem::OtherMentor{name} if name=="导师2"))
    );
    let page = drafts(
        &db,
        &DraftQuery {
            task_id: task,
            filter: DraftFilter::Problems,
            page: std::num::NonZeroU32::MIN,
        },
        T,
    )
    .await
    .unwrap();
    assert_eq!(page.total, 1);
    assert_eq!(page.drafts[0].problems, p.len());
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn regeneration_is_local_and_deleted_model_uses_the_new_default(db: SqlitePool) {
    let a = mentor(&db, 1, true).await;
    let b = mentor(&db, 2, true).await;
    let old=sqlx::query_scalar::<_,i64>("INSERT INTO model_config(provider,base_url,model,api_key,agreed,created_at) VALUES ('custom','http://localhost:1/v1','old','',1,0) RETURNING id").fetch_one(&db).await.unwrap();
    let new=sqlx::query_scalar::<_,i64>("INSERT INTO model_config(provider,base_url,model,api_key,agreed,created_at) VALUES ('custom','http://localhost:1/v1','new','',1,1) RETURNING id").fetch_one(&db).await.unwrap();
    let mut f = form(&db, vec![a, b]).await;
    f.writer = Writer::Ai;
    f.model_config_id = Some(old);
    let task = create(&db, &f, T).await.unwrap();
    for _ in 0..2 {
        let c = queue::next(&db).await.unwrap().unwrap();
        queue::complete(
            &mut db.acquire().await.unwrap(),
            c,
            "原主题",
            "人工稿",
            "old",
            T,
        )
        .await
        .unwrap();
    }
    let ds = items(&db, task).await;
    assert_eq!(
        regenerate(&db, task, Some(ds[0].id), false, T)
            .await
            .unwrap(),
        1
    );
    let after = items(&db, task).await;
    assert_eq!(after[0].state, DraftState::Generating);
    assert_eq!(after[1].state, DraftState::Review);
    assert_eq!(after[1].body_md, "人工稿");
    model::delete(&db, old).await.unwrap();
    assert_eq!(get(&db, task).await.unwrap().unwrap().model_config_id, None);
    assert_eq!(model::selected(&db, None).await.unwrap().unwrap().id, new);
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn uploaded_material_is_added_only_to_the_current_task_draft_without_overwriting_text(
    db: SqlitePool,
) {
    let a = mentor(&db, 1, true).await;
    let b = mentor(&db, 2, true).await;
    let task = create(&db, &form(&db, vec![a, b]).await, T).await.unwrap();
    let ds = items(&db, task).await;
    let material=sqlx::query_scalar::<_,i64>("INSERT INTO material(file_name,size,for_matching,for_writing,attach_by_default,created_at) VALUES('新材料.pdf',10,0,0,0,0) RETURNING id").fetch_one(&db).await.unwrap();
    save(
        &db,
        &DraftEdit {
            task_id: task,
            id: ds[0].id,
            subject: "新主题".into(),
            body_md: "上传时修改后的正文".into(),
            attachments: vec![],
        },
        T,
    )
    .await
    .unwrap();
    assert!(
        queue::attach_material(&db, task, ds[0].id, material)
            .await
            .unwrap()
    );
    let after = items(&db, task).await;
    assert_eq!(after[0].body_md, "上传时修改后的正文");
    assert_eq!(after[0].attachments, [material]);
    assert!(after[1].attachments.is_empty());
    remove(
        &db,
        &TaskEmail {
            task_id: task,
            id: ds[0].id,
        },
        T,
    )
    .await
    .unwrap();
    assert!(
        !queue::attach_material(&db, task, ds[0].id, material)
            .await
            .unwrap()
    );
}
