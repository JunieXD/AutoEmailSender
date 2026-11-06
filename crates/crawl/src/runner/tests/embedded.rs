use super::*;
use crate::enrich;
use base64::{Engine, engine::general_purpose::STANDARD};

const CV: &[u8] = include_bytes!("../../../tests/pages/cv.pdf");

fn visited(site: &Site, ending: &str) -> Vec<String> {
    let visits = site.visits.lock().unwrap();
    visits
        .iter()
        .filter(|v| v.ends_with(ending))
        .cloned()
        .collect()
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn addresses_drawn_as_pictures_are_read_in_place_and_marked(db: SqlitePool) {
    let site = Site::new().await;
    site.page(
        "/p",
        "<title>团队</title><div><p>张三 邮箱：<img src='/zs.png'></p>\
        <p>李四 邮箱：<img data-src='/ls.png' src='/loading.gif'></p></div>\
        <img src='/photo.png'><img src='/banner.png' width='900' height='300'>",
    );
    site.file("/zs.png", "image/png", image(180, 20, "zs@example.edu"));
    site.file("/ls.png", "image/png", image(180, 20, "ls@example. edu"));
    site.file(
        "/photo.png",
        "image/png",
        image(300, 400, "photo@example.edu"),
    );
    site.file(
        "/banner.png",
        "image/png",
        image(900, 300, "banner@example.edu"),
    );
    let mut provider = Provider::start().await.unwrap();
    config(&db, &provider.address).await;
    let (_, id) = enrichment::candidate(&db, &site, "/p").await;
    enrich::start(&db, &[id], 2).await.unwrap();
    let claim = queue::next(&db).await.unwrap().unwrap();
    let mut answer = targeted("zs@example.edu");
    answer["relation"] = json!("uncertain");
    answer["emails"] = json!(["zs@example.edu", "ls@example.edu"]);
    answer["names"] = json!(["张三", "李四"]);
    let (result, given) = enrichment::answered(&db, &site, &mut provider, &claim, answer).await;
    save(&db, &claim, &result).await.unwrap();
    assert!(given.contains("zs@example.edu") && given.contains("ls@example.edu"));
    // A photo is no line of text; nor is a banner, seen from its attributes without reading it.
    assert!(!given.contains("photo@") && !given.contains("banner@"));
    assert!(visited(&site, "/banner.png").is_empty());
    assert!(visited(&site, "/loading.gif").is_empty());
    // Each address read where it was drawn: the neighbour's stays the neighbour's.
    let d = review::detail(&db, id).await.unwrap().unwrap();
    assert_eq!(d.candidate.fields.emails, vec!["zs@example.edu"]);
    assert_eq!(
        d.evidence.last().unwrap().recognized,
        vec!["zs@example.edu"]
    );
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn pdfs_are_read_while_research_is_missing_and_pictures_while_the_address_is(db: SqlitePool) {
    let site = Site::new().await;
    site.page(
        "/p",
        "<title>张三</title><p>张三 教授 <img src='/mail.png'></p>\
        <iframe src='/pdfjs/web/viewer.html?file=%2Ffiles%2Fcv.pdf'></iframe>\
        <iframe src='/pdfjs/web/viewer.html?file=%252Ffiles%252Fpapers.pdf'></iframe>",
    );
    site.file("/mail.png", "image/png", image(180, 20, "zs@example.edu"));
    site.file("/files/cv.pdf", "application/pdf", CV.to_vec());
    site.file("/files/papers.pdf", "application/pdf", CV.to_vec());
    let mut provider = Provider::start().await.unwrap();
    config(&db, &provider.address).await;
    let (_, id) = enrichment::candidate(&db, &site, "/p").await;
    sqlx::query("UPDATE candidate SET emails = '[\"old@example.edu\"]' WHERE id = ?")
        .bind(id)
        .execute(&db)
        .await
        .unwrap();
    enrich::start(&db, &[id], 2).await.unwrap();
    let claim = queue::next(&db).await.unwrap().unwrap();
    let (result, given) = enrichment::answered(
        &db,
        &site,
        &mut provider,
        &claim,
        targeted("zhangsan@cs.example.edu.cn"),
    )
    .await;
    save(&db, &claim, &result).await.unwrap();
    assert!(visited(&site, "/mail.png").is_empty());
    assert_eq!(
        visited(&site, ".pdf"),
        ["/files/cv.pdf", "/files/papers.pdf"]
    );
    // The radicals a PDF made on macOS gives for 士 and 生 are read as the characters.
    assert!(given.contains("博士生导师") && given.contains("zhangsan@cs.example.edu.cn"));
    let text = sqlx::query_scalar::<_, String>("SELECT text FROM crawl_page WHERE id = ?")
        .bind(claim.id)
        .fetch_one(&db)
        .await
        .unwrap();
    assert!(text.contains("研究方向:机器学习"));
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn pictures_that_speak_of_contact_are_read_first_and_six_at_most(db: SqlitePool) {
    let site = Site::new().await;
    let mut html = "<title>张三</title>".to_owned();
    for n in 1..=7 {
        html += &format!("<p><img src='/{n}.png'></p>");
        let text = format!("n{n}@example.edu");
        site.file(&format!("/{n}.png"), "image/png", image(120, 16, &text));
    }
    html += "<p>张三 E-mail: <img src='/a.png'></p><p><img src='/b.png' alt='Email'></p>";
    site.file("/a.png", "image/png", image(120, 16, "zs@example.edu"));
    site.file(
        "/b.png",
        "image/png",
        image(120, 16, "zhangsan@example.edu"),
    );
    site.page("/p", &html);
    let mut provider = Provider::start().await.unwrap();
    config(&db, &provider.address).await;
    let (_, id) = enrichment::candidate(&db, &site, "/p").await;
    enrich::start(&db, &[id], 2).await.unwrap();
    let claim = queue::next(&db).await.unwrap().unwrap();
    let (result, given) = enrichment::answered(
        &db,
        &site,
        &mut provider,
        &claim,
        targeted("zs@example.edu"),
    )
    .await;
    save(&db, &claim, &result).await.unwrap();
    assert_eq!(
        visited(&site, ".png"),
        ["/a.png", "/b.png", "/1.png", "/2.png", "/3.png", "/4.png"]
    );
    assert!(given.contains("zhangsan@example.edu") && !given.contains("n5@example.edu"));
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn a_pdf_chosen_as_a_subpage_is_read_as_its_text(db: SqlitePool) {
    let site = Site::new().await;
    site.page(
        "/p",
        "<title>张三</title><p>张三</p><a href='/cv.pdf'>个人简历</a>",
    );
    site.file("/cv.pdf", "application/pdf", CV.to_vec());
    let mut provider = Provider::start().await.unwrap();
    config(&db, &provider.address).await;
    let (_, id) = enrichment::candidate(&db, &site, "/p").await;
    enrich::start(&db, &[id], 2).await.unwrap();
    let claim = queue::next(&db).await.unwrap().unwrap();
    let mut answer = targeted("missing@example.edu");
    answer["research"] = Value::Null;
    answer["next"] = json!([1]);
    let (result, _) = enrichment::answered(&db, &site, &mut provider, &claim, answer).await;
    save(&db, &claim, &result).await.unwrap();
    let claim = queue::next(&db).await.unwrap().unwrap();
    assert_eq!(claim.kind, PageKind::EnrichChild);
    let mut answer = targeted("zhangsan@cs.example.edu.cn");
    answer["names"] = json!(["张三", "李四"]);
    let (result, given) = enrichment::answered(&db, &site, &mut provider, &claim, answer).await;
    save(&db, &claim, &result).await.unwrap();
    assert!(given.contains("知识图谱"));
    let d = review::detail(&db, id).await.unwrap().unwrap();
    assert_eq!(
        d.candidate.fields.emails,
        vec!["zhangsan@cs.example.edu.cn"]
    );
    assert!(d.evidence.last().unwrap().recognized.is_empty());
}

#[tokio::test]
async fn a_website_asking_to_slow_down_holds_back_the_rest_of_its_pictures() {
    let site = Site::new().await;
    let drawn = STANDARD.encode(image(120, 16, "zs@example.edu"));
    site.files
        .lock()
        .unwrap()
        .insert("/slow.png".into(), (429, "text/plain", vec![]));
    site.file(
        "/later.png",
        "image/png",
        image(120, 16, "later@example.edu"),
    );
    let page = crate::parse(
        &site.url("/p"),
        &format!(
            "<p>张三 <img src='data:image/png;base64,{drawn}'></p>\
            <p><img src='/slow.png'></p><p><img src='/later.png'></p>"
        ),
    );
    let fetcher = Fetcher::local(site.port);
    let cooldown = Cooldown::new(Arc::default());
    let read = crate::embedded::Reader {
        fetcher: &fetcher,
        recognize,
        cooldown: &cooldown,
        crawl: 1,
    }
    .read(page, true, true)
    .await
    .unwrap();
    assert!(read.text().contains("张三 zs@example.edu"));
    assert!(read.only_from_image("zs@example.edu"));
    assert!(!read.text().contains("later@"));
    assert_eq!(visited(&site, ".png"), ["/slow.png"]);
    assert!(cooldown.until().is_some());
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn a_page_that_is_only_a_picture_and_a_pdf_is_read(db: SqlitePool) {
    let site = Site::new().await;
    site.page(
        "/p",
        "<title>张三</title><img src='/mail.png'><iframe src='/cv.pdf'></iframe>",
    );
    site.file("/mail.png", "image/png", image(180, 20, "zs@example.edu"));
    // A server that puts a blank line before the file.
    site.file("/cv.pdf", "application/pdf", [b"\r\n", CV].concat());
    let mut provider = Provider::start().await.unwrap();
    config(&db, &provider.address).await;
    let (_, id) = enrichment::candidate(&db, &site, "/p").await;
    enrich::start(&db, &[id], 2).await.unwrap();
    let claim = queue::next(&db).await.unwrap().unwrap();
    let (result, given) = enrichment::answered(
        &db,
        &site,
        &mut provider,
        &claim,
        targeted("zs@example.edu"),
    )
    .await;
    save(&db, &claim, &result).await.unwrap();
    assert!(given.contains("知识图谱"));
    let d = review::detail(&db, id).await.unwrap().unwrap();
    assert_eq!(d.candidate.fields.emails, vec!["zs@example.edu"]);
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn each_line_of_a_picture_is_a_line_of_the_page(db: SqlitePool) {
    let site = Site::new().await;
    site.page("/p", "<title>团队</title><p><img src='/team.png'></p>");
    let lines = "李四 ls@example.edu\n张三 zs@example.edu";
    site.file("/team.png", "image/png", image(200, 40, lines));
    let mut provider = Provider::start().await.unwrap();
    config(&db, &provider.address).await;
    let (_, id) = enrichment::candidate(&db, &site, "/p").await;
    enrich::start(&db, &[id], 2).await.unwrap();
    let claim = queue::next(&db).await.unwrap().unwrap();
    let mut answer = targeted("zs@example.edu");
    answer["relation"] = json!("uncertain");
    answer["emails"] = json!(["zs@example.edu", "ls@example.edu"]);
    answer["names"] = json!(["张三", "李四"]);
    let (result, _) = enrichment::answered(&db, &site, &mut provider, &claim, answer).await;
    save(&db, &claim, &result).await.unwrap();
    // The address on 张三's own line is his, as it would be after a <br>.
    let d = review::detail(&db, id).await.unwrap().unwrap();
    assert_eq!(d.candidate.fields.emails, vec!["zs@example.edu"]);
}

#[sqlx::test(migrator = "db::MIGRATOR")]
async fn a_pause_stops_the_pictures_still_to_be_read(db: SqlitePool) {
    let site = Site::new().await;
    site.page(
        "/p",
        "<title>张三</title><p>张三 <img src='/1.png'></p><p><img src='/2.png'></p>",
    );
    // A website that never answers.
    site.files
        .lock()
        .unwrap()
        .insert("/1.png".into(), (0, "image/png", vec![]));
    site.file("/2.png", "image/png", image(120, 16, "zs@example.edu"));
    let provider = Provider::start().await.unwrap();
    config(&db, &provider.address).await;
    let (crawl, id) = enrichment::candidate(&db, &site, "/p").await;
    enrich::start(&db, &[id], 2).await.unwrap();
    let claim = queue::next(&db).await.unwrap().unwrap();
    let net = net::Net::new(vec![]).unwrap();
    let pages = Pages::local(site.port);
    let limit = Limit::default();
    let cooldown = Cooldown::new(Arc::default());
    let read = process(&db, &net, &pages, &limit, &claim, &cooldown);
    let pause = async {
        tokio::time::timeout(GUARD_TIMEOUT, async {
            while visited(&site, "/1.png").is_empty() {
                tokio::time::sleep(Duration::from_millis(10)).await;
            }
        })
        .await
        .unwrap();
        assert!(queue::control(&db, crawl, "pause", 2).await.unwrap());
    };
    let (outcome, ()) = tokio::time::timeout(GUARD_TIMEOUT, async { tokio::join!(read, pause) })
        .await
        .unwrap();
    assert!(matches!(outcome.unwrap(), Outcome::Gone));
    assert!(visited(&site, "/2.png").is_empty());
}
