//! Borrowed provider wrappers; business parsers decide how many blocks and which fences to accept.

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
