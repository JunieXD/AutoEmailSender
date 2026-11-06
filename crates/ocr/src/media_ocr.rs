use windows::{
    Graphics::Imaging::BitmapDecoder,
    Media::Ocr::OcrEngine,
    Storage::Streams::{DataWriter, InMemoryRandomAccessStream},
};

/// Whether text can be recognized: one of the user's languages has recognition installed, which
/// comes with its language pack.
pub fn available() -> bool {
    OcrEngine::TryCreateFromUserProfileLanguages().is_ok()
}

/// The text in an encoded image (PNG, JPEG, GIF and the like), one line of text to a line; empty
/// when it has none. Fails when the image cannot be decoded.
pub fn recognize(image: &[u8]) -> Result<String, String> {
    read(image).map_err(|e| e.message())
}

fn read(image: &[u8]) -> windows::core::Result<String> {
    let stream = InMemoryRandomAccessStream::new()?;
    let writer = DataWriter::CreateDataWriter(&stream)?;
    writer.WriteBytes(image)?;
    writer.StoreAsync()?.join()?;
    writer.DetachStream()?;
    stream.Seek(0)?;
    let bitmap = BitmapDecoder::CreateAsync(&stream)?
        .join()?
        .GetSoftwareBitmapAsync()?
        .join()?;
    let result = OcrEngine::TryCreateFromUserProfileLanguages()?
        .RecognizeAsync(&bitmap)?
        .join()?;
    let lines = result
        .Lines()?
        .into_iter()
        .map(|line| line.Text().map(|text| text.to_string()))
        .collect::<windows::core::Result<Vec<_>>>()?;
    Ok(lines.join("\n"))
}
