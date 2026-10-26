#![cfg(test)]

use crawl::{EmailLocation, PageUrl, owns_email, parse};

fn url() -> PageUrl {
    PageUrl::parse("https://cs.pku.edu.cn/teachers/list.htm").unwrap()
}

fn check(html: &str, people: &[(&str, &[&str])]) {
    let page = parse(&url(), html).unwrap();
    let names: Vec<_> = people.iter().map(|(name, _)| *name).collect();
    for (name, own) in people {
        for (other, addresses) in people {
            for address in *addresses {
                assert_eq!(
                    owns_email(&page, &names, &[name], address),
                    name == other,
                    "{name}: {address}, {html}"
                );
            }
        }
        assert!(!owns_email(&page, &names, &[name], "absent@pku.edu.cn"));
        for address in *own {
            assert!(!owns_email(&page, &names, &["Absent"], address));
        }
    }
}

fn pairings(html: &str, names: [&str; 2], addresses: [&str; 2]) -> [[bool; 2]; 2] {
    let page = parse(&url(), html).unwrap();
    names.map(|name| addresses.map(|address| owns_email(&page, &names, &[name], address)))
}

#[test]
fn nested_contact_lists_and_divs_use_the_common_ancestor() {
    for html in [
        r#"<ul>
          <li><h2><a href="/a">Alice</a></h2><ul>
            <li><a href="mailto:a@pku.edu.cn">Email</a></li><li><a href="/a/home">Homepage</a></li>
          </ul></li>
          <li><h2><a href="/b">Bob</a></h2><ul>
            <li><a href="mailto:b@pku.edu.cn">Email</a></li><li><a href="/b/home">Homepage</a></li>
          </ul></li></ul>"#,
        r#"<section>
          <div><h2><a href="/a">Alice</a></h2><div><div><a href="mailto:a@pku.edu.cn">Email</a></div><div><a href="/a/home">Homepage</a></div></div></div>
          <div><h2><a href="/b">Bob</a></h2><div><div><a href="mailto:b@pku.edu.cn">Email</a></div><div><a href="/b/home">Homepage</a></div></div></div>
        </section>"#,
    ] {
        check(
            html,
            &[("Alice", &["a@pku.edu.cn"]), ("Bob", &["b@pku.edu.cn"])],
        );
    }
    check(
        "<article><p>a@pku.edu.cn</p><h2>Alice</h2></article>",
        &[("Alice", &["a@pku.edu.cn"])],
    );
}

#[test]
fn contact_labels_and_deep_tables_do_not_establish_identities() {
    for labels in [["Email", "Email"], ["Work email", "Personal email"]] {
        let rows = format!(
            r#"<li><h2>Alice</h2><ul><li>{}: a@pku.edu.cn</li><li>{}: alice@gmail.com</li></ul></li><li><h2>Bob</h2><ul><li>{}: b@pku.edu.cn</li><li>{}: bob@gmail.com</li></ul></li>"#,
            labels[0], labels[1], labels[0], labels[1]
        );
        for html in [
            format!("<ul>{rows}</ul>"),
            format!("<div>{rows}</div>")
                .replace("<li>", "<div><div>")
                .replace("</li>", "</div></div>"),
            format!("<table><tr><td><ul>{rows}</ul></td></tr></table>"),
        ] {
            check(
                &html,
                &[
                    ("Alice", &["a@pku.edu.cn", "alice@gmail.com"]),
                    ("Bob", &["b@pku.edu.cn", "bob@gmail.com"]),
                ],
            );
        }
    }
}

#[test]
fn columns_with_missing_emails_do_not_lend_contacts_to_neighbors() {
    for (bob_email, david_email) in [("", ""), ("<p>b@pku.edu.cn</p>", "<p>d@pku.edu.cn</p>")] {
        let html = format!(
            r#"<main>
          <section class="column"><h1>教授</h1>
            <article><h2>Alice</h2><p>a@pku.edu.cn</p></article><article><h2><a href="/b">Bob</a></h2>{bob_email}</article>
          </section>
          <section class="column"><h1>副教授</h1>
            <article><h2>Carol</h2><p>c@pku.edu.cn</p></article><article><h2><a href="/d">David</a></h2>{david_email}</article>
          </section></main>"#
        );
        check(
            &html,
            &[
                ("Alice", &["a@pku.edu.cn"]),
                (
                    "Bob",
                    if !bob_email.is_empty() {
                        &["b@pku.edu.cn"][..]
                    } else {
                        &[]
                    },
                ),
                ("Carol", &["c@pku.edu.cn"]),
                (
                    "David",
                    if !david_email.is_empty() {
                        &["d@pku.edu.cn"][..]
                    } else {
                        &[]
                    },
                ),
            ],
        );
    }
}

#[test]
fn table_rows_and_obfuscated_addresses_keep_ownership() {
    for (first, second) in [
        ("a[at]pku.edu.cn", "b[at]pku.edu.cn"),
        ("a at pku dot edu dot cn", "b at pku dot edu dot cn"),
        ("a（at）pku．edu．cn", "b（at）pku．edu．cn"),
    ] {
        for html in [
            format!("<ul><li>Alice {first}</li><li>Bob {second}</li></ul>"),
            format!(
                "<table><tr><td>Alice</td><td>{first}</td></tr><tr><td>Bob</td><td>{second}</td></tr></table>"
            ),
        ] {
            check(
                &html,
                &[("Alice", &["a@pku.edu.cn"]), ("Bob", &["b@pku.edu.cn"])],
            );
        }
    }
}

#[test]
fn flat_headings_and_contacts_keep_ownership() {
    for html in [
        "<main><h2>Alice</h2><p>a@pku.edu.cn</p><h2>Bob</h2><p>b@pku.edu.cn</p></main>",
        "<main><div><h2>Alice</h2></div><p>Research: AI</p><p><a href='mailto:a@pku.edu.cn'>联系</a></p><h2>Bob</h2><p><a href='mailto:b@pku.edu.cn'>联系</a></p></main>",
        "<main><h2>Alice</h2><p>a@pku.edu.cn</p><aside><h2>Bob</h2></aside><p>b@pku.edu.cn</p></main>",
    ] {
        check(
            html,
            &[("Alice", &["a@pku.edu.cn"]), ("Bob", &["b@pku.edu.cn"])],
        );
    }
}

#[test]
fn line_break_separated_people_in_a_paragraph_keep_ownership() {
    check(
        "<p>张三 教授 邮箱：zs@pku.edu.cn<br>李四 副教授 邮箱：ls@pku.edu.cn</p>",
        &[("张三", &["zs@pku.edu.cn"]), ("李四", &["ls@pku.edu.cn"])],
    );
}

#[test]
fn line_break_separated_people_in_a_table_cell_keep_ownership() {
    check(
        "<table><tr><td>张三 教授 邮箱：zs@pku.edu.cn<br>李四 副教授 邮箱：ls@pku.edu.cn</td></tr></table>",
        &[("张三", &["zs@pku.edu.cn"]), ("李四", &["ls@pku.edu.cn"])],
    );
}

#[test]
fn same_block_mailto_positions_handle_whitespace_and_repeated_names() {
    for html in [
        "<p>张 <span>三</span> 教授 <a href='mailto:zs@pku.edu.cn'>邮箱</a><br>李 四 副教授 <a href='mailto:ls@pku.edu.cn'>邮箱</a></p>",
        "<table><tr><td>张 <span>三</span> 教授 <a href='mailto:zs@pku.edu.cn'>邮箱</a><br>李 四 副教授 <a href='mailto:ls@pku.edu.cn'>邮箱</a></td></tr></table>",
        "<p>张三、李四<br>张 <span>三</span> 教授 <a href='mailto:zs@pku.edu.cn'>邮箱</a><br>李 四 副教授 <a href='mailto:ls@pku.edu.cn'>邮箱</a></p>",
    ] {
        check(
            html,
            &[("张三", &["zs@pku.edu.cn"]), ("李 四", &["ls@pku.edu.cn"])],
        );
    }
}

#[test]
fn same_line_people_cannot_claim_each_others_emails() {
    let results = [
        "<p>Alice a@pku.edu.cn Bob b@pku.edu.cn</p>",
        "<p>Alice <a href='mailto:a@pku.edu.cn'>联系</a> Bob <a href='mailto:b@pku.edu.cn'>联系</a></p>",
        r#"<p><a href="mailto:a@pku.edu.cn">Alice</a>，<a href="mailto:b@pku.edu.cn">Bob</a></p>"#,
        "<p><a href='mailto:a@pku.edu.cn'>Alice</a> <a href='mailto:b@pku.edu.cn'>Bob</a></p>",
    ]
    .map(|html| {
        pairings(
            html,
            ["Alice", "Bob"],
            ["a@pku.edu.cn", "b@pku.edu.cn"],
        )
    });
    // Bob blocks Alice at his address, but cannot start ownership mid-line.
    assert_eq!(results, [[[true, false], [false, false]]; 4]);
}

#[test]
fn longer_names_contain_no_independent_short_name_occurrence() {
    for (other, email) in [("王明华", "wmh@pku.edu.cn"), ("陈王明", "cwm@pku.edu.cn")] {
        for html in [
            format!("<p>王明 wm@pku.edu.cn<br>{other} {email}</p>"),
            format!(
                "<table><tr><td>王明</td><td>wm@pku.edu.cn</td></tr><tr><td>{other}</td><td>{email}</td></tr></table>"
            ),
        ] {
            check(&html, &[("王明", &["wm@pku.edu.cn"]), (other, &[email])]);
        }
    }
    check(
        "<p><a href='mailto:js@pku.edu.cn'>John Smith</a><br><a href='mailto:jss@pku.edu.cn'>John Smithson</a></p>",
        &[
            ("John Smith", &["js@pku.edu.cn"]),
            ("John Smithson", &["jss@pku.edu.cn"]),
        ],
    );
}

#[test]
fn prose_mentions_block_ownership_without_starting_it() {
    let results = [
        "<main><article><h2>Alice</h2><p>Collaborates with Bob</p><p>Email: a@pku.edu.cn</p></article><article><h2>Bob</h2><p>Email: b@pku.edu.cn</p></article></main>",
        "<main><article><h2>Alice</h2><p>Collaborates with\nBob</p><p>Email: a@pku.edu.cn</p></article><article><h2>Bob</h2><p>Email: b@pku.edu.cn</p></article></main>",
        "<main><h2>Alice</h2><p>Collaborates with Bob</p><p>a@pku.edu.cn</p><h2>Bob</h2><p>b@pku.edu.cn</p></main>",
        "<p>Alice<br>Collaborates with Bob<br>Email: a@pku.edu.cn<br>Bob<br>Email: b@pku.edu.cn</p>",
    ]
    .map(|html| {
        pairings(
            html,
            ["Alice", "Bob"],
            ["a@pku.edu.cn", "b@pku.edu.cn"],
        )
    });
    assert_eq!(results, [[[false, false], [false, true]]; 4]);
    assert_eq!(
        pairings(
            "<main><article><h2>张三</h2><p>与李四共同指导研究生</p><p>邮箱：zs@pku.edu.cn</p></article><article><h2>李四</h2><p>ls@pku.edu.cn</p></article></main>",
            ["张三", "李四"],
            ["zs@pku.edu.cn", "ls@pku.edu.cn"],
        ),
        [[false, false], [false, true]]
    );
    // A repeated leading name in the contact paragraph can start after the mention.
    check(
        "<main><article><h2>张三</h2><p>与李四共同指导研究生</p><p>张三邮箱：zs@pku.edu.cn</p></article><article><h2>李四</h2><p>ls@pku.edu.cn</p></article></main>",
        &[("张三", &["zs@pku.edu.cn"]), ("李四", &["ls@pku.edu.cn"])],
    );
}

#[test]
fn english_nonleading_mention_cannot_claim_an_email_in_the_same_block() {
    assert_eq!(
        pairings(
            r#"<main>
              <article><h2>Alice Chen</h2>
                <p>Works with Bob Li on machine learning.<br>
                   Email: achen@pku.edu.cn</p>
              </article>
              <article><h2>Bob Li</h2><p>Email: bli@pku.edu.cn</p></article>
            </main>"#,
            ["Alice Chen", "Bob Li"],
            ["achen@pku.edu.cn", "bli@pku.edu.cn"],
        ),
        [[false, false], [false, true]]
    );
}

#[test]
fn chinese_nonleading_mention_cannot_claim_an_email_in_the_same_block() {
    assert_eq!(
        pairings(
            r#"<main>
              <article><h2>张三</h2>
                <p>与李四共同指导研究生。<br>邮箱：zs@pku.edu.cn</p>
              </article>
              <article><h2>李四</h2><p>邮箱：ls@pku.edu.cn</p></article>
            </main>"#,
            ["张三", "李四"],
            ["zs@pku.edu.cn", "ls@pku.edu.cn"],
        ),
        [[false, false], [false, true]]
    );
}

#[test]
fn footer_and_navigation_contacts_do_not_extend_a_flat_list() {
    for region in ["footer", "nav"] {
        for contact in [
            "学院邮箱: office@pku.edu.cn",
            "<p>学院邮箱: office@pku.edu.cn</p>",
        ] {
            let html = format!(
                "<main><h2>Alice</h2><p>a@pku.edu.cn</p><h2>Bob</h2><p>b@pku.edu.cn</p><{region}>{contact}</{region}></main>"
            );
            check(
                &html,
                &[("Alice", &["a@pku.edu.cn"]), ("Bob", &["b@pku.edu.cn"])],
            );
            let page = parse(&url(), &html).unwrap();
            for name in ["Alice", "Bob"] {
                assert!(
                    !owns_email(&page, &["Alice", "Bob"], &[name], "office@pku.edu.cn"),
                    "{name}: {html}"
                );
            }
        }
    }
    let html = "<main><article><h2>Alice</h2><p>Collaborates with Bob</p>\
        <p>a@pku.edu.cn</p><footer>office@pku.edu.cn</footer></article>\
        <article><h2>Bob</h2><p>b@pku.edu.cn</p></article>\
        </main>";
    assert_eq!(
        pairings(html, ["Alice", "Bob"], ["a@pku.edu.cn", "b@pku.edu.cn"]),
        [[false, false], [false, true]]
    );
    let page = parse(&url(), html).unwrap();
    assert!(!owns_email(
        &page,
        &["Alice", "Bob"],
        &["Alice"],
        "office@pku.edu.cn"
    ));
}

#[test]
fn direct_name_text_and_links_precede_child_contact_blocks() {
    check(
        "<main><a href='/a'>Alice</a><p>a@pku.edu.cn</p><a href='/b'>Bob</a><p>b@pku.edu.cn</p></main>",
        &[("Alice", &["a@pku.edu.cn"]), ("Bob", &["b@pku.edu.cn"])],
    );
    check(
        "<main>张三<p>zs@pku.edu.cn</p>李四<p>ls@pku.edu.cn</p></main>",
        &[("张三", &["zs@pku.edu.cn"]), ("李四", &["ls@pku.edu.cn"])],
    );
}

#[test]
fn image_link_alternatives_provide_positioned_names() {
    for attribute in ["alt", "title", "aria-label"] {
        let html = format!(
            "<div><figure><a href='/a'><img src='a.jpg' {attribute}='Alice'></a><p>a@pku.edu.cn</p></figure><figure><a href='/b'><img src='b.jpg' {attribute}='Bob'></a><p>b@pku.edu.cn</p></figure></div>"
        );
        check(
            &html,
            &[("Alice", &["a@pku.edu.cn"]), ("Bob", &["b@pku.edu.cn"])],
        );
    }
    for attribute in ["title", "aria-label"] {
        let html = format!(
            "<ul><li><a href='/zs' {attribute}='张三'><img src='zs.jpg'></a><p>zs@pku.edu.cn</p></li><li><a href='/ls' {attribute}='李四'><img src='ls.jpg'></a><p>ls@pku.edu.cn</p></li></ul>"
        );
        check(
            &html,
            &[("张三", &["zs@pku.edu.cn"]), ("李四", &["ls@pku.edu.cn"])],
        );
    }
    check(
        "<ul><li><a href='/zs'><img alt='张三'></a><p>zs@pku.edu.cn</p></li><li><a href='/ls'><img alt='李四'></a><p>ls@pku.edu.cn</p></li></ul>",
        &[("张三", &["zs@pku.edu.cn"]), ("李四", &["ls@pku.edu.cn"])],
    );
}

#[test]
fn shared_name_blocks_without_ordered_contacts_are_rejected() {
    for html in
        ["<main><div><h2>Alice</h2><h2>Bob</h2></div><p>a@pku.edu.cn b@pku.edu.cn</p></main>"]
    {
        let page = parse(&url(), html).unwrap();
        let names = ["Alice", "Bob"];
        for name in names {
            for address in ["a@pku.edu.cn", "b@pku.edu.cn"] {
                assert!(
                    !owns_email(&page, &names, &[name], address),
                    "{name}: {address}, {html}"
                );
            }
        }
    }
}

#[test]
fn repeated_names_and_whitespace_use_any_matching_occurrence() {
    check(
        "<nav>张 三 李 四</nav><section><h2>张<span>三</span></h2><p>a@pku.edu.cn</p></section><section><h2>李\n四</h2><p>b@pku.edu.cn</p></section>",
        &[("张三", &["a@pku.edu.cn"]), ("李 四", &["b@pku.edu.cn"])],
    );
}

#[test]
fn blocks_keep_order_paths_and_only_their_own_evidence() {
    let page = parse(
        &url(),
        "<main>前<div>张 <span>三</span><a href='mailto:a@pku.edu.cn'>联系</a></div>后<p>\
        b@pku.edu.cn</p></main>",
    )
    .unwrap();
    assert_eq!(
        page.units
            .iter()
            .map(|u| u.text.as_str())
            .collect::<Vec<_>>(),
        ["前", "张 三联系", "后", "b@pku.edu.cn"]
    );
    assert_eq!(page.units[0].path, page.units[2].path);
    assert!(page.units[1].path.starts_with(&page.units[0].path));
    assert_ne!(page.units[1].path, page.units[3].path);
    assert_eq!(page.units[1].links.len(), 0);
    assert_eq!(page.units[1].emails[0].address, "a@pku.edu.cn");
    assert!(matches!(
        page.units[1].emails[0].location,
        EmailLocation::Attribute { .. }
    ));
    assert_eq!(page.units[3].emails.len(), 1);
    assert!(page.units[0].emails.is_empty() && page.units[2].emails.is_empty());
}

#[test]
fn every_real_mcgill_person_accepts_own_email_and_rejects_adjacent_emails() {
    let page = parse(
        &PageUrl::parse("https://www.cs.mcgill.ca/people/faculty/").unwrap(),
        include_str!("pages/mcgill.html"),
    )
    .unwrap();
    let people: Vec<_> = include_str!("pages/mcgill-emails.tsv")
        .lines()
        .map(|line| line.split_once('\t').unwrap())
        .collect();
    let names: Vec<_> = people.iter().map(|(name, _)| *name).collect();
    for (index, (name, address)) in people.iter().enumerate() {
        assert!(
            owns_email(&page, &names, &[name], address),
            "{name}: {address}"
        );
        for neighbor in [
            (index + 1) % people.len(),
            (index + people.len() - 1) % people.len(),
        ] {
            assert!(
                !owns_email(&page, &names, &[name], people[neighbor].1),
                "{name}: {}",
                people[neighbor].1
            );
        }
    }
}

#[test]
fn mcgill_footer_email_does_not_belong_to_the_last_mentor() {
    let page = parse(
        &PageUrl::parse("https://www.cs.mcgill.ca/people/faculty/").unwrap(),
        include_str!("pages/mcgill.html"),
    )
    .unwrap();
    let names: Vec<_> = include_str!("pages/mcgill-emails.tsv")
        .lines()
        .map(|line| line.split_once('\t').unwrap().0)
        .collect();
    assert!(owns_email(
        &page,
        &names,
        &["Sue Whitesides"],
        "sue@uvic.ca"
    ));
    assert!(!owns_email(
        &page,
        &names,
        &["Sue Whitesides"],
        "webadmin@cs.mcgill.ca"
    ));
}

#[test]
fn contacts_before_names_do_not_cross_sibling_articles() {
    check(
        "<section><article><p>a@pku.edu.cn</p><h2>Alice</h2></article><article><p>b@pku.edu.cn</p><h2>Bob</h2></article></section>",
        &[("Alice", &["a@pku.edu.cn"]), ("Bob", &["b@pku.edu.cn"])],
    );
}

#[test]
fn real_profiles_require_both_the_name_and_an_evidenced_address() {
    let toronto = parse(&url(), include_str!("pages/toronto.html")).unwrap();
    let names = ["Geoffrey E. Hinton", "Jennifer Cressman"];
    assert!(owns_email(
        &toronto,
        &names,
        &[names[0]],
        "geoffrey.hinton@gmail.com"
    ));
    // The assistant is only identified by role and obfuscated email, not by this name.
    assert!(!owns_email(
        &toronto,
        &names,
        &[names[1]],
        "geoffrey.hinton@gmail.com"
    ));
    assert!(!owns_email(
        &toronto,
        &names,
        &[names[1]],
        "jennifer.cressman@utoronto.ca"
    ));
    assert!(!owns_email(
        &toronto,
        &names,
        &[names[0]],
        "absent@utoronto.ca"
    ));
    for html in [
        include_str!("pages/nju.html").to_owned(),
        crawl::fetch::decode(include_bytes!("pages/nju-gbk.html"), None).unwrap(),
    ] {
        let page = parse(&url(), &html).unwrap();
        for address in ["zhouzh@nju.edu.cn", "zhouzh.gm@gmail.com"] {
            assert!(owns_email(
                &page,
                &["Zhi-Hua Zhou"],
                &["Zhi-Hua Zhou"],
                address
            ));
            assert!(!owns_email(
                &page,
                &["Zhi-Hua Zhou", "Absent"],
                &["Absent"],
                address
            ));
        }
        assert!(!owns_email(
            &page,
            &["Zhi-Hua Zhou"],
            &["Zhi-Hua Zhou"],
            "absent@nju.edu.cn"
        ));
    }
}
