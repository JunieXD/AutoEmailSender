//! The background draft queue shares model capacity and failure policy with matching.
use super::*;
use model::{
    Chosen,
    background::{CONCURRENCY, Cooldown, Failure, LASTING, Limit, Limited, RateLimit, Rounds},
};
use std::{
    collections::{HashMap, HashSet},
    sync::{
        Arc,
        atomic::{AtomicU64, Ordering},
    },
};
use tokio::{sync::Notify, task::JoinSet, time::Instant};
#[derive(Clone, Default)]
pub struct Generator {
    wake: Arc<Notify>,
    rounds: Arc<Rounds<i64>>,
}
impl Generator {
    pub fn wake(&self) {
        self.wake.notify_one();
    }
    /// The user resumed `task`.
    pub fn restart(&self, task: i64) {
        self.rounds.next(task);
        self.wake();
    }
    fn round(&self, task: i64) -> u64 {
        self.rounds.current(&task)
    }
}
#[derive(Debug, Clone, Serialize, TS)]
#[serde(rename_all = "camelCase")]
pub struct DraftsFinished {
    pub task_id: i64,
    pub name: String,
    pub generated: i64,
    pub failed: i64,
}
enum Outcome {
    Done {
        letter: writing::Letter,
        model: String,
        fact_check: Option<email::fact_check::FactCheck>,
        /// With the round the check's last request went out in.
        failure: Option<(Failure, Chosen, u64)>,
    },
    Failed(String),
    Pause(String, Option<Chosen>),
    /// With the round the limited request went out in.
    RateLimit(RateLimit, Chosen, u64),
    Requeue,
}

async fn generate(
    db: &SqlitePool,
    net: &net::Net,
    writes: &db::Writes,
    generator: &Generator,
    claim: emails::Claim,
) -> std::result::Result<Outcome, sqlx::Error> {
    if !emails::valid(db, claim).await? {
        return Ok(Outcome::Requeue);
    }
    let mut tx = db.begin().await?;
    let Some(t) = sqlx::query!(
        r#"SELECT kind AS "kind: Kind", model_config_id, template_subject,
            template_body_md
        FROM task
        WHERE id = ?"#,
        claim.task
    )
    .fetch_optional(&mut *tx)
    .await?
    else {
        return Ok(Outcome::Requeue);
    };
    let Some(chosen) = model::selected(&mut *tx, t.model_config_id).await? else {
        return Ok(Outcome::Pause(
            "尚未添加模型，请在设置中添加后继续。".into(),
            None,
        ));
    };
    if !chosen.agreed {
        return Ok(Outcome::Pause(
            "模型配置已改变，请先确认发给服务商的内容。".into(),
            Some(chosen),
        ));
    }
    let refs = sqlx::query_as!(
        material::Reference,
        r#"SELECT m.file_name, m.extracted_text AS "text!"
        FROM material m
        JOIN task_material t ON t.material_id = m.id
        WHERE t.task_id = ? AND t.role = 'reference'
        ORDER BY m.id"#,
        claim.task
    )
    .fetch_all(&mut *tx)
    .await?;
    let previous = if t.kind == Kind::FollowUp {
        Some(history(&mut tx, claim.mentor).await?)
    } else {
        None
    };
    let attachments = email::attached(&mut tx, claim.id).await?;
    let subject = if t.kind == Kind::FollowUp {
        sqlx::query_scalar!("SELECT subject FROM email WHERE id = ?", claim.id)
            .fetch_one(&mut *tx)
            .await?
    } else {
        None
    };
    tx.commit().await?;
    // The round the latest request went out in; the check asks again from inside.
    let sent = AtomicU64::new(0);
    let note_sent = || sent.store(generator.round(claim.task), Ordering::Relaxed);
    let answer = async {
        let input = writing::prepare(
            db,
            claim.mentor,
            &attachments,
            writing::Basis {
                chosen: &chosen,
                references: &refs,
                kind: previous
                    .as_deref()
                    .map_or(writing::Kind::First, writing::Kind::FollowUp),
            },
        )
        .await?;
        let current = match (t.template_subject, t.template_body_md) {
            (Some(subject), Some(body)) => {
                fill(&subject, &body, &input.mentor, input.me.name.as_deref())
            }
            _ => writing::Letter {
                subject: String::new(),
                body_md: String::new(),
            },
        };
        let at = now();
        note_sent();
        writing::write(net, &input, &current, |purpose, usage| {
            let chosen = &chosen;
            async move {
                writes
                    .retry("record draft usage", || {
                        model::usage::record_for_email(
                            db,
                            chosen.id,
                            &chosen.model,
                            purpose,
                            Some(claim.id),
                            usage,
                            at,
                        )
                    })
                    .await;
                Ok(())
            }
        })
        .await
    }
    .await;
    Ok(match answer {
        Ok(mut l) => {
            if let Some(subject) = subject {
                l.subject = subject;
            }
            // The settings, configuration or consent may have changed while writing the letter.
            let mut failure = None;
            let fact_check = if profile::ai_fact_check(db).await? {
                if !emails::valid(db, claim).await? {
                    return Ok(Outcome::Requeue);
                }
                match model::selected(db, t.model_config_id).await? {
                    Some(check_config) => {
                        let at = now();
                        note_sent();
                        let result = writing::fact_check::check(
                            db,
                            net,
                            claim.id,
                            &l,
                            &check_config,
                            &refs,
                            || async move {
                                let go_on = profile::ai_fact_check(db).await?
                                    && emails::valid(db, claim).await?;
                                note_sent();
                                Ok(go_on)
                            },
                            |usage| {
                                let chosen = &check_config;
                                async move {
                                    writes
                                        .retry("record fact check usage", || {
                                            model::usage::record_for_email(
                                                db,
                                                chosen.id,
                                                &chosen.model,
                                                model::usage::Purpose::FactCheck,
                                                Some(claim.id),
                                                usage,
                                                at,
                                            )
                                        })
                                        .await;
                                    Ok(())
                                }
                            },
                        )
                        .await;
                        match result {
                            // Stopped or paused while checking: as before the check.
                            Ok(None) if !emails::valid(db, claim).await? => {
                                return Ok(Outcome::Requeue);
                            }
                            Ok(findings) => findings.map(|findings| {
                                email::fact_check::FactCheck::Completed { findings }
                            }),
                            Err(writing::fact_check::Error::Db(error)) => return Err(error),
                            Err(writing::fact_check::Error::Model(error)) => {
                                let message = error.message();
                                let classified =
                                    model::background::classify(error, &check_config.access);
                                let reason = match &classified {
                                    Failure::Pause(reason) | Failure::Failed(reason) => {
                                        reason.clone()
                                    }
                                    Failure::RateLimit(_) => message,
                                };
                                failure =
                                    Some((classified, check_config, sent.load(Ordering::Relaxed)));
                                Some(email::fact_check::FactCheck::Failed { reason })
                            }
                            Err(error) => Some(email::fact_check::FactCheck::Failed {
                                reason: error.message(),
                            }),
                        }
                    }
                    None => Some(email::fact_check::FactCheck::Failed {
                        reason: writing::fact_check::Error::NoModel.message(),
                    }),
                }
            } else {
                None
            };
            Outcome::Done {
                letter: l,
                model: chosen.model,
                fact_check,
                failure,
            }
        }
        Err(writing::Error::Model(error)) => {
            tracing::warn!(
                email_id = claim.id,
                model = chosen.model,
                "draft model failed: {error}"
            );
            match model::background::classify(error, &chosen.access) {
                Failure::Pause(reason) => Outcome::Pause(reason, Some(chosen)),
                Failure::RateLimit(limit) => {
                    Outcome::RateLimit(limit, chosen, sent.load(Ordering::Relaxed))
                }
                Failure::Failed(reason) => Outcome::Failed(reason),
            }
        }
        Err(writing::Error::NotALetter { .. }) => {
            Outcome::Failed("模型未能写出完整的草稿，请重试或换个模型。".into())
        }
        Err(writing::Error::Db(e)) => return Err(e),
        Err(writing::Error::NoMentor) => Outcome::Requeue,
        Err(writing::Error::NoModel | writing::Error::NotAgreed) => {
            unreachable!("configuration checked above")
        }
    })
}

pub async fn requeue(
    db: &SqlitePool,
    claim: emails::Claim,
    pause: Option<(&str, Option<&Chosen>)>,
    at: i64,
) -> std::result::Result<bool, sqlx::Error> {
    let mut tx = db.begin().await?;
    if !emails::requeue(&mut tx, claim).await? {
        return Ok(false);
    }
    if let Some((reason, expected)) = pause {
        pause_task(&mut tx, claim.task, reason, expected, at).await?;
    }
    tx.commit().await?;
    Ok(true)
}

async fn pause_task(
    conn: &mut SqliteConnection,
    task: i64,
    reason: &str,
    expected: Option<&Chosen>,
    at: i64,
) -> std::result::Result<(), sqlx::Error> {
    let id = sqlx::query_scalar!("SELECT model_config_id FROM task WHERE id = ?", task)
        .fetch_optional(&mut *conn)
        .await?
        .flatten();
    let current = model::selected(&mut *conn, id).await?;
    if expected.is_none_or(|e| {
        current
            .as_ref()
            .is_some_and(|c| model::background::same_config(c, e))
    }) {
        sqlx::query!(
            r#"UPDATE task
            SET state = 'paused', generation_paused_at = ?,
                generation_pause_reason = ?
            WHERE id = ? AND state = 'active' AND generation_paused_at IS NULL"#,
            at,
            reason,
            task
        )
        .execute(conn)
        .await?;
    }
    Ok(())
}

async fn apply(
    db: &SqlitePool,
    claim: emails::Claim,
    outcome: &Outcome,
    at: i64,
) -> std::result::Result<bool, sqlx::Error> {
    match outcome {
        Outcome::Done {
            letter,
            model,
            fact_check,
            failure,
        } => {
            let mut tx = db.begin().await?;
            let applied = emails::complete(
                &mut tx,
                claim,
                &letter.subject,
                &letter.body_md,
                model,
                fact_check.as_ref(),
                at,
            )
            .await?;
            if applied && let Some((Failure::Pause(reason), config, _)) = failure {
                pause_task(&mut tx, claim.task, reason, Some(config), at).await?;
            }
            tx.commit().await?;
            Ok(applied)
        }
        Outcome::Failed(reason) => emails::fail(&mut *db.acquire().await?, claim, reason, at).await,
        Outcome::Pause(reason, config) => {
            requeue(db, claim, Some((reason, config.as_ref())), at).await
        }
        Outcome::RateLimit(..) | Outcome::Requeue => requeue(db, claim, None, at).await,
    }
}

pub async fn run(
    db: SqlitePool,
    writes: db::Writes,
    net: net::Net,
    generator: Generator,
    limit: Limit,
    changed: impl Fn() + Send + 'static,
    finished: impl Fn(DraftsFinished) + Send + 'static,
) {
    writes
        .retry("recover drafts", || emails::recover(&db))
        .await;
    let mut workers = JoinSet::new();
    let mut claims = HashMap::new();
    let mut active = HashSet::new();
    // Tasks share it: they can use the same model.
    let cooldown = Cooldown::new(generator.rounds.clone());
    // By task: tasks can use different models.
    let mut limited: HashMap<i64, Limited> = HashMap::new();
    loop {
        let tasks = writes
            .retry("draft progress", || {
                sqlx::query!(
                    r#"SELECT DISTINCT task_id
                    FROM email
                    WHERE state = 'generating' AND task_id IS NOT NULL"#
                )
                .fetch_all(&db)
            })
            .await;
        for t in tasks {
            if let Some(id) = t.task_id {
                active.insert(id);
            }
        }
        for id in active.clone() {
            let result = sqlx::query!(
                r#"SELECT t.id, t.name, t.state, (
                    SELECT count(*)
                    FROM email
                    WHERE task_id = t.id AND state = 'generating'
                ) AS remaining, (
                    SELECT count(*)
                    FROM email
                    WHERE task_id = t.id AND model IS NOT NULL
                    AND state NOT IN ('generating', 'generation_failed',
                        'canceled')
                ) AS generated, (
                    SELECT count(*)
                    FROM email
                    WHERE task_id = t.id AND state = 'generation_failed'
                ) AS failed
                FROM task t
                WHERE t.id = ?"#,
                id
            )
            .fetch_optional(&db)
            .await;
            match result {
                Ok(Some(t))
                    if t.remaining == 0
                        && !claims.values().any(|c: &emails::Claim| c.task == id) =>
                {
                    active.remove(&id);
                    limited.remove(&id);
                    if t.state != "stopped" && t.generated + t.failed > 0 {
                        finished(DraftsFinished {
                            task_id: t.id,
                            name: t.name,
                            generated: t.generated,
                            failed: t.failed,
                        });
                    }
                }
                Ok(None) => {
                    active.remove(&id);
                    limited.remove(&id);
                }
                Err(e) => tracing::error!("draft completion: {e}"),
                _ => {}
            }
        }
        let retry_at = cooldown.until();
        let cooling = retry_at.is_some();
        if !cooling
            && workers.len() < CONCURRENCY
            && let Some(claim) = writes.retry("claim draft", || emails::next(&db)).await
        {
            active.insert(claim.task);
            let (db, net, writes, limit) = (db.clone(), net.clone(), writes.clone(), limit.clone());
            let generator = generator.clone();
            let task = workers.spawn(async move {
                let _permit = limit.acquire().await;
                (claim, generate(&db, &net, &writes, &generator, claim).await)
            });
            claims.insert(task.id(), claim);
            changed();
            continue;
        }
        tokio::select! {
            Some(joined) = workers.join_next_with_id(), if !workers.is_empty() => {
                match joined {
                    Ok((worker, (claim, result))) => {
                        claims.remove(&worker);
                        let current = |round| round == generator.round(claim.task);
                        let outcome = match result {
                            Ok(Outcome::RateLimit(limit, chosen, round))
                                if current(round)
                                    && limited.entry(claim.task).or_default().lasting(
                                        round,
                                        Instant::now(),
                                        limit.wait,
                                        &chosen,
                                    ) =>
                            {
                                Outcome::Pause(limit.lasting, Some(chosen))
                            }
                            // The draft got through, which starts the limits afresh; the check
                            // after it was limited.
                            Ok(Outcome::Done {
                                letter,
                                model,
                                fact_check,
                                failure: Some((Failure::RateLimit(limit), config, round)),
                            }) if current(round) && limit.wait > LASTING =>
                            {
                                Outcome::Done {
                                    letter,
                                    model,
                                    fact_check,
                                    failure: Some((Failure::Pause(limit.lasting), config, round)),
                                }
                            }
                            Ok(o) => o,
                            Err(e) => {
                                tracing::error!("draft database: {e}");
                                Outcome::Pause(
                                    "草稿数据暂时无法读取，请继续或重启应用；\
                                    仍然出错时，请导出日志。"
                                        .into(),
                                    None,
                                )
                            }
                        };
                        let at = now();
                        let applied = writes
                            .retry("save draft", || apply(&db, claim, &outcome, at))
                            .await;
                        if applied && matches!(outcome, Outcome::Done { .. } | Outcome::Failed(_)) {
                            limited.remove(&claim.task);
                        }
                        // A limit from before the user went on does not hold the new round back
                        // either: its own requests find out whether it still holds.
                        if applied
                            && let Some((wait, round)) = match outcome {
                                Outcome::RateLimit(RateLimit { wait, .. }, _, round)
                                | Outcome::Done {
                                    failure:
                                        Some((Failure::RateLimit(RateLimit { wait, .. }), _, round)),
                                    ..
                                } if current(round) => Some((wait, round)),
                                _ => None,
                            }
                        {
                            cooldown.defer(claim.task, round, wait);
                        }
                        changed();
                    }
                    Err(e) => {
                        tracing::error!("draft worker: {e}");
                        if let Some(claim) = claims.remove(&e.id()) {
                            writes
                                .retry("recover draft worker", || {
                                    requeue(
                                        &db,
                                        claim,
                                        Some(("草稿生成意外中断，请继续或重启应用。", None)),
                                        now(),
                                    )
                                })
                                .await;
                        }
                        changed();
                    }
                }
            },
            () = generator.wake.notified() => {},
            () = async {
                if let Some(at) = retry_at {
                    tokio::time::sleep_until(at).await;
                }
            }, if cooling => {},
        }
    }
}
fn now() -> i64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map_or(0, |d| d.as_millis() as i64)
}
