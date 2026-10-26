use crate::{CallError, ErrorCode};
use std::{
    collections::BTreeMap,
    sync::{Arc, Mutex, MutexGuard},
    time::Duration,
};
use tokio::{
    sync::{Semaphore, watch},
    time::Instant,
};

#[derive(Clone)]
enum State {
    Known(bool),
    Pending(watch::Receiver<bool>),
    /// The lookup failed; lists do not ask again before then, so that their refreshes do not keep
    /// querying while offline.
    RetryAfter(Instant),
}

#[derive(Clone)]
pub struct MailDomains {
    results: Arc<Mutex<BTreeMap<String, State>>>,
    limit: Arc<Semaphore>,
}

impl Default for MailDomains {
    fn default() -> Self {
        Self {
            results: Default::default(),
            limit: Arc::new(Semaphore::new(8)),
        }
    }
}

impl MailDomains {
    fn results(&self) -> MutexGuard<'_, BTreeMap<String, State>> {
        self.results.lock().unwrap_or_else(|_| {
            unreachable!("mail domain updates cannot panic while holding the lock")
        })
    }

    /// The domain's state, starting a lookup unless one is under way or answered. A failed one is
    /// asked again when `retry` says so.
    fn request(&self, address: &str, retry: fn(Instant) -> bool) -> Option<(String, State)> {
        let (_, domain) = address.rsplit_once('@')?;
        let domain = domain.to_lowercase();
        let mut results = self.results();
        if let Some(state) = results.get(&domain)
            && !matches!(state, State::RetryAfter(at) if retry(*at))
        {
            return Some((domain, state.clone()));
        }
        // Pending carries no answer until the sender changes its value or closes on failure.
        let (answer, waiting) = watch::channel(false);
        let state = State::Pending(waiting);
        results.insert(domain.clone(), state.clone());
        let domains = self.clone();
        let queried = domain.clone();
        let address = address.to_owned();
        tokio::spawn(async move {
            let _permit = domains
                .limit
                .acquire()
                .await
                .unwrap_or_else(|_| unreachable!("mail domain capacity is never closed"));
            match check::mail_domain(&address).await {
                Ok(problem) => {
                    let servers = problem.is_none();
                    domains.results().insert(queried, State::Known(servers));
                    answer.send_replace(servers);
                }
                Err(error) => {
                    tracing::warn!("looking up a mail domain: {error}");
                    domains.results().insert(
                        queried,
                        State::RetryAfter(Instant::now() + Duration::from_secs(30)),
                    );
                }
            }
        });
        Some((domain, state))
    }

    /// Lists use only completed answers, leaving DNS to the background.
    pub(crate) fn known_or_start(&self, address: &str) -> Option<check::Problem> {
        let (domain, state) = self.request(address, |at| at <= Instant::now())?;
        matches!(state, State::Known(false)).then_some(check::Problem::NoMailServer { domain })
    }

    /// A letter being written asks only now and then, such as when it opens or the network returns.
    pub(crate) async fn check(&self, address: &str) -> Result<Option<check::Problem>, CallError> {
        let Some((domain, state)) = self.request(address, |_| true) else {
            return Ok(None);
        };
        let servers = match state {
            State::Known(servers) => servers,
            State::Pending(mut answer) => {
                answer.changed().await.map_err(|_| lookup_failed())?;
                *answer.borrow()
            }
            State::RetryAfter(_) => unreachable!("a failed lookup is always started again here"),
        };
        Ok((!servers).then_some(check::Problem::NoMailServer { domain }))
    }
}

fn lookup_failed() -> CallError {
    CallError {
        code: ErrorCode::External,
        message: "未能查询导师邮箱的域名。".into(),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::{Context, call};
    use serde_json::json;
    use sqlx::SqlitePool;

    #[sqlx::test(migrator = "db::MIGRATOR")]
    async fn known_mail_domains_count_in_task_lists_and_bulk_review(db: SqlitePool) {
        let ctx = Context::new(db, net::Net::new(vec![]).unwrap(), Default::default());
        // Keep the unknown domain in flight so this test never starts a real DNS lookup.
        let (_answer, waiting) = watch::channel(false);
        ctx.mail_domains.results().extend([
            ("missing.example".into(), State::Known(false)),
            ("mail.example".into(), State::Known(true)),
            ("unknown.example".into(), State::Pending(waiting)),
        ]);
        let mut ids = vec![];
        for (name, addresses) in [
            ("王伟", vec!["wang@MISSING.EXAMPLE", "wang@mail.example"]),
            ("李明", vec!["li@unknown.example"]),
            ("赵平", vec!["zhao@mail.example", "zhao@missing.example"]),
        ] {
            ids.push(
                mentor::create(
                    &ctx.db,
                    &mentor::MentorFields {
                        name: name.into(),
                        emails: addresses.into_iter().map(str::to_owned).collect(),
                        ..Default::default()
                    },
                    0,
                )
                .await
                .unwrap(),
            );
        }
        let template = template::create(
            &ctx.db,
            &template::TemplateForm {
                name: "首封".into(),
                subject: "申请".into(),
                body_md: "{导师姓名}老师：您好！".into(),
            }
            .check()
            .unwrap(),
            0,
        )
        .await
        .unwrap();
        let task = task::create(
            &ctx.db,
            &task::NewTask {
                name: "域名检查".into(),
                kind: task::Kind::First,
                writer: task::Writer::Template,
                ids: ids.clone(),
                schedule: email::schedule::Schedule::Now,
                template_id: Some(template),
                template_source_id: None,
                source_task_id: None,
                model_config_id: None,
                references: vec![],
                attachments: vec![],
            },
            0,
        )
        .await
        .unwrap();
        let all = call(
            &ctx,
            "listTaskDrafts",
            json!({"taskId":task,"filter":"all","page":1}),
        )
        .await
        .unwrap();
        assert_eq!(all["total"], 3);
        for row in all["drafts"].as_array().unwrap() {
            assert_eq!(row["problems"], usize::from(row["mentorId"] == ids[0]));
        }
        let filtered = call(
            &ctx,
            "listTaskDrafts",
            json!({"taskId":task,"filter":"problems","page":1}),
        )
        .await
        .unwrap();
        assert_eq!(filtered["total"], 1);
        assert_eq!(filtered["drafts"][0]["mentorId"], ids[0]);
        let pending = call(&ctx, "prepareTaskApproval", json!({"id":task}))
            .await
            .unwrap();
        assert_eq!(pending.as_array().unwrap().len(), 3);
        for row in pending.as_array().unwrap() {
            let draft = call(&ctx, "getTaskDraft", json!({"taskId":task,"id":row["id"]}))
                .await
                .unwrap();
            let mentor_id: i64 = serde_json::from_value(draft["mentorId"].clone()).unwrap();
            assert_eq!(row["problems"], usize::from(mentor_id == ids[0]));
        }
        assert_eq!(
            call(
                &ctx,
                "checkMailDomain",
                json!({"address":"another@Missing.Example"})
            )
            .await
            .unwrap(),
            json!({"kind":"no_mail_server","domain":"missing.example"})
        );
    }
}
