use objc2::{AnyThread, rc::autoreleasepool};
use objc2_foundation::{NSArray, NSData, NSDictionary, NSString};
use objc2_vision::{
    VNImageRequestHandler, VNRecognizeTextRequest, VNRequest, VNRequestTextRecognitionLevel,
};

/// Whether text can be recognized: always, as the app is built only for Apple silicon, which
/// starts at macOS 11, the first whose Vision reads Chinese.
pub fn available() -> bool {
    true
}

/// The text in an encoded image (PNG, JPEG, GIF and the like), one line of text to a line; empty
/// when it has none. Fails when the image cannot be decoded, or the system cannot read Chinese.
pub fn recognize(image: &[u8]) -> Result<String, String> {
    autoreleasepool(|_| {
        let request = VNRecognizeTextRequest::new();
        request.setRecognitionLevel(VNRequestTextRecognitionLevel::Accurate);
        request.setRecognitionLanguages(&NSArray::from_retained_slice(&[
            NSString::from_str("zh-Hans"),
            NSString::from_str("en-US"),
        ]));
        // Correction turns an address into the words it resembles.
        request.setUsesLanguageCorrection(false);
        let handler = VNImageRequestHandler::initWithData_options(
            VNImageRequestHandler::alloc(),
            &NSData::with_bytes(image),
            &NSDictionary::new(),
        );
        let requests: [&VNRequest; 1] = [&request];
        handler
            .performRequests_error(&NSArray::from_slice(&requests))
            .map_err(|e| e.localizedDescription().to_string())?;
        let Some(lines) = request.results() else {
            return Ok(String::new());
        };
        Ok(lines
            .iter()
            .filter_map(|line| line.topCandidates(1).firstObject())
            .map(|text| text.string().to_string())
            .collect::<Vec<_>>()
            .join("\n"))
    })
}
