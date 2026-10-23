// Marks the whole file as test code, so clippy's test allowances cover the helpers too.
#![cfg(test)]

//! Logging in and sending against small SMTP and IMAP servers that run in the test, with a
//! certificate from a test authority that only `Net::new` is told to trust.

use std::borrow::Cow;
use std::sync::{Arc, Mutex};

use base64::Engine;
use mailbox::{Account, MailError, Net, Security, Server};
use rcgen::{
    BasicConstraints, CertificateParams, CertifiedIssuer, ExtendedKeyUsagePurpose, IsCa, KeyPair,
    KeyUsagePurpose,
};
use tokio::io::{AsyncBufReadExt, AsyncRead, AsyncWrite, AsyncWriteExt, BufReader};
use tokio::net::TcpListener;
use tokio_rustls::TlsAcceptor;
use tokio_rustls::rustls::pki_types::{CertificateDer, PrivateKeyDer};
use tokio_rustls::rustls::{self, ServerConfig};

const ADDRESS: &str = "me@example.com";
const SECRET: &str = "right secret";

/// A test authority and a `localhost` certificate it signed. macOS refuses certificates valid for
/// over 825 days or without the server purpose, even from an authority it is told to trust.
fn pki() -> (CertificateDer<'static>, TlsAcceptor) {
    let now = time::OffsetDateTime::now_utc();
    let valid = |params: &mut CertificateParams| {
        params.not_before = now - time::Duration::days(1);
        params.not_after = now + time::Duration::days(30);
    };
    let mut ca = CertificateParams::new(Vec::<String>::new()).unwrap();
    ca.is_ca = IsCa::Ca(BasicConstraints::Unconstrained);
    ca.key_usages = vec![
        KeyUsagePurpose::KeyCertSign,
        KeyUsagePurpose::DigitalSignature,
    ];
    valid(&mut ca);
    let ca = CertifiedIssuer::self_signed(ca, KeyPair::generate().unwrap()).unwrap();
    let mut leaf = CertificateParams::new(vec!["localhost".to_owned()]).unwrap();
    leaf.extended_key_usages = vec![ExtendedKeyUsagePurpose::ServerAuth];
    valid(&mut leaf);
    let key = KeyPair::generate().unwrap();
    let cert = leaf.signed_by(&key, &ca).unwrap();
    let config =
        ServerConfig::builder_with_provider(Arc::new(rustls::crypto::ring::default_provider()))
            .with_safe_default_protocol_versions()
            .unwrap()
            .with_no_client_auth()
            .with_single_cert(
                vec![cert.der().clone()],
                PrivateKeyDer::Pkcs8(key.serialize_der().into()),
            )
            .unwrap();
    (ca.der().clone(), TlsAcceptor::from(Arc::new(config)))
}

trait Stream: AsyncRead + AsyncWrite + Unpin + Send {}
impl<T: AsyncRead + AsyncWrite + Unpin + Send> Stream for T {}

/// Starts a fake server on a free port and returns the port. Each connection is served by `serve`,
/// given a stream that is already encrypted unless the server speaks STARTTLS.
async fn listen<F, Fut>(tls: &TlsAcceptor, security: Security, serve: F) -> u16
where
    F: Fn(BufReader<Box<dyn Stream>>) -> Fut + Send + Sync + 'static,
    Fut: Future<Output = std::io::Result<()>> + Send,
{
    let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let port = listener.local_addr().unwrap().port();
    let tls = tls.clone();
    tokio::spawn(async move {
        loop {
            let (tcp, _) = listener.accept().await.unwrap();
            let stream: Box<dyn Stream> = match security {
                Security::Tls => match tls.accept(tcp).await {
                    Ok(s) => Box::new(s),
                    // A client that does not trust the certificate hangs up here.
                    Err(_) => continue,
                },
                Security::Starttls => Box::new(tcp),
            };
            // Errors are the client hanging up; what it saw is what the tests check.
            let _ = serve(BufReader::new(stream)).await;
        }
    });
    port
}

/// Mail the fake SMTP server accepted.
type Inbox = Arc<Mutex<Vec<String>>>;

async fn smtp(tls: &TlsAcceptor, security: Security) -> (u16, Inbox) {
    let inbox = Inbox::default();
    let received = inbox.clone();
    let acceptor = tls.clone();
    let port = listen(tls, security, move |io| {
        smtp_session(io, acceptor.clone(), received.clone())
    })
    .await;
    (port, inbox)
}

async fn smtp_session(
    mut io: BufReader<Box<dyn Stream>>,
    tls: TlsAcceptor,
    inbox: Inbox,
) -> std::io::Result<()> {
    let login = base64::engine::general_purpose::STANDARD.encode(format!("\0{ADDRESS}\0{SECRET}"));
    let mut encrypted = false;
    io.write_all(b"220 fake ESMTP\r\n").await?;
    loop {
        let mut line = String::new();
        if io.read_line(&mut line).await? == 0 {
            return Ok(());
        }
        let words: Vec<&str> = line.split_whitespace().collect();
        let reply = match words.first().map(|w| w.to_ascii_uppercase()).as_deref() {
            Some("EHLO") if encrypted => "250-fake\r\n250 AUTH PLAIN LOGIN\r\n",
            Some("EHLO") => "250-fake\r\n250-STARTTLS\r\n250 AUTH PLAIN LOGIN\r\n",
            Some("STARTTLS") => {
                io.write_all(b"220 go ahead\r\n").await?;
                io = BufReader::new(Box::new(tls.accept(io.into_inner()).await?));
                encrypted = true;
                continue;
            }
            Some("AUTH") if words.get(2) == Some(&login.as_str()) => "235 ok\r\n",
            Some("AUTH") => "535 5.7.8 authentication failed\r\n",
            Some("MAIL" | "RCPT") => "250 ok\r\n",
            Some("DATA") => {
                io.write_all(b"354 go ahead\r\n").await?;
                let mut message = String::new();
                while !message.ends_with("\r\n.\r\n") {
                    io.read_line(&mut message).await?;
                }
                inbox.lock().unwrap().push(message);
                "250 queued\r\n"
            }
            Some("QUIT") => {
                io.write_all(b"221 bye\r\n").await?;
                return Ok(());
            }
            _ => "502 unknown\r\n",
        };
        io.write_all(reply.as_bytes()).await?;
    }
}

/// Like 163's, the fake IMAP server opens no folder before the client sends ID.
async fn imap(tls: &TlsAcceptor, security: Security) -> u16 {
    let acceptor = tls.clone();
    listen(tls, security, move |io| imap_session(io, acceptor.clone())).await
}

async fn imap_session(mut io: BufReader<Box<dyn Stream>>, tls: TlsAcceptor) -> std::io::Result<()> {
    let login = format!("\"{ADDRESS}\" \"{SECRET}\"");
    let mut identified = false;
    io.write_all(b"* OK fake IMAP ready\r\n").await?;
    loop {
        let mut line = String::new();
        if io.read_line(&mut line).await? == 0 {
            return Ok(());
        }
        let line = line.trim_end();
        let mut words = line.splitn(3, ' ');
        let (tag, command, rest) = (
            words.next().unwrap_or_default(),
            words.next().unwrap_or_default().to_ascii_uppercase(),
            words.next().unwrap_or_default(),
        );
        let reply = match command.as_str() {
            "CAPABILITY" => format!("* CAPABILITY IMAP4rev1 ID STARTTLS\r\n{tag} OK done\r\n"),
            "STARTTLS" => {
                io.write_all(format!("{tag} OK go ahead\r\n").as_bytes())
                    .await?;
                io = BufReader::new(Box::new(tls.accept(io.into_inner()).await?));
                continue;
            }
            "LOGIN" if rest == login => format!("{tag} OK logged in\r\n"),
            "LOGIN" => format!("{tag} NO [AUTHENTICATIONFAILED] invalid credentials\r\n"),
            "ID" => {
                identified = true;
                format!("* ID NIL\r\n{tag} OK done\r\n")
            }
            "SELECT" if identified => {
                format!("* 0 EXISTS\r\n* OK [UIDVALIDITY 1] ok\r\n{tag} OK [READ-WRITE] done\r\n")
            }
            "SELECT" => format!("{tag} NO Unsafe Login\r\n"),
            "LOGOUT" => {
                io.write_all(format!("* BYE\r\n{tag} OK bye\r\n").as_bytes())
                    .await?;
                return Ok(());
            }
            _ => format!("{tag} BAD unknown\r\n"),
        };
        io.write_all(reply.as_bytes()).await?;
    }
}

fn account(smtp: u16, imap: u16, security: Security, secret: &str) -> Account {
    let server = |port| Server {
        host: Cow::Borrowed("localhost"),
        port,
        security,
    };
    Account {
        address: ADDRESS.parse().unwrap(),
        sender_name: "林晓".into(),
        smtp: server(smtp),
        imap: server(imap),
        secret: secret.into(),
    }
}

#[tokio::test]
async fn checks_pass_over_tls_and_starttls() {
    let (root, tls) = pki();
    let net = Net::new(vec![root]).unwrap();
    for security in [Security::Tls, Security::Starttls] {
        let (smtp, _) = smtp(&tls, security).await;
        let imap = imap(&tls, security).await;
        let me = account(smtp, imap, security, SECRET);
        mailbox::check_sending(&net, &me).await.unwrap();
        mailbox::check_receiving(&net, &me).await.unwrap();
    }
}

#[tokio::test]
async fn a_wrong_secret_is_a_refused_login() {
    let (root, tls) = pki();
    let net = Net::new(vec![root]).unwrap();
    let (smtp, _) = smtp(&tls, Security::Tls).await;
    let imap = imap(&tls, Security::Tls).await;
    let me = account(smtp, imap, Security::Tls, "wrong");
    let sending = mailbox::check_sending(&net, &me).await;
    assert!(
        matches!(sending, Err(MailError::Login(ref r)) if r.contains("535")),
        "{sending:?}"
    );
    let receiving = mailbox::check_receiving(&net, &me).await;
    assert!(
        matches!(receiving, Err(MailError::Login(ref r)) if r == "[AUTHENTICATIONFAILED] invalid credentials"),
        "{receiving:?}"
    );
}

#[tokio::test]
async fn an_untrusted_certificate_is_refused() {
    let (_, tls) = pki();
    let net = Net::new(Vec::new()).unwrap();
    let (smtp, _) = smtp(&tls, Security::Tls).await;
    let imap = imap(&tls, Security::Tls).await;
    let me = account(smtp, imap, Security::Tls, SECRET);
    for result in [
        mailbox::check_sending(&net, &me).await,
        mailbox::check_receiving(&net, &me).await,
    ] {
        let message = result.unwrap_err().message();
        assert!(message.contains("证书不受信任"), "{message}");
    }
}

#[tokio::test]
async fn the_test_email_goes_to_the_mailbox_itself() {
    let (root, tls) = pki();
    let net = Net::new(vec![root]).unwrap();
    let (smtp, inbox) = smtp(&tls, Security::Starttls).await;
    let me = account(smtp, 0, Security::Starttls, SECRET);
    mailbox::send_test(&net, &me).await.unwrap();
    let inbox = inbox.lock().unwrap();
    assert_eq!(inbox.len(), 1);
    let headers = inbox[0].split("\r\n\r\n").next().unwrap();
    let header = |name: &str| {
        headers
            .lines()
            .find_map(|l| l.strip_prefix(name))
            .unwrap_or_default()
    };
    assert!(
        header("From: ").ends_with(&format!("<{ADDRESS}>")),
        "{headers}"
    );
    assert!(
        header("To: ").ends_with(&format!("<{ADDRESS}>")),
        "{headers}"
    );
    // Named after the mailbox's domain, not the computer.
    assert!(
        header("Message-ID: ").ends_with("@example.com>"),
        "{headers}"
    );
}
