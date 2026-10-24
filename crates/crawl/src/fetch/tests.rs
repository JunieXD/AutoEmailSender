use super::*;
use crate::Scope;
use net::rustls;
use tokio::{
    io::{AsyncBufReadExt, AsyncWriteExt, BufReader},
    net::TcpListener,
    sync::{mpsc, oneshot},
    task::JoinHandle,
};

struct Request {
    path: String,
    respond: oneshot::Sender<String>,
}
struct Server {
    port: u16,
    requests: mpsc::UnboundedReceiver<Request>,
    task: JoinHandle<()>,
}

#[tokio::test(start_paused = true)]
async fn subdomains_share_two_request_slots_and_start_spacing() {
    use std::{future::Future, task::Poll};
    let server = Server::start(false).await;
    let fetcher = server.fetcher();
    let first_site = fetcher
        .site(&PageUrl::parse("https://cs.pku.edu.cn/a").unwrap())
        .await;
    let second_site = fetcher
        .site(&PageUrl::parse("https://www.pku.edu.cn/b").unwrap())
        .await;
    let first = first_site.start().await.unwrap();
    let mut second = std::pin::pin!(second_site.start());
    assert!(std::future::poll_fn(|cx| Poll::Ready(second.as_mut().poll(cx).is_pending())).await);
    tokio::time::advance(REQUEST_INTERVAL - Duration::from_millis(1)).await;
    assert!(std::future::poll_fn(|cx| Poll::Ready(second.as_mut().poll(cx).is_pending())).await);
    tokio::time::advance(Duration::from_millis(1)).await;
    let second = second.await.unwrap();
    let mut third = std::pin::pin!(first_site.start());
    tokio::time::advance(REQUEST_INTERVAL).await;
    assert!(std::future::poll_fn(|cx| Poll::Ready(third.as_mut().poll(cx).is_pending())).await);
    drop(first);
    let third = third.await.unwrap();
    drop((second, third));
}
impl Drop for Server {
    fn drop(&mut self) {
        self.task.abort();
    }
}

impl Server {
    async fn start(tls: bool) -> Self {
        let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
        let port = listener.local_addr().unwrap().port();
        let (tx, requests) = mpsc::unbounded_channel();
        let acceptor = tls.then(|| {
            let cert = rcgen::generate_simple_self_signed(vec!["cs.pku.edu.cn".into()]).unwrap();
            let key = rustls::pki_types::PrivatePkcs8KeyDer::from(cert.signing_key.serialize_der())
                .into();
            let config = rustls::ServerConfig::builder_with_provider(Arc::new(
                rustls::crypto::ring::default_provider(),
            ))
            .with_safe_default_protocol_versions()
            .unwrap()
            .with_no_client_auth()
            .with_single_cert(vec![cert.cert.der().clone()], key)
            .unwrap();
            tokio_rustls::TlsAcceptor::from(Arc::new(config))
        });
        let task = tokio::spawn(async move {
            loop {
                let (stream, _) = listener.accept().await.unwrap();
                let tx = tx.clone();
                let acceptor = acceptor.clone();
                tokio::spawn(async move {
                    if let Some(tls) = acceptor {
                        // The first connection is expected to reject the self-signed certificate.
                        if let Ok(stream) = tls.accept(stream).await {
                            serve(stream, tx).await;
                        }
                    } else {
                        serve(stream, tx).await;
                    }
                });
            }
        });
        Self {
            port,
            requests,
            task,
        }
    }

    fn target(&self, path: &str, tls: bool) -> PageTarget {
        let scheme = if tls { "https" } else { "http" };
        let url = PageUrl::parse(&format!("{scheme}://cs.pku.edu.cn:{}{path}", self.port)).unwrap();
        Scope::new(&url).page(url).unwrap()
    }

    fn fetcher(&self) -> Fetcher {
        let net = net::Net::new(vec![]).unwrap();
        let address = ([127, 0, 0, 1], self.port).into();
        let client = |builder: reqwest::ClientBuilder| {
            builder
                .no_proxy()
                .resolve("cs.pku.edu.cn", address)
                .resolve("www.pku.edu.cn", address)
                .build()
                .unwrap()
        };
        Fetcher {
            trusted: client(net.page_http_builder()),
            untrusted: client(net.untrusted_page_http_builder()),
            sites: Mutex::new(BTreeMap::new()),
        }
    }
}

async fn serve<S: tokio::io::AsyncRead + tokio::io::AsyncWrite + Unpin>(
    stream: S,
    tx: mpsc::UnboundedSender<Request>,
) {
    let mut stream = BufReader::new(stream);
    let mut request = String::new();
    if stream.read_line(&mut request).await.unwrap() == 0 {
        return;
    }
    loop {
        let mut header = String::new();
        if stream.read_line(&mut header).await.unwrap() == 0 {
            return;
        }
        if header == "\r\n" {
            break;
        }
    }
    let (respond, response) = oneshot::channel();
    tx.send(Request {
        path: request.split_whitespace().nth(1).unwrap().into(),
        respond,
    })
    .unwrap();
    if let Ok(response) = response.await {
        // A size-limited reader may close the connection before the whole fixture is sent.
        let _ = stream.write_all(response.as_bytes()).await;
        let _ = stream.shutdown().await;
    }
}

fn reply(status: u16, extra: &str, body: &str) -> String {
    format!(
        "HTTP/1.1 {status} Reply\r\nConnection: close\r\nContent-Type: text/html; charset=utf-8\r\nContent-Length: {}\r\n{extra}\r\n{body}",
        body.len()
    )
}

#[tokio::test]
async fn redirects_are_checked_at_every_hop_and_preserve_the_final_source() {
    let mut server = Server::start(false).await;
    let fetcher = server.fetcher();
    let target = server.target("/start", false);
    let port = server.port;
    let responder = tokio::spawn(async move {
        let first = server.requests.recv().await.unwrap();
        assert_eq!(first.path, "/start");
        first
            .respond
            .send(reply(
                302,
                &format!("Location: http://www.pku.edu.cn:{port}/landing\r\n"),
                "",
            ))
            .unwrap();
        let second = server.requests.recv().await.unwrap();
        assert_eq!(second.path, "/landing");
        second
            .respond
            .send(reply(200, "", "<p>张老师 alice@pku.edu.cn</p>"))
            .unwrap();
    });
    let page = fetcher.fetch(&target).await.unwrap();
    assert!(page.material.url.as_str().contains("www.pku.edu.cn"));
    assert_eq!(page.material.units[0].emails[0].address, "alice@pku.edu.cn");
    assert!(!page.relaxed_certificate);
    responder.await.unwrap();
}

#[tokio::test]
async fn redirects_cannot_leave_the_site_or_reach_a_private_address() {
    for location in [
        "https://evil.edu/",
        "http://127.0.0.1/",
        "http://[::ffff:127.0.0.1]/",
    ] {
        let mut server = Server::start(false).await;
        let fetcher = server.fetcher();
        let target = server.target("/start", false);
        let responder = tokio::spawn(async move {
            server
                .requests
                .recv()
                .await
                .unwrap()
                .respond
                .send(reply(302, &format!("Location: {location}\r\n"), ""))
                .unwrap();
        });
        assert!(matches!(
            fetcher.fetch(&target).await,
            Err(CrawlError::OutsideSite | CrawlError::Url)
        ));
        responder.await.unwrap();
    }
}

#[tokio::test]
async fn bodies_are_limited_even_without_a_content_length() {
    for response in [
        format!(
            "HTTP/1.1 200 OK\r\nContent-Length: {}\r\n\r\n",
            MAX_PAGE_BYTES + 1
        ),
        format!(
            "HTTP/1.1 200 OK\r\nContent-Type: text/html\r\nConnection: close\r\n\r\n{}",
            "x".repeat(MAX_PAGE_BYTES + 1)
        ),
    ] {
        let mut server = Server::start(false).await;
        let fetcher = server.fetcher();
        let target = server.target("/large", false);
        let responder = tokio::spawn(async move {
            server
                .requests
                .recv()
                .await
                .unwrap()
                .respond
                .send(response)
                .unwrap();
        });
        assert!(matches!(
            fetcher.fetch(&target).await,
            Err(CrawlError::TooLarge)
        ));
        responder.await.unwrap();
    }
}

#[tokio::test]
async fn certificate_errors_retry_once_using_the_relaxed_net_client() {
    let mut server = Server::start(true).await;
    let fetcher = server.fetcher();
    let target = server.target("/expired", true);
    let responder = tokio::spawn(async move {
        server
            .requests
            .recv()
            .await
            .unwrap()
            .respond
            .send(reply(200, "", "<p>可读取的个人资料</p>"))
            .unwrap();
    });
    let page = fetcher.fetch(&target).await.unwrap();
    assert!(page.relaxed_certificate);
    assert!(page.material.text().contains("可读取的个人资料"));
    responder.await.unwrap();
}

#[tokio::test]
async fn hanging_responses_have_a_deadline() {
    let mut server = Server::start(false).await;
    let fetcher = server.fetcher();
    let target = server.target("/hang", false);
    let fetch = tokio::spawn(async move { fetcher.fetch(&target).await });
    let request = server.requests.recv().await.unwrap();
    tokio::time::pause();
    tokio::time::advance(PAGE_TIMEOUT).await;
    assert!(matches!(fetch.await.unwrap(), Err(CrawlError::TimedOut)));
    drop(request);
}

#[tokio::test]
async fn unrelated_http_failures_are_reported() {
    let mut server = Server::start(false).await;
    let fetcher = server.fetcher();
    let target = server.target("/missing", false);
    let responder = tokio::spawn(async move {
        server
            .requests
            .recv()
            .await
            .unwrap()
            .respond
            .send(reply(404, "", "<p>not found</p>"))
            .unwrap();
    });
    assert!(matches!(
        fetcher.fetch(&target).await,
        Err(CrawlError::Status(404))
    ));
    responder.await.unwrap();
}
