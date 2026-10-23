//! The provider table: what differs between model providers lives here, as data
//! (docs/design.md#桌面端).

use serde::Serialize;
use serde_json::{Value, json};
use ts_rs::TS;

/// The provider id of a model configuration with an address the user filled in.
pub const CUSTOM: &str = "custom";

// Named apart from the mail providers for the UI.
#[derive(Debug, Serialize, TS)]
#[serde(rename_all = "camelCase")]
#[ts(rename = "ModelProvider")]
pub struct Provider {
    pub id: &'static str,
    pub name: &'static str,
    /// Where the user gets an API key.
    pub key_url: &'static str,
    #[serde(skip)]
    #[ts(skip)]
    pub base_url: &'static str,
    #[serde(skip)]
    #[ts(skip)]
    pub no_thinking: Option<NoThinking>,
}

/// How a provider is told not to think, where one way works for all its models. Most providers
/// also serve models that cannot stop thinking, such as GLM-5.3 and Kimi K3, so they get none.
#[derive(Debug, Clone, Copy)]
pub enum NoThinking {
    /// `"thinking": {"type": "disabled"}`
    ThinkingDisabled,
}

impl NoThinking {
    /// Adds the parameter to a chat request.
    pub(crate) fn apply(self, body: &mut Value) {
        match self {
            NoThinking::ThinkingDisabled => body["thinking"] = json!({"type": "disabled"}),
        }
    }
}

pub fn find(id: &str) -> Option<&'static Provider> {
    PROVIDERS.iter().find(|p| p.id == id)
}

pub static PROVIDERS: &[Provider] = &[
    Provider {
        id: "deepseek",
        name: "DeepSeek",
        key_url: "https://platform.deepseek.com/api_keys",
        base_url: "https://api.deepseek.com",
        no_thinking: Some(NoThinking::ThinkingDisabled),
    },
    Provider {
        id: "qwen",
        name: "通义千问（阿里云百炼）",
        key_url: "https://bailian.console.aliyun.com",
        base_url: "https://dashscope.aliyuncs.com/compatible-mode/v1",
        no_thinking: None,
    },
    Provider {
        id: "glm",
        name: "智谱 GLM",
        key_url: "https://bigmodel.cn/usercenter/proj-mgmt/apikeys",
        base_url: "https://open.bigmodel.cn/api/paas/v4",
        no_thinking: None,
    },
    Provider {
        id: "doubao",
        name: "豆包（火山方舟）",
        key_url: "https://console.volcengine.com/ark/region:ark+cn-beijing/apiKey",
        base_url: "https://ark.cn-beijing.volces.com/api/v3",
        no_thinking: None,
    },
    Provider {
        id: "kimi",
        name: "Kimi（月之暗面）",
        key_url: "https://platform.moonshot.cn/console/api-keys",
        base_url: "https://api.moonshot.cn/v1",
        no_thinking: None,
    },
    Provider {
        id: "siliconflow",
        name: "硅基流动",
        key_url: "https://cloud.siliconflow.cn/account/ak",
        base_url: "https://api.siliconflow.cn/v1",
        no_thinking: None,
    },
    Provider {
        id: "openai",
        name: "OpenAI",
        key_url: "https://platform.openai.com/api-keys",
        base_url: "https://api.openai.com/v1",
        no_thinking: None,
    },
    Provider {
        id: "gemini",
        name: "Google Gemini",
        key_url: "https://aistudio.google.com/apikey",
        base_url: "https://generativelanguage.googleapis.com/v1beta/openai",
        no_thinking: None,
    },
    Provider {
        id: "openrouter",
        name: "OpenRouter",
        key_url: "https://openrouter.ai/keys",
        base_url: "https://openrouter.ai/api/v1",
        no_thinking: None,
    },
];
