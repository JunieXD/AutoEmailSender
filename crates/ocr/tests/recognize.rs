#[test]
fn an_address_drawn_as_a_picture_is_read() {
    if !ocr::available() {
        // Windows Server, as on CI, can come without any recognition language.
        eprintln!("skipped: no text recognition language is installed");
        return;
    }
    let text = ocr::recognize(include_bytes!("email.png")).unwrap();
    assert_eq!(text, "wangxm@cs.example.edu");
}

#[test]
fn what_is_not_an_image_fails() {
    assert!(ocr::recognize(b"<html></html>").is_err());
}
