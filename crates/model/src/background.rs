//! Shared model capacity and failure policy for persisted background queues.

use crate::{Access, Chosen, ModelError};
use std::{
    collections::HashMap,
    hash::Hash,
    sync::{Arc, Mutex, MutexGuard},
    time::Duration,
};
use tokio::{
    sync::{Notify, OwnedSemaphorePermit, Semaphore},
    time::Instant,
};

pub const CONCURRENCY: usize = 3;

/// Rate limits lasting longer than this with nothing getting through are taken as limits that
/// will not lift by themselves, such as a used-up plan, and pause the work.
pub const LASTING: Duration = Duration::from_secs(10 * 60);

#[derive(Clone)]
pub struct Limit(Arc<Semaphore>);
impl Default for Limit {
    fn default() -> Self {
        Self(Arc::new(Semaphore::new(CONCURRENCY)))
    }
}
impl Limit {
    /// A turn among the calls running at once. Take it before reading the model configuration, so
    /// a call that waited in line uses the configuration as it is when its turn comes.
    pub async fn acquire(&self) -> OwnedSemaphorePermit {
        self.0
            .clone()
            .acquire_owned()
            .await
            .unwrap_or_else(|_| unreachable!("capacity is never closed"))
    }
}

pub fn same_config(current: &Chosen, expected: &Chosen) -> bool {
    current.id == expected.id
        && current.model == expected.model
        && current.access.base_url == expected.access.base_url
        && current.access.api_key == expected.access.api_key
}

pub enum Failure {
    Pause(String),
    RateLimit(RateLimit),
    Failed(String),
}

#[derive(Debug)]
pub struct RateLimit {
    pub wait: Duration,
    /// The reason to pause with when the limits last.
    pub lasting: String,
}

/// How many times the user has started or resumed each piece of background work (`K`): rate
/// limits met in an earlier round, even by calls still running when it ended, do not count toward
/// pausing it.
#[derive(Default)]
pub struct Rounds<K> {
    rounds: Mutex<HashMap<K, u64>>,
    /// A round began, which may lift a cooldown being waited out.
    began: Notify,
}
impl<K: Eq + Hash> Rounds<K> {
    pub fn next(&self, key: K) {
        *self.rounds().entry(key).or_default() += 1;
        self.began.notify_waiters();
    }
    pub fn current(&self, key: &K) -> u64 {
        self.rounds().get(key).copied().unwrap_or_default()
    }
    fn rounds(&self) -> MutexGuard<'_, HashMap<K, u64>> {
        self.rounds
            .lock()
            .unwrap_or_else(|_| unreachable!("nothing panics while holding the lock"))
    }
}

/// Until when rate limits hold background work back. Each limit belongs to the piece of work
/// (`K`) and round whose call met it: once the user goes on with a piece, what it met before no
/// longer holds it back, while what other pieces met still does.
pub struct Cooldown<K> {
    rounds: Arc<Rounds<K>>,
    until: Arc<Mutex<HashMap<K, (u64, Instant)>>>,
}
impl<K> Clone for Cooldown<K> {
    fn clone(&self) -> Self {
        Self {
            rounds: self.rounds.clone(),
            until: self.until.clone(),
        }
    }
}
impl<K: Eq + Hash> Cooldown<K> {
    pub fn new(rounds: Arc<Rounds<K>>) -> Self {
        Self {
            rounds,
            until: Arc::default(),
        }
    }
    /// The round of `key` a call going out now belongs to.
    pub fn round(&self, key: &K) -> u64 {
        self.rounds.current(key)
    }
    /// When the last limit still holding back ends.
    pub fn until(&self) -> Option<Instant> {
        let now = Instant::now();
        let mut until = self.lock();
        until.retain(|key, &mut (round, at)| round == self.rounds.current(key) && at > now);
        until.values().map(|&(_, at)| at).max()
    }
    /// Holds back for `wait` from now, for a limit a call of `key` made in `round` met.
    pub fn defer(&self, key: K, round: u64, wait: Duration) {
        let at = Instant::now() + wait;
        let mut until = self.lock();
        let held = until.entry(key).or_insert((round, at));
        *held = if held.0 == round {
            (round, held.1.max(at))
        } else {
            (round, at)
        };
    }
    /// A turn among the calls running at once, once no limit holds back.
    pub async fn acquire(&self, limit: &Limit) -> OwnedSemaphorePermit {
        loop {
            // Made before looking, so that it hears a round that begins in between.
            let began = self.rounds.began.notified();
            if let Some(at) = self.until() {
                tokio::select! {
                    () = tokio::time::sleep_until(at) => {}
                    () = began => {}
                }
                continue;
            }
            let permit = limit.acquire().await;
            if self.until().is_none() {
                return permit;
            }
        }
    }
    fn lock(&self) -> MutexGuard<'_, HashMap<K, (u64, Instant)>> {
        self.until
            .lock()
            .unwrap_or_else(|_| unreachable!("nothing panics while holding the lock"))
    }
}

/// Rate limits one piece of background work has met in a round, with nothing getting through in
/// between. Whoever keeps it starts it afresh once something gets through, so that only time spent
/// waiting out limits counts.
#[derive(Default)]
pub struct Limited {
    round: u64,
    /// By configuration: work can run on two at once, and late answers can come through one the
    /// user has replaced since.
    waits: Vec<Waits>,
}
struct Waits {
    config: Chosen,
    /// Each stretch of time counted once however many limits asked for it.
    blocked: Duration,
    until: Instant,
}
impl Limited {
    /// Notes a limit met `now` through `config` that asks to wait `wait`, by a call made in the
    /// current `round`; true once the waiting through `config` is longer than `LASTING`.
    pub fn lasting(&mut self, round: u64, now: Instant, wait: Duration, config: &Chosen) -> bool {
        if self.round != round {
            *self = Limited {
                round,
                waits: Vec::new(),
            };
        }
        let i = self
            .waits
            .iter()
            .position(|w| same_config(&w.config, config))
            .unwrap_or_else(|| {
                self.waits.push(Waits {
                    config: config.clone(),
                    blocked: Duration::ZERO,
                    until: now,
                });
                self.waits.len() - 1
            });
        let w = &mut self.waits[i];
        let end = now + wait;
        if end > w.until {
            w.blocked += end - w.until.max(now);
            w.until = end;
        }
        w.blocked > LASTING
    }
}

/// How background work goes on after `error` from calling through `access`.
pub fn classify(error: ModelError, access: &Access) -> Failure {
    let message = error.message();
    let (status, words, code, retry_after) = match error {
        ModelError::MissingKey | ModelError::Unreachable { .. } | ModelError::TimedOut { .. } => {
            return Failure::Pause(message);
        }
        ModelError::Refused {
            status,
            reply,
            retry_after,
            code,
        } => (Some(status), reply, code, retry_after),
        ModelError::Failed(reply) => (None, reply, None, None),
        _ => return Failure::Failed(message),
    };
    let reply = format!("{} {words}", code.unwrap_or_default()).to_lowercase();
    if [
        "context_length",
        "context length",
        "maximum context",
        "too many tokens",
        "上下文长度",
    ]
    .iter()
    .any(|s| reply.contains(s))
    {
        return Failure::Failed(
            "资料超出了模型能读取的长度。减少参考材料，或换一个支持更长上下文的模型后重试。".into(),
        );
    }
    let balance = [
        "insufficient balance",
        "insufficient credits",
        "credit balance",
        "余额不足",
        "额度用完",
        "insufficient funds",
        "insufficient account balance",
        "余额已耗尽",
        "欠费",
        "arrearage",
        "account is in good standing",
        "overdue",
        "free trial quota",
    ];
    // What OpenAI says when the balance is used up, and some providers when a limit is hit.
    let quota = [
        "insufficient_quota",
        "quota exceeded",
        "exceeded your current quota",
        "exceeded_current_quota",
    ];
    let rate_limited = status == Some(429) && access.quota_is_rate_limit;
    if balance.iter().any(|s| reply.contains(s))
        || (!rate_limited && quota.iter().any(|s| reply.contains(s)))
    {
        Failure::Pause("模型服务商的账户余额或额度不足。到服务商网站充值或补充额度后继续。".into())
    } else if [
        "model_not_found",
        "model not found",
        "model does not exist",
        "模型不存在",
    ]
    .iter()
    .any(|s| reply.contains(s))
    {
        Failure::Pause(
            "服务商找不到这个模型。请在“设置 › AI › 模型”里检查模型名或更换模型后继续。".into(),
        )
    } else if status.is_some_and(|s| matches!(s, 401..=404))
        // Bailian and Ark answer 429 when the service was never ordered or a limit the user set
        // there is reached.
        || ["commoditynotpurchased", "budgetlimitexceeded", "setlimitexceeded"]
            .iter()
            .any(|s| reply.contains(s))
    {
        Failure::Pause(message)
    } else if status == Some(429) {
        Failure::RateLimit(RateLimit {
            // A provider asking for no wait at all is still limiting; a second keeps retries from
            // running back to back.
            wait: retry_after
                .unwrap_or(Duration::from_secs(30))
                .max(Duration::from_secs(1)),
            lasting: format!(
                "模型服务商限流超过 {} 分钟（服务商回复：{words}）。可能是套餐到期或用量到了上限，\
                到服务商网站查看，处理好后继续。",
                LASTING.as_secs() / 60
            ),
        })
    } else {
        Failure::Failed(message)
    }
}

/// Approximate tokens from CJK characters and groups of four other characters.
pub fn tokens(text: &str) -> i64 {
    let (mut cjk, mut other) = (0, 0);
    for c in text.chars() {
        if matches!(
            u32::from(c),
            0x2e80..=0x9fff | 0xac00..=0xd7af | 0xf900..=0xfaff | 0x20000..=0x323af
        ) {
            cjk += 1;
        } else {
            other += 1;
        }
    }
    cjk + (other + 3) / 4
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::Source;

    fn refused(status: u16, code: &str, reply: &str) -> ModelError {
        ModelError::Refused {
            status,
            reply: reply.into(),
            retry_after: None,
            code: Some(code.into()),
        }
    }

    #[test]
    fn rate_limits_pause_once_waiting_them_out_takes_over_ten_minutes() {
        let config = |key: &str| Chosen {
            id: 1,
            provider: "custom".into(),
            model: "m".into(),
            agreed: true,
            access: Source::check("custom", "https://relay.example.com/v1")
                .unwrap()
                .access(Some(key.into())),
        };
        let (a, b) = (config("k"), config("new key"));
        let start = Instant::now();
        let at = |secs| start + Duration::from_secs(secs);
        let secs = Duration::from_secs;

        // Retried as soon as each 30-second wait ends: the 21st limit passes ten minutes.
        let mut limited = Limited::default();
        let pauses: Vec<_> = (0..21)
            .map(|n| limited.lasting(0, at(n * 31), secs(30), &a))
            .collect();
        assert_eq!(pauses.iter().position(|&p| p), Some(20));

        assert!(!Limited::default().lasting(0, at(0), secs(600), &a));
        assert!(Limited::default().lasting(0, at(0), secs(601), &a));

        // Limits met together count their longest wait once.
        let mut limited = Limited::default();
        for wait in [590, 1, 0] {
            assert!(!limited.lasting(0, at(0), secs(wait), &a));
        }
        assert!(limited.lasting(0, at(591), secs(590), &a));

        // Waiting for a turn at the model between limits does not count.
        let mut limited = Limited::default();
        assert!(!limited.lasting(0, at(0), secs(300), &a));
        assert!(!limited.lasting(0, at(3600), secs(300), &a));

        // Each configuration counts its own waits, whichever answers last; a new round starts
        // afresh.
        let mut limited = Limited::default();
        assert!(!limited.lasting(0, at(0), secs(590), &b));
        assert!(!limited.lasting(0, at(1), secs(1), &a));
        assert!(!limited.lasting(0, at(591), secs(590), &a));
        assert!(limited.lasting(0, at(591), secs(30), &b));
        assert!(!limited.lasting(1, at(1200), secs(590), &b));
    }

    #[tokio::test(start_paused = true)]
    async fn going_on_with_one_piece_of_work_drops_only_the_limits_it_met_before() {
        let rounds = Arc::new(Rounds::default());
        let cooldown = Cooldown::new(rounds.clone());
        let secs = Duration::from_secs;
        let at = |wait| Instant::now() + secs(wait);
        cooldown.defer(1, 0, secs(590));
        cooldown.defer(1, 0, secs(10));
        cooldown.defer(2, 0, secs(60));
        assert_eq!(cooldown.until(), Some(at(590)));
        rounds.next(1);
        assert_eq!(cooldown.until(), Some(at(60)));
        cooldown.defer(1, 1, secs(90));
        assert_eq!(cooldown.until(), Some(at(90)));
        tokio::time::advance(secs(90)).await;
        assert_eq!(cooldown.until(), None);
    }

    #[tokio::test(start_paused = true)]
    async fn a_call_waiting_out_a_limit_stops_waiting_once_going_on_drops_it() {
        let rounds = Arc::new(Rounds::default());
        let cooldown = Cooldown::new(rounds.clone());
        cooldown.defer(1, 0, Duration::from_secs(590));
        cooldown.defer(2, 0, Duration::from_secs(60));
        let start = Instant::now();
        let waiting = tokio::spawn({
            let cooldown = cooldown.clone();
            async move {
                let _permit = cooldown.acquire(&Limit::default()).await;
                Instant::now()
            }
        });
        // Asleep until the 590 seconds are over.
        tokio::task::yield_now().await;
        rounds.next(1);
        // Woken, it still waits out the limit the other piece met.
        assert_eq!(waiting.await.unwrap(), start + Duration::from_secs(60));
    }

    #[test]
    fn a_used_up_balance_pauses_and_a_token_limit_waits() {
        let access = |provider| {
            Source::check(provider, "https://relay.example.com/v1")
                .unwrap()
                .access(Some("k".into()))
        };
        let quota = "You exceeded your current quota, please check your plan and billing details.";
        for (provider, error, expected) in [
            ("openai", refused(429, "insufficient_quota", quota), "pause"),
            ("custom", refused(429, "insufficient_quota", quota), "pause"),
            ("qwen", refused(429, "insufficient_quota", quota), "wait"),
            (
                "qwen",
                refused(
                    429,
                    "Throttling.AllocationQuota",
                    "Allocated quota exceeded, please increase your quota limit.",
                ),
                "wait",
            ),
            (
                "qwen",
                refused(
                    400,
                    "Arrearage",
                    "Access denied, please make sure your account is in good standing.",
                ),
                "pause",
            ),
            (
                "qwen",
                refused(429, "PrepaidBillOverdue", "The prepaid bill is overdue."),
                "pause",
            ),
            (
                "qwen",
                refused(
                    429,
                    "CommodityNotPurchased",
                    "Commodity has not purchased yet.",
                ),
                "pause",
            ),
            (
                "qwen",
                refused(
                    429,
                    "BudgetLimitExceeded",
                    "The budget configured in Budget Management has been exhausted.",
                ),
                "pause",
            ),
            (
                "doubao",
                refused(
                    429,
                    "SetLimitExceeded",
                    "Your account has reached the set inference limit for the model, and the model service has been paused.",
                ),
                "pause",
            ),
            (
                "doubao",
                refused(
                    429,
                    "QuotaExceeded",
                    "Your account has exhausted its free trial quota for the model.",
                ),
                "pause",
            ),
            (
                "doubao",
                refused(
                    429,
                    "RateLimitExceeded.EndpointTPMExceeded",
                    "TPM limit exceeded",
                ),
                "wait",
            ),
            (
                "deepseek",
                refused(402, "invalid_request_error", "Insufficient Balance"),
                "pause",
            ),
            (
                "glm",
                refused(429, "1113", "余额不足或无可用资源包,请充值。"),
                "pause",
            ),
            (
                "glm",
                refused(429, "1302", "您当前使用该API的并发数过高"),
                "wait",
            ),
        ] {
            let got = match classify(error, &access(provider)) {
                Failure::Pause(_) => "pause",
                Failure::RateLimit(_) => "wait",
                Failure::Failed(_) => "fail",
            };
            assert_eq!(got, expected, "{provider}");
        }
    }
}
