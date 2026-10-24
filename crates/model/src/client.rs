//! Calls to OpenAI-compatible APIs.

use std::time::Duration;

use net::Net;
use serde::Deserialize;
use serde_json::{Value, json};
use tokio::time::timeout;

use crate::ModelError;
use crate::providers::NoThinking;
use crate::usage::Usage;

/// Where and how to call a provider.
#[derive(Debug)]
pub struct Access {
    /// Without a trailing slash.
    pub base_url: String,
    pub api_key: String,
    pub no_thinking: Option<NoThinking>,
}

/// The names of the models the provider offers, sorted.
pub async fn list_models(net: &Net, access: &Access) -> Result<Vec<String>, ModelError> {
    #[derive(Deserialize)]
    struct Models {
        data: Vec<Model>,
    }
    #[derive(Deserialize)]
    struct Model {
        id: String,
    }
    let request = net
        .http()
        .get(format!("{}/models", access.base_url))
        .bearer_auth(&access.api_key)
        .timeout(Duration::from_secs(20));
    let body = read(send(request, access).await?, access).await?;
    let models: Models =
        serde_json::from_str(&body).map_err(|e| ModelError::Broken(format!("model list: {e}")))?;
    let mut names: Vec<String> = models.data.into_iter().map(|m| m.id).collect();
    names.sort();
    Ok(names)
}

/// Gives up on a provider that has sent nothing for this long, however long its whole answer
/// takes: a model may think for minutes, streaming its thoughts as it goes.
const IDLE: Duration = Duration::from_secs(60);

/// What a model answered.
#[derive(Debug)]
pub struct Answer {
    /// May be empty, such as when the model spent all it may write on thinking.
    pub text: String,
    /// Why it stopped, such as "length" when it ran out of room.
    pub finish_reason: Option<String>,
    /// None when the provider did not say.
    pub usage: Option<Usage>,
}

/// Asks the model for a one-word answer, which proves the address, the key and the model name.
pub async fn test(net: &Net, access: &Access, model: &str) -> Result<(), ModelError> {
    let mut body = json!({
        "model": model,
        "messages": [{"role": "user", "content": "请只回复一个字：好"}],
        "stream": true,
    });
    if let Some(no_thinking) = access.no_thinking {
        no_thinking.apply(&mut body);
    }
    let answer = chat(net, access, body).await?;
    if answer.text.trim().is_empty() {
        return Err(ModelError::NoAnswer {
            finish_reason: answer.finish_reason,
        });
    }
    Ok(())
}

/// Asks the model to do what `system` says with what `user` gives it.
pub async fn ask(
    net: &Net,
    access: &Access,
    model: &str,
    system: &str,
    user: &str,
) -> Result<Answer, ModelError> {
    let body = json!({
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "stream": true,
        // A streamed reply leaves out the tokens it took unless asked.
        "stream_options": {"include_usage": true},
    });
    chat(net, access, body).await
}

/// Sends a chat request and puts the answer together. Streamed, as some models only think that
/// way and not all can stop thinking. What the provider wrote stays out of the errors, which go to
/// the log: it may be the user's letter.
async fn chat(net: &Net, access: &Access, body: Value) -> Result<Answer, ModelError> {
    #[derive(Deserialize)]
    struct Chunk {
        #[serde(default)]
        choices: Vec<Choice>,
        /// Some providers, such as OpenRouter, report failing partway here, after a success status.
        error: Option<Failure>,
        usage: Option<Tokens>,
    }
    #[derive(Deserialize)]
    struct Failure {
        message: String,
    }
    #[derive(Deserialize)]
    struct Choice {
        #[serde(default)]
        delta: Delta,
        finish_reason: Option<String>,
        /// Where Kimi reports the tokens.
        usage: Option<Tokens>,
    }
    #[derive(Default, Deserialize)]
    struct Delta {
        content: Option<String>,
    }
    /// Counted in whole or not at all: some count only the total.
    #[derive(Deserialize)]
    struct Tokens {
        prompt_tokens: Option<i64>,
        completion_tokens: Option<i64>,
        prompt_tokens_details: Option<Details>,
        /// DeepSeek's name for the cached tokens.
        prompt_cache_hit_tokens: Option<i64>,
        /// Kimi's.
        cached_tokens: Option<i64>,
    }
    #[derive(Deserialize)]
    struct Details {
        cached_tokens: Option<i64>,
    }
    let request = net
        .http()
        .post(format!("{}/chat/completions", access.base_url))
        .bearer_auth(&access.api_key)
        .json(&body);
    let mut response = send(request, access).await?;
    let (mut text, mut finish_reason, mut tokens) = (String::new(), None, None);
    let (mut pending, mut events) = (Vec::new(), 0);
    // Server-sent events, each a `data:` line, until `data: [DONE]`: what may follow it, or a
    // connection kept open after it, is not part of the answer.
    'read: while more(&mut response, &mut pending, access).await? {
        while let Some(end) = pending.iter().position(|&b| b == b'\n') {
            let line: Vec<u8> = pending.drain(..=end).collect();
            let line = String::from_utf8_lossy(&line);
            let Some(data) = line.trim().strip_prefix("data:").map(str::trim) else {
                continue;
            };
            if data == "[DONE]" {
                break 'read;
            }
            events += 1;
            let chunk: Chunk = serde_json::from_str(data).map_err(|e| {
                ModelError::Broken(format!(
                    "event {events}: {:?} error at column {}",
                    e.classify(),
                    e.column()
                ))
            })?;
            if let Some(failure) = chunk.error {
                return Err(ModelError::Failed(quote(&failure.message, access)));
            }
            tokens = chunk.usage.or(tokens);
            // The last chunk may only count tokens.
            if let Some(choice) = chunk.choices.into_iter().next() {
                text.extend(choice.delta.content);
                finish_reason = choice.finish_reason.or(finish_reason);
                tokens = choice.usage.or(tokens);
            }
        }
    }
    if events == 0 {
        return Err(ModelError::Broken("not an event stream".into()));
    }
    let usage = tokens.and_then(|t| {
        Some(Usage {
            input: t.prompt_tokens?,
            cached: t
                .prompt_tokens_details
                .and_then(|d| d.cached_tokens)
                .or(t.prompt_cache_hit_tokens)
                .or(t.cached_tokens)
                .unwrap_or(0),
            output: t.completion_tokens?,
        })
    });
    Ok(Answer {
        text,
        finish_reason,
        usage,
    })
}

/// Sends the request; any status but success becomes the provider's refusal, in its words.
async fn send(
    request: reqwest::RequestBuilder,
    access: &Access,
) -> Result<reqwest::Response, ModelError> {
    let response = timeout(IDLE, request.send())
        .await
        .map_err(|_| timed_out(access))?
        .map_err(|e| unreachable(access, e))?;
    let status = response.status();
    if status.is_success() {
        return Ok(response);
    }
    let body = read(response, access).await?;
    // OpenAI-compatible APIs explain errors in `error.message`.
    let words = serde_json::from_str::<Value>(&body).ok().and_then(|v| {
        v["error"]["message"]
            .as_str()
            .or(v["message"].as_str())
            .map(str::to_owned)
    });
    Err(ModelError::Refused {
        status: status.as_u16(),
        reply: quote(words.as_deref().unwrap_or(&body), access),
    })
}

/// The whole body of the response.
async fn read(mut response: reqwest::Response, access: &Access) -> Result<String, ModelError> {
    let mut body = Vec::new();
    while more(&mut response, &mut body, access).await? {}
    Ok(String::from_utf8_lossy(&body).into_owned())
}

/// Adds the next piece of the body to `buffer`; false at its end. Gives up when the provider has
/// sent nothing for `IDLE`.
async fn more(
    response: &mut reqwest::Response,
    buffer: &mut Vec<u8>,
    access: &Access,
) -> Result<bool, ModelError> {
    let chunk = timeout(IDLE, response.chunk())
        .await
        .map_err(|_| timed_out(access))?
        .map_err(|e| unreachable(access, e))?;
    match chunk {
        Some(chunk) => {
            buffer.extend_from_slice(&chunk);
            Ok(true)
        }
        None => Ok(false),
    }
}

fn timed_out(access: &Access) -> ModelError {
    ModelError::TimedOut {
        address: access.base_url.clone(),
    }
}

fn unreachable(access: &Access, source: reqwest::Error) -> ModelError {
    if source.is_timeout() {
        timed_out(access)
    } else {
        ModelError::Unreachable {
            address: access.base_url.clone(),
            source,
        }
    }
}

/// Up to 200 characters of what the provider said, for the user and the log. Without the key, in
/// case the provider quotes it back.
fn quote(text: &str, access: &Access) -> String {
    let text = match access.api_key.as_str() {
        "" => text.to_owned(),
        key => text.replace(key, "***"),
    };
    text.chars().take(200).collect()
}
