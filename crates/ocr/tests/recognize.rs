#[test]
fn an_address_drawn_as_a_picture_is_read() {
    let text = ocr::recognize(include_bytes!("email.png")).unwrap();
    assert_eq!(text, "wangxm@cs.example.edu");
}

#[test]
fn lines_on_a_transparent_background_are_read_one_by_one() {
    // A name above an address: narrower, close, but as tall.
    let text = ocr::recognize(include_bytes!("lines.png")).unwrap();
    assert_eq!(text, "陈军\n邮箱:chen_jun@pku.edu.cn");
}

#[test]
fn an_underscore_apart_from_the_letters_is_read_with_them() {
    let text = ocr::recognize(include_bytes!("underscore.png")).unwrap();
    assert_eq!(text, "Email: rnmo_li@uestc.edu.cn");
}

#[test]
fn a_rule_under_an_address_is_no_line_of_text() {
    let text = ocr::recognize(include_bytes!("rule.png")).unwrap();
    assert_eq!(text, "wangxm@cs.example.edu");
}

#[test]
fn a_line_of_small_text_under_a_large_title_is_a_line_of_its_own() {
    let text = ocr::recognize(include_bytes!("title.png")).unwrap();
    assert_eq!(text, "CONTACT\nwangxm@cs.example.edu");
    // A short address, narrower than a third of the title.
    let text = ocr::recognize(include_bytes!("short.png")).unwrap();
    assert_eq!(text, "CONTACT\nli@x.edu.cn");
}

#[test]
fn an_image_with_more_text_than_is_read_in_one_fails() {
    // Twenty-four bands, each as wide as a line is read.
    let stripes = image::GrayImage::from_fn(1600, 240, |_, y| {
        image::Luma([if y % 10 < 4 { 0 } else { 255 }])
    });
    let mut png = std::io::Cursor::new(Vec::new());
    stripes.write_to(&mut png, image::ImageFormat::Png).unwrap();
    assert!(ocr::recognize(png.get_ref()).is_err());
}

#[test]
fn what_is_not_an_image_fails() {
    assert!(ocr::recognize(b"<html></html>").is_err());
}
