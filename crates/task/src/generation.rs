//! The background draft queue shares model capacity and failure policy with matching.
use super::*;
use model::{
    Chosen,
    background::{CONCURRENCY, Failure, Limit},
};
use std::{
    collections::{HashMap, HashSet},
    sync::Arc,
    time::Duration,
};
use tokio::{sync::Notify, task::JoinSet, time::Instant};
const RETRY: Duration = Duration::from_secs(5);
#[derive(Clone, Default)]
pub struct Generator(Arc<Notify>);
impl Generator {
    pub fn wake(&self) {
        self.0.notify_one();
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
    Done(writing::Letter, String),
    Failed(String),
    Pause(String, Option<Chosen>),
    RateLimit(Duration),
    Requeue,
}

async fn generate(
    db: &SqlitePool,
    net: &net::Net,
    claim: emails::Claim,
) -> std::result::Result<Outcome, sqlx::Error> {
    if !emails::valid(db, claim).await? {
        return Ok(Outcome::Requeue);
    }
    let mut tx = db.begin().await?;
    let Some(t) = sqlx::query!(
        "SELECT model_config_id,template_subject,template_body_md FROM task WHERE id = ?",
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
    let refs = sqlx::query_as!(material::Reference,r#"SELECT m.file_name,m.extracted_text AS "text!" FROM material m JOIN task_material t ON t.material_id = m.id WHERE t.task_id = ? AND t.role = 'reference' ORDER BY m.id"#,claim.task).fetch_all(&mut *tx).await?;
    let attachments = email::attached(&mut tx, claim.id).await?;
    tx.commit().await?;
    let Some(m) = mentor::get(db, claim.mentor).await? else {
        return Ok(Outcome::Requeue);
    };
    let me = profile::get(db).await?;
    let current = match (t.template_subject, t.template_body_md) {
        (Some(s), Some(b)) => fill(&s, &b, &m, me.name.as_deref()),
        _ => writing::Letter {
            subject: String::new(),
            body_md: String::new(),
        },
    };
    let answer = writing::write(
        db,
        net,
        claim.mentor,
        &current,
        &attachments,
        now(),
        writing::Basis {
            chosen: &chosen,
            references: &refs,
        },
    )
    .await;
    Ok(match answer {
        Ok(l) => Outcome::Done(l, chosen.model),
        Err(writing::Error::Model(error)) => match model::background::classify(error) {
            Failure::Pause(reason) => Outcome::Pause(reason, Some(chosen)),
            Failure::RateLimit(wait) => Outcome::RateLimit(wait),
            Failure::Failed(reason) => Outcome::Failed(reason),
        },
        Err(writing::Error::NotALetter { .. }) => {
            Outcome::Failed("模型未能写出完整的主题和正文，请重试或换个模型。".into())
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
        let id = sqlx::query_scalar!("SELECT model_config_id FROM task WHERE id = ?", claim.task)
            .fetch_optional(&mut *tx)
            .await?
            .flatten();
        let current = model::selected(&mut *tx, id).await?;
        if expected.is_none_or(|e| {
            current
                .as_ref()
                .is_some_and(|c| model::background::same_config(c, e))
        }) {
            sqlx::query!("UPDATE task SET state = 'paused', generation_paused_at = ?, generation_pause_reason = ? WHERE id = ? AND state = 'active' AND generation_paused_at IS NULL",at,reason,claim.task).execute(&mut *tx).await?;
        }
    }
    tx.commit().await?;
    Ok(true)
}

pub async fn run(
    db: SqlitePool,
    net: net::Net,
    generator: Generator,
    limit: Limit,
    changed: impl Fn() + Send + 'static,
    finished: impl Fn(DraftsFinished) + Send + 'static,
) {
    while let Err(e) = emails::recover(&db).await {
        tracing::error!("recover drafts: {e}");
        tokio::time::sleep(RETRY).await;
    }
    let mut workers = JoinSet::new();
    let mut claims = HashMap::new();
    let mut active = HashSet::new();
    let mut retry_at: Option<Instant> = None;
    loop {
        let tasks = match sqlx::query!(
            "SELECT DISTINCT task_id FROM email WHERE state = 'generating' AND task_id IS NOT NULL"
        )
        .fetch_all(&db)
        .await
        {
            Ok(rows) => rows,
            Err(e) => {
                tracing::error!("draft progress: {e}");
                tokio::time::sleep(RETRY).await;
                continue;
            }
        };
        for t in tasks {
            if let Some(id) = t.task_id {
                active.insert(id);
            }
        }
        for id in active.clone() {
            let result = sqlx::query!(r#"SELECT t.id,t.name,t.state,
                (SELECT count(*) FROM email WHERE task_id = t.id AND state = 'generating') AS remaining,
                (SELECT count(*) FROM email WHERE task_id = t.id AND model IS NOT NULL AND state NOT IN ('generating','generation_failed','canceled')) AS generated,
                (SELECT count(*) FROM email WHERE task_id = t.id AND state = 'generation_failed') AS failed
                FROM task t WHERE t.id = ?"#,id).fetch_optional(&db).await;
            match result {
                Ok(Some(t))
                    if t.remaining == 0
                        && !claims.values().any(|c: &emails::Claim| c.task == id) =>
                {
                    active.remove(&id);
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
                }
                Err(e) => tracing::error!("draft completion: {e}"),
                _ => {}
            }
        }
        let cooling = retry_at.is_some_and(|at| at > Instant::now());
        if !cooling && workers.len() < CONCURRENCY {
            match emails::next(&db).await {
                Ok(Some(claim)) => {
                    active.insert(claim.task);
                    let (db, net, limit) = (db.clone(), net.clone(), limit.clone());
                    let task = workers.spawn(async move {
                        let _permit = limit.acquire().await;
                        (claim, generate(&db, &net, claim).await)
                    });
                    claims.insert(task.id(), claim);
                    changed();
                    continue;
                }
                Ok(None) => {}
                Err(e) => {
                    tracing::error!("claim draft: {e}");
                    tokio::time::sleep(RETRY).await;
                    continue;
                }
            }
        }
        tokio::select! {
            Some(joined) = workers.join_next_with_id(), if !workers.is_empty() => {
                match joined {
                    Ok((worker,(claim,result))) => {
                        claims.remove(&worker);
                        let outcome = match result { Ok(o)=>o,Err(e)=>{tracing::error!("draft database: {e}");Outcome::Pause("草稿数据暂时无法读取，请继续或重启应用；仍然出错时，请导出日志。".into(),None)} };
                        let at=now();
                        let applied=loop {
                            let result=match &outcome {
                                Outcome::Done(l,model)=>emails::complete(&mut *connection(&db).await,claim,&l.subject,&l.body_md,model,at).await,
                                Outcome::Failed(reason)=>emails::fail(&mut *connection(&db).await,claim,reason,at).await,
                                Outcome::Pause(reason,config)=>requeue(&db,claim,Some((reason,config.as_ref())),at).await,
                                Outcome::RateLimit(_)|Outcome::Requeue=>requeue(&db,claim,None,at).await,
                            };
                            match result {Ok(a)=>break a,Err(e)=>{tracing::error!("save draft: {e}");tokio::time::sleep(RETRY).await;}}
                        };
                        if applied && let Outcome::RateLimit(wait)=outcome { let at=Instant::now()+wait;retry_at=Some(retry_at.map_or(at,|old|old.max(at))); }
                        changed();
                    }
                    Err(e)=>{
                        tracing::error!("draft worker: {e}");
                        if let Some(claim)=claims.remove(&e.id()) { while let Err(e)=requeue(&db,claim,Some(("草稿生成意外中断，请继续或重启应用。",None)),now()).await {tracing::error!("recover draft worker: {e}");tokio::time::sleep(RETRY).await;} }
                        changed();
                    }
                }
            },
            ()=generator.0.notified()=>{},
            ()=async {if let Some(at)=retry_at {tokio::time::sleep_until(at).await;}},if cooling=>{},
        }
    }
}
async fn connection(db: &SqlitePool) -> sqlx::pool::PoolConnection<sqlx::Sqlite> {
    loop {
        match db.acquire().await {
            Ok(c) => return c,
            Err(e) => {
                tracing::error!("draft connection: {e}");
                tokio::time::sleep(RETRY).await;
            }
        }
    }
}
fn now() -> i64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map_or(0, |d| d.as_millis() as i64)
}
