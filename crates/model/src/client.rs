//! Calls to OpenAI-compatible APIs.

use std::time::Duration;

use net::Net;
use serde::Deserialize;
use serde_json::{Value, json};

use crate::ModelError;
use crate::providers::NoThinking;

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
    let body = send(request, access).await?;
    let models: Models =
        serde_json::from_str(&body).map_err(|e| ModelError::Broken(format!("model list: {e}")))?;
    let mut names: Vec<String> = models.data.into_iter().map(|m| m.id).collect();
    names.sort();
    Ok(names)
}

/// Asks the model for a one-word answer, which proves the address, the key and the model name.
pub async fn test(net: &Net, access: &Access, model: &str) -> Result<(), ModelError> {
    #[derive(Deserialize)]
    struct Completion {
        choices: Vec<Choice>,
    }
    #[derive(Deserialize)]
    struct Choice {
        message: Message,
        finish_reason: Option<String>,
    }
    #[derive(Deserialize)]
    struct Message {
        content: Option<String>,
    }
    let mut body = json!({
        "model": model,
        "messages": [{"role": "user", "content": "请只回复一个字：好"}],
    });
    if let Some(no_thinking) = access.no_thinking {
        no_thinking.apply(&mut body);
    }
    let request = net
        .http()
        .post(format!("{}/chat/completions", access.base_url))
        .bearer_auth(&access.api_key)
        .json(&body)
        // Models that think first may take a while even for one word.
        .timeout(Duration::from_secs(60));
    let reply = send(request, access).await?;
    let completion: Completion = serde_json::from_str(&reply)
        .map_err(|_| ModelError::Broken(format!("unexpected reply: {}", quote(&reply, access))))?;
    match completion.choices.into_iter().next() {
        Some(Choice {
            message: Message {
                content: Some(answer),
            },
            ..
        }) if !answer.trim().is_empty() => Ok(()),
        choice => Err(ModelError::NoAnswer {
            finish_reason: choice.and_then(|c| c.finish_reason),
        }),
    }
}

/// Sends the request; any status but success becomes the provider's refusal, in its words.
async fn send(request: reqwest::RequestBuilder, access: &Access) -> Result<String, ModelError> {
    let unreachable = |source: reqwest::Error| {
        let address = access.base_url.clone();
        if source.is_timeout() {
            ModelError::TimedOut { address }
        } else {
            ModelError::Unreachable { address, source }
        }
    };
    let response = request.send().await.map_err(unreachable)?;
    let status = response.status();
    let body = response.text().await.map_err(unreachable)?;
    if status.is_success() {
        return Ok(body);
    }
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

/// Up to 200 characters of what the provider said, for the user and the log. Without the key, in
/// case the provider quotes it back.
fn quote(text: &str, access: &Access) -> String {
    let text = match access.api_key.as_str() {
        "" => text.to_owned(),
        key => text.replace(key, "***"),
    };
    text.chars().take(200).collect()
}
