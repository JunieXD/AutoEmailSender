#[test]
fn an_address_drawn_as_a_picture_is_read() {
    let text = ocr::recognize(include_bytes!("email.png")).unwrap();
    assert_eq!(text, "wangxm@cs.example.edu");
}

#[test]
fn lines_on_a_transparent_background_are_read_one_by_one() {
    let text = ocr::recognize(include_bytes!("lines.png")).unwrap();
    assert_eq!(text, "电话：010-62751234\n邮箱：chen_jun@pku.edu.cn");
}

#[test]
fn what_is_not_an_image_fails() {
    assert!(ocr::recognize(b"<html></html>").is_err());
}
