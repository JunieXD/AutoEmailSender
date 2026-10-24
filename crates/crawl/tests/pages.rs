#![cfg(test)]

use crawl::{
    BrowserReason, EmailLocation, PageUrl, Scope,
    fetch::{decode, response_material},
    parse,
};

fn url() -> PageUrl {
    PageUrl::parse("https://cs.pku.edu.cn/teachers/list.htm").unwrap()
}

#[test]
fn scope_allows_one_registrable_domain_and_only_evidenced_external_profiles() {
    let scope = Scope::new(&url());
    assert!(
        scope
            .page(PageUrl::parse("https://www.pku.edu.cn/a").unwrap())
            .is_ok()
    );
    assert!(
        scope
            .page(PageUrl::parse("https://pku.edu.cn.evil.com/a").unwrap())
            .is_err()
    );
    assert!(
        scope
            .page(PageUrl::parse("https://cs.tsinghua.edu.cn/a").unwrap())
            .is_err()
    );
    let external = PageUrl::parse("https://alice.github.io/profile").unwrap();
    let other = PageUrl::parse("https://bob.github.io/profile").unwrap();
    let material = parse(
        &url(),
        "<a href='https://alice.github.io/profile'>Alice</a>",
    )
    .unwrap();
    assert!(scope.linked_profile(&material, &external).is_ok());
    assert!(scope.linked_profile(&material, &other).is_err());
    assert_eq!(
        PageUrl::parse("HTTPS://CS.PKU.EDU.CN:443/a?page=2#part").unwrap(),
        PageUrl::parse("https://cs.pku.edu.cn/a?page=2").unwrap()
    );
    assert_ne!(
        PageUrl::parse("https://cs.pku.edu.cn/a?page=2").unwrap(),
        PageUrl::parse("https://cs.pku.edu.cn/a?page=3").unwrap()
    );
}

#[test]
fn literal_private_and_local_urls_are_rejected_without_dns() {
    for value in [
        "file:///etc/passwd",
        "ftp://example.edu/a",
        "http://localhost/a",
        "http://x.localhost./",
        "http://server/a",
        "http://printer.local/a",
        "http://127.1/",
        "http://2130706433/",
        "http://10.2.3.4/",
        "http://172.16.0.1/",
        "http://192.168.2.3/",
        "http://169.254.169.254/",
        "http://100.64.0.1/",
        "http://0.0.0.0/",
        "http://[::1]/",
        "http://[fe80::1]/",
        "http://[fc00::1]/",
        "http://[::ffff:127.0.0.1]/",
        "https://user:secret@example.edu/",
    ] {
        assert!(PageUrl::parse(value).is_err(), "{value}");
    }
    for value in [
        "https://8.8.8.8/",
        "https://[2606:4700:4700::1111]/",
        "https://unknown.school.edu.cn/",
    ] {
        assert!(PageUrl::parse(value).is_ok(), "{value}");
    }
}

#[test]
fn real_mcgill_cards_keep_each_name_and_email_together() {
    let base = PageUrl::parse("https://www.cs.mcgill.ca/people/faculty/").unwrap();
    let material = parse(&base, include_str!("pages/mcgill.html")).unwrap();
    let expectations = [
        ("David Adelani", "david.adelani@mcgill.ca"),
        ("Oana Balmau", "oana.balmau@cs.mcgill.ca"),
        ("Jackie Chi Kit Cheung", "jcheung@cs.mcgill.ca"),
    ];
    for (name, address) in expectations {
        let units: Vec<_> = material
            .units
            .iter()
            .filter(|u| u.text.contains(address))
            .collect();
        assert_eq!(units.len(), 1, "{address}");
        let unit = units[0];
        assert!(
            unit.is_card && unit.text.contains(name),
            "{name}: {}",
            unit.text
        );
        assert_eq!(unit.emails.len(), 1, "{}", unit.text);
        assert_eq!(unit.emails[0].address, address);
        let EmailLocation::Text { start, end } = unit.emails[0].location else {
            panic!("expected text evidence");
        };
        assert_eq!(&unit.text[start..end], address);
    }
    assert!(material.units.iter().filter(|u| u.is_card).count() >= 30);
}

#[test]
fn real_muc_cards_retain_onclick_and_image_only_profile_links() {
    let base = PageUrl::parse("https://xingong.muc.edu.cn/szdw/xyjs.htm").unwrap();
    let material = parse(&base, include_str!("pages/muc.html")).unwrap();
    let unit = material
        .units
        .iter()
        .find(|u| u.text.contains("毕晓君") && u.is_card)
        .unwrap();
    assert!(!unit.text.contains("陈波"));
    assert!(
        unit.links
            .iter()
            .any(|l| l.url.as_str() == "https://xingong.muc.edu.cn/info/1051/1715.htm")
    );
    assert!(unit.links.iter().any(|l| l.text == "毕晓君"));
}

#[test]
fn email_spellings_preserve_original_evidence_positions() {
    let cases = [
        ("a@school.edu.cn", "a@school.edu.cn"),
        ("B (at) school.edu.cn", "b@school.edu.cn"),
        ("c[at]school.edu.cn", "c@school.edu.cn"),
        ("d at school dot edu dot cn", "d@school.edu.cn"),
        ("邮箱：e#school.edu.cn", "e@school.edu.cn"),
        ("Email: spaced # school.edu.cn", "spaced@school.edu.cn"),
        ("电子邮箱\nline#school.edu.cn", "line@school.edu.cn"),
        ("ｆ＠ｓｃｈｏｏｌ．ｅｄｕ．ｃｎ", "f@school.edu.cn"),
        ("g(dot)h(at)school(dot)edu(dot)cn", "g.h@school.edu.cn"),
        ("i\u{200b}@school.edu.cn", "i@school.edu.cn"),
        ("ab#cd@school.edu.cn", "ab#cd@school.edu.cn"),
    ];
    for (raw, expected) in cases {
        let page = parse(&url(), &format!("<p>张老师 {raw}</p>")).unwrap();
        let unit = &page.units[0];
        assert_eq!(unit.emails.len(), 1, "{raw}");
        let evidence = &unit.emails[0];
        assert_eq!(evidence.address, expected, "{raw}");
        let EmailLocation::Text { start, end } = evidence.location else {
            panic!("expected text evidence");
        };
        assert!(
            raw.contains(unit.text[start..end].trim()),
            "{raw}: {}",
            &unit.text[start..end]
        );
    }
    let page = parse(&url(), "<p>article#school.edu.cn</p><script>fake@school.edu.cn</script><style>foo@school.edu.cn</style>").unwrap();
    assert!(page.units.iter().all(|u| u.emails.is_empty()));
}

#[test]
fn mailto_and_cloudflare_have_attribute_evidence() {
    let address = "protected@school.edu.cn";
    let encoded = format!(
        "42{}",
        address
            .bytes()
            .map(|b| format!("{:02x}", b ^ 0x42))
            .collect::<String>()
    );
    let page = parse(&url(), &format!(r#"<a href="MAILTO:First%40school.edu.cn,second@school.edu.cn?subject=ignore@evil.edu">联系</a><span data-cfemail="{encoded}">[email protected]</span><span data-cfemail="0zz"></span>"#)).unwrap();
    let unit = &page.units[0];
    for expected in ["first@school.edu.cn", "second@school.edu.cn", address] {
        assert!(
            unit.emails
                .iter()
                .any(|e| e.address == expected
                    && matches!(e.location, EmailLocation::Attribute { .. })),
            "{expected}"
        );
    }
    assert!(!unit.emails.iter().any(|e| e.address == "ignore@evil.edu"));
}

#[test]
fn real_toronto_profile_decodes_bracketed_at_and_dot() {
    let base = PageUrl::parse("https://www.cs.toronto.edu/~hinton/").unwrap();
    let material = parse(&base, include_str!("pages/toronto.html")).unwrap();
    assert!(
        material
            .units
            .iter()
            .flat_map(|u| &u.emails)
            .any(|e| e.address == "geoffrey.hinton@gmail.com")
    );
    assert!(
        material
            .units
            .iter()
            .flat_map(|u| &u.emails)
            .any(|e| e.address == "jennifer.cressman@utoronto.ca")
    );
}

#[test]
fn real_fzu_page_keeps_image_mailto_contacts() {
    let base = PageUrl::parse("https://chem.fzu.edu.cn/szdw/jsml1.htm").unwrap();
    let material = parse(&base, include_str!("pages/fzu.html")).unwrap();
    assert!(
        material
            .units
            .iter()
            .flat_map(|u| &u.emails)
            .any(|e| e.address == "xcwang@fzu.edu.cn"
                && matches!(e.location, EmailLocation::Attribute { .. }))
    );
}

#[test]
fn pagination_returns_safe_urls_and_marks_click_only_controls() {
    let page = parse(&url(), r#"<nav class="pagination"><a href="list.htm?page=2#top">下一页</a><a href="list.htm?page=3">3</a><a href="javascript:go(4)">4</a><button>下一页</button><a href="http://127.0.0.1/a?page=5">下一页</a><a href="https://other.edu/a?page=6">下一页</a></nav><a href="/other?page=7">更多</a><a href="/2026">2026</a>"#).unwrap();
    assert_eq!(
        page.pagination
            .urls
            .iter()
            .map(PageUrl::as_str)
            .collect::<Vec<_>>(),
        [
            "https://cs.pku.edu.cn/teachers/list.htm?page=2",
            "https://cs.pku.edu.cn/teachers/list.htm?page=3",
            "https://cs.pku.edu.cn/other?page=7"
        ]
    );
    assert!(page.pagination.needs_browser);
    assert_eq!(page.needs_browser, Some(BrowserReason::Pagination));
}

#[test]
fn real_seu_directory_exposes_url_pagination() {
    let base = PageUrl::parse("https://cyber.seu.edu.cn/yjsds_22371/list.htm").unwrap();
    let material = parse(&base, include_str!("pages/seu.html")).unwrap();
    assert!(
        material
            .pagination
            .urls
            .iter()
            .any(|u| u.as_str() == "https://cyber.seu.edu.cn/yjsds_22371/list2.htm")
    );
    assert!(
        material
            .pagination
            .urls
            .iter()
            .any(|u| u.as_str() == "https://cyber.seu.edu.cn/yjsds_22371/list4.htm")
    );
    assert!(!material.pagination.needs_browser);
}

#[test]
fn fallback_keeps_document_order_and_ignores_hidden_executable_content() {
    let page = parse(&url(), r#"<p>先读这里</p><a href="/image#x"><img alt="图片主页"></a><p>再读这里</p><div onclick="location.href='/profile'">静态主页</div><aside onclick="window.open('https://outside.edu/a')">不执行</aside><template>hidden@school.edu.cn</template><div hidden>hidden@school.edu.cn</div>"#).unwrap();
    assert_eq!(page.units.len(), 1);
    assert!(page.units[0].text.find("先读这里") < page.units[0].text.find("再读这里"));
    assert!(
        page.units[0]
            .links
            .iter()
            .any(|l| l.text == "图片主页" && l.url.as_str() == "https://cs.pku.edu.cn/image")
    );
    assert!(
        page.units[0]
            .links
            .iter()
            .any(|l| l.url.as_str() == "https://cs.pku.edu.cn/profile")
    );
    assert!(
        !page.units[0]
            .links
            .iter()
            .any(|l| l.url.as_str().contains("outside.edu"))
    );
    assert!(page.units[0].emails.is_empty());
}

#[test]
fn real_challenge_and_dynamic_directory_need_browser() {
    let challenge = response_material(
        &url(),
        412,
        Some("text/html"),
        include_bytes!("pages/bupt.html"),
    )
    .unwrap();
    assert_eq!(challenge.needs_browser, Some(BrowserReason::Challenge));
    let accepted = response_material(
        &url(),
        202,
        Some("text/html"),
        include_bytes!("pages/bupt.html"),
    )
    .unwrap();
    assert_eq!(accepted.needs_browser, Some(BrowserReason::Challenge));
    let dynamic = parse(&url(), include_str!("pages/fudan.html")).unwrap();
    assert_eq!(dynamic.needs_browser, Some(BrowserReason::JavaScript));
    let empty = parse(&url(), "<div id='app'></div><script src='a'></script><script src='b'></script><script src='c'></script>").unwrap();
    assert_eq!(empty.needs_browser, Some(BrowserReason::JavaScript));
    let encrypted = parse(
        &url(),
        "<p>导师</p><span _tsites_encrypt_field='_tsites_encrypt_field'>secret@fake.edu</span>",
    )
    .unwrap();
    assert_eq!(
        encrypted.needs_browser,
        Some(BrowserReason::EncryptedContent)
    );
    assert!(encrypted.units.iter().all(|u| u.emails.is_empty()));
}

#[test]
fn real_profile_text_survives_legacy_encoding_and_header_precedence() {
    let bytes = include_bytes!("pages/nju-gbk.html");
    for content_type in [
        None,
        Some("text/html; charset=gbk"),
        Some("text/html; charset=GB2312"),
    ] {
        let decoded = decode(bytes, content_type).unwrap();
        assert!(decoded.contains("周志华"));
        assert!(parse(&url(), &decoded).unwrap().text().contains("机器学习"));
    }
    let overridden = include_bytes!("pages/nju.html");
    assert!(
        decode(overridden, Some("text/html; charset=utf-8"))
            .unwrap()
            .contains("周志华")
    );
    assert!(response_material(&url(), 200, Some("application/pdf"), b"%PDF-1.7").is_err());
}
