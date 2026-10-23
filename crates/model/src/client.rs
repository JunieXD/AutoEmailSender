//! Calls to OpenAI-compatible APIs.

use std::time::Duration;

use net::Net;
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
    #[derive(serde::Deserialize)]
    struct Models {
        data: Vec<Model>,
    }
    #[derive(serde::Deserialize)]
    struct Model {
        id: String,
    }
    let request = net
        .http()
        .get(format!("{}/models", access.base_url))
        .bearer_auth(&access.api_key)
        .timeout(Duration::from_secs(20));
    let body = send(request, &access.base_url).await?;
    let models: Models =
        serde_json::from_str(&body).map_err(|e| ModelError::Broken(format!("model list: {e}")))?;
    let mut names: Vec<String> = models.data.into_iter().map(|m| m.id).collect();
    names.sort();
    Ok(names)
}

/// Asks the model for a one-word answer, which proves the address, the key and the model name.
pub async fn test(net: &Net, access: &Access, model: &str) -> Result<(), ModelError> {
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
    let reply = send(request, &access.base_url).await?;
    let answered = serde_json::from_str::<Value>(&reply)
        .is_ok_and(|r| r["choices"].as_array().is_some_and(|c| !c.is_empty()));
    if answered {
        Ok(())
    } else {
        Err(ModelError::Broken(format!(
            "unexpected reply: {}",
            excerpt(&reply)
        )))
    }
}

/// Sends the request; any status but success becomes the provider's refusal, in its words.
async fn send(request: reqwest::RequestBuilder, address: &str) -> Result<String, ModelError> {
    let unreachable = |source: reqwest::Error| {
        let address = address.to_owned();
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
        Ok(body)
    } else {
        Err(ModelError::Refused {
            status: status.as_u16(),
            reply: provider_words(&body),
        })
    }
}

/// The message in an error reply, which OpenAI-compatible APIs put in `error.message`.
fn provider_words(body: &str) -> String {
    serde_json::from_str::<Value>(body)
        .ok()
        .and_then(|v| {
            v["error"]["message"]
                .as_str()
                .or(v["message"].as_str())
                .map(str::to_owned)
        })
        .unwrap_or_else(|| excerpt(body))
}

fn excerpt(text: &str) -> String {
    text.chars().take(200).collect()
}
