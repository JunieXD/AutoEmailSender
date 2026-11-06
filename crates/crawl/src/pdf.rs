/// Characters of a PDF the model reads. A longer one keeps its beginning and its end, where a CV
/// gives its owner and how to reach them.
const MOST: usize = 8000;
const BEGINNING: usize = 6000;

pub(crate) fn is_pdf(bytes: &[u8]) -> bool {
    bytes.starts_with(b"%PDF-")
}

/// The text layer of a PDF, read as a material is; empty for a scan, which has none.
pub(crate) fn text(bytes: &[u8]) -> Result<String, String> {
    let text: Vec<char> = material::pdf_text(bytes)?
        .unwrap_or_default()
        .chars()
        .collect();
    Ok(if text.len() > MOST {
        format!(
            "{}\n……\n{}",
            String::from_iter(&text[..BEGINNING]),
            String::from_iter(&text[text.len() - (MOST - BEGINNING)..])
        )
    } else {
        text.into_iter().collect()
    })
}
