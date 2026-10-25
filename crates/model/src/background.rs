//! Shared model capacity and failure policy for persisted background queues.

use crate::{Chosen, ModelError};
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

pub fn classify(error: ModelError) -> Failure {
    let message = error.message();
    let (status, reply, retry_after) = match error {
        ModelError::Unreachable { .. } | ModelError::TimedOut { .. } => {
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
    if [
        "insufficient_quota",
        "insufficient balance",
        "insufficient credits",
        "credit balance",
        "余额不足",
        "额度用完",
        "quota exceeded",
        "exceeded your current quota",
        "insufficient funds",
        "insufficient account balance",
        "余额已耗尽",
    ]
    .iter()
    .any(|s| reply.contains(s))
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
    } else if status.is_some_and(|s| matches!(s, 401..=404)) {
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
        if matches!(u32::from(c), 0x2e80..=0x9fff | 0xac00..=0xd7af | 0xf900..=0xfaff | 0x20000..=0x323af)
        {
            cjk += 1;
        } else {
            other += 1;
        }
    }
    cjk + (other + 3) / 4
}
