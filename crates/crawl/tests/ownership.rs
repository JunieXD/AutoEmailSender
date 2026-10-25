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
                    owns_email(&page, &names, name, address),
                    name == other,
                    "{name}: {address}, {html}"
                );
            }
        }
        assert!(!owns_email(&page, &names, name, "absent@pku.edu.cn"));
        for address in *own {
            assert!(!owns_email(&page, &names, "Absent", address));
        }
    }
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
fn flat_headings_and_contacts_use_direct_children() {
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
fn shared_name_blocks_cannot_use_the_flat_fallback() {
    for html in [
        "<p>Alice a@pku.edu.cn Bob b@pku.edu.cn</p>",
        "<p><a href='mailto:a@pku.edu.cn'>Alice</a> <a href='mailto:b@pku.edu.cn'>Bob</a></p>",
        "<p>Alice <a href='mailto:a@pku.edu.cn'>联系</a> Bob <a href='mailto:b@pku.edu.cn'>联系</a></p>",
        "<main><div><h2>Alice</h2><h2>Bob</h2></div><p>a@pku.edu.cn b@pku.edu.cn</p></main>",
    ] {
        let page = parse(&url(), html).unwrap();
        let names = ["Alice", "Bob"];
        for name in names {
            for address in ["a@pku.edu.cn", "b@pku.edu.cn"] {
                assert!(
                    !owns_email(&page, &names, name, address),
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
    let page = parse(&url(), "<main>前<div>张 <span>三</span><a href='mailto:a@pku.edu.cn'>联系</a></div>后<p>b@pku.edu.cn</p></main>").unwrap();
    assert_eq!(
        page.units
            .iter()
            .map(|u| u.text.as_str())
            .collect::<Vec<_>>(),
        ["前", "张 三 联系", "后", "b@pku.edu.cn"]
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
            owns_email(&page, &names, name, address),
            "{name}: {address}"
        );
        for neighbor in [
            (index + 1) % people.len(),
            (index + people.len() - 1) % people.len(),
        ] {
            assert!(
                !owns_email(&page, &names, name, people[neighbor].1),
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
    assert!(owns_email(&page, &names, "Sue Whitesides", "sue@uvic.ca"));
    assert!(!owns_email(
        &page,
        &names,
        "Sue Whitesides",
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
        names[0],
        "geoffrey.hinton@gmail.com"
    ));
    // The assistant is only identified by role and obfuscated email, not by this name.
    assert!(!owns_email(
        &toronto,
        &names,
        names[1],
        "geoffrey.hinton@gmail.com"
    ));
    assert!(!owns_email(
        &toronto,
        &names,
        names[1],
        "jennifer.cressman@utoronto.ca"
    ));
    assert!(!owns_email(
        &toronto,
        &names,
        names[0],
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
                "Zhi-Hua Zhou",
                address
            ));
            assert!(!owns_email(
                &page,
                &["Zhi-Hua Zhou", "Absent"],
                "Absent",
                address
            ));
        }
        assert!(!owns_email(
            &page,
            &["Zhi-Hua Zhou"],
            "Zhi-Hua Zhou",
            "absent@nju.edu.cn"
        ));
    }
}
