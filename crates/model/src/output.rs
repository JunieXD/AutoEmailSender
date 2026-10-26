//! Typed response shapes and provider wrappers; business parsers decide which wrappers to accept.

use schemars::{
    JsonSchema, Schema,
    generate::SchemaSettings,
    transform::{RecursiveTransform, RestrictFormats},
};
use serde_json::{Value, json};

use crate::providers::Structured;

/// A response shape derived from the same type that deserializes the answer.
pub struct Structure {
    name: String,
    schema: Schema,
}

impl Structure {
    pub fn of<T: JsonSchema>() -> Self {
        let mut schema = SchemaSettings::draft2020_12()
            .with(|settings| {
                settings.inline_subschemas = true;
            })
            // Rust integer formats are not standard JSON Schema formats.
            .with_transform(RestrictFormats::default())
            .with_transform(RecursiveTransform(|schema: &mut Schema| {
                if let Some(properties) = schema.get("properties").and_then(Value::as_object) {
                    let required = properties.keys().cloned().collect::<Vec<_>>();
                    // Strict providers require every field, including nullable Option fields.
                    schema.insert("required".into(), json!(required));
                    schema.insert("additionalProperties".into(), json!(false));
                }
            }))
            .into_generator()
            .into_root_schema_for::<T>();
        schema.remove("$schema");
        Self {
            name: T::schema_name().into_owned(),
            schema,
        }
    }

    pub(crate) fn apply(&self, capability: Structured, body: &mut Value) {
        // Kimi JSON mode and OpenAI strict schemas require an object, but fact checks use arrays.
        if self.schema.get("type").is_none_or(|t| t != "object") {
            return;
        }
        body["response_format"] = match capability {
            Structured::JsonSchema => json!({
                "type": "json_schema",
                "json_schema": {"name": self.name, "strict": true, "schema": self.schema},
            }),
            Structured::JsonObject => json!({"type": "json_object"}),
        };
    }
}

/// Removes one leading thinking block, leaving any further blocks for the caller.
pub fn thinking_block(text: &str) -> Result<&str, &'static str> {
    let text = text.trim_start();
    match text.strip_prefix("<think>") {
        Some(rest) => rest
            .split_once("</think>")
            .map(|(_, text)| text.trim_start())
            .ok_or("思考块没有结束"),
        None => Ok(text),
    }
}

/// Removes consecutive leading thinking blocks.
pub fn thinking_blocks(text: &str) -> Result<&str, &'static str> {
    let mut text = text.trim_start();
    while text.starts_with("<think>") {
        text = thinking_block(text)?;
    }
    Ok(text)
}

/// The content after a leading code fence's first line, including its closing fence if any.
pub fn fence_content(text: &str) -> Result<Option<&str>, &'static str> {
    text.strip_prefix("```")
        .map(|rest| {
            rest.split_once('\n')
                .map(|(_, text)| text)
                .ok_or("代码围栏没有内容")
        })
        .transpose()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn wrappers_only_remove_leading_provider_syntax() {
        for (text, one, all) in [
            ("  {} ", "{} ", "{} "),
            ("<think>a</think> {}", "{}", "{}"),
            (
                "<think>a</think> \n<think>b</think>[]",
                "<think>b</think>[]",
                "[]",
            ),
            (
                "<body><think>x</think></body>",
                "<body><think>x</think></body>",
                "<body><think>x</think></body>",
            ),
            (
                "</think><subject>x</subject>",
                "</think><subject>x</subject>",
                "</think><subject>x</subject>",
            ),
        ] {
            assert_eq!(thinking_block(text), Ok(one));
            assert_eq!(thinking_blocks(text), Ok(all));
        }
        assert!(thinking_block("<think>unfinished").is_err());
        assert!(thinking_blocks("<think>a</think><think>unfinished").is_err());
        for (text, content) in [
            ("{}", None),
            ("```json\n{}\n```", Some("{}\n```")),
            ("```other\n[]", Some("[]")),
            ("{\"text\":\"```\"}", None),
        ] {
            assert_eq!(fence_content(text), Ok(content));
        }
        assert!(fence_content("```json").is_err());
    }
}
