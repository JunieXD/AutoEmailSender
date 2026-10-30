//! Shared model capacity and failure policy for persisted background queues.

use crate::{Access, Chosen, ModelError};
use std::{sync::Arc, time::Duration};
use tokio::sync::{OwnedSemaphorePermit, Semaphore};

pub const CONCURRENCY: usize = 3;

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
    RateLimit(Duration),
    Failed(String),
}

/// How background work goes on after `error` from calling through `access`.
pub fn classify(error: ModelError, access: &Access) -> Failure {
    let message = error.message();
    let (status, reply, retry_after) = match error {
        ModelError::MissingKey | ModelError::Unreachable { .. } | ModelError::TimedOut { .. } => {
            return Failure::Pause(message);
        }
        ModelError::Refused {
            status,
            reply,
            retry_after,
            code,
        } => (
            Some(status),
            format!("{} {reply}", code.unwrap_or_default()),
            retry_after,
        ),
        ModelError::Failed(reply) => (None, reply, None),
        _ => return Failure::Failed(message),
    };
    let reply = reply.to_lowercase();
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
        // Bailian answers 429 when the service was never ordered or the budget set there is used.
        || ["commoditynotpurchased", "budgetlimitexceeded"]
            .iter()
            .any(|s| reply.contains(s))
    {
        Failure::Pause(message)
    } else if status == Some(429) {
        Failure::RateLimit(retry_after.unwrap_or(Duration::from_secs(30)))
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
