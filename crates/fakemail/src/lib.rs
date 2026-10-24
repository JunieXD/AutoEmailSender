//! Small SMTP and IMAP servers for tests, with a certificate from a test authority that only the
//! `Net` the test builds is told to trust (docs/development.md#测试).

// A test server has nobody to report errors to but the test, which fails on them.
#![expect(
    clippy::unwrap_used,
    clippy::panic,
    reason = "fails the test using the server"
)]

use std::sync::{Arc, Mutex};

use base64::Engine;
use rcgen::{
    BasicConstraints, CertificateParams, CertifiedIssuer, DnType, ExtendedKeyUsagePurpose, IsCa,
    KeyPair, KeyUsagePurpose,
};
use tokio::io::{AsyncBufReadExt, AsyncRead, AsyncWrite, AsyncWriteExt, BufReader};

mod imap;

pub use imap::{Store, imap};
use tokio::net::TcpListener;
use tokio_rustls::TlsAcceptor;
use tokio_rustls::rustls::pki_types::{CertificateDer, PrivateKeyDer};
use tokio_rustls::rustls::{self, ServerConfig};

/// The mailbox the servers accept a login for.
pub const ADDRESS: &str = "me@example.com";
pub const SECRET: &str = "right secret";

/// A test authority and a `localhost` certificate it signed. macOS refuses certificates valid for
/// over 825 days or without the server purpose, even from an authority it is told to trust. Each
/// authority has its own name, and the certificate names its key: Windows otherwise picks up another
/// test's authority by name and fails to verify the signature.
pub fn pki() -> Pki {
    let now = time::OffsetDateTime::now_utc();
    let valid = |params: &mut CertificateParams| {
        params.not_before = now - time::Duration::days(1);
        params.not_after = now + time::Duration::days(30);
    };
    let mut ca = CertificateParams::new(Vec::<String>::new()).unwrap();
    ca.is_ca = IsCa::Ca(BasicConstraints::Unconstrained);
    ca.distinguished_name
        .push(DnType::CommonName, format!("test {}", uuid::Uuid::new_v4()));
    ca.key_usages = vec![
        KeyUsagePurpose::KeyCertSign,
        KeyUsagePurpose::DigitalSignature,
    ];
    valid(&mut ca);
    let ca = CertifiedIssuer::self_signed(ca, KeyPair::generate().unwrap()).unwrap();
    let mut leaf = CertificateParams::new(vec!["localhost".to_owned()]).unwrap();
    leaf.extended_key_usages = vec![ExtendedKeyUsagePurpose::ServerAuth];
    leaf.use_authority_key_identifier_extension = true;
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
    Pki {
        root: ca.der().clone(),
        tls: TlsAcceptor::from(Arc::new(config)),
    }
}

pub struct Pki {
    /// The authority to trust.
    pub root: CertificateDer<'static>,
    tls: TlsAcceptor,
}

trait Stream: AsyncRead + AsyncWrite + Unpin + Send {}
impl<T: AsyncRead + AsyncWrite + Unpin + Send> Stream for T {}

/// Starts a fake server on a free port and returns the port. Each connection is served by `serve`
/// on a task of its own, given a stream that is already encrypted unless the server speaks STARTTLS.
async fn listen<F, Fut>(tls: &TlsAcceptor, starttls: bool, serve: F) -> u16
where
    F: Fn(BufReader<Box<dyn Stream>>) -> Fut + Send + Sync + 'static,
    Fut: Future<Output = std::io::Result<()>> + Send + 'static,
{
    let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let port = listener.local_addr().unwrap().port();
    let tls = tls.clone();
    let serve = Arc::new(serve);
    tokio::spawn(async move {
        loop {
            let (tcp, _) = listener.accept().await.unwrap();
            let (tls, serve) = (tls.clone(), serve.clone());
            tokio::spawn(async move {
                let stream: Box<dyn Stream> = if starttls {
                    Box::new(tcp)
                } else {
                    match tls.accept(tcp).await {
                        Ok(s) => Box::new(s),
                        // A client that does not trust the certificate hangs up here.
                        Err(_) => return,
                    }
                };
                // Errors are the client hanging up; what it saw is what the tests check.
                let _ = serve(BufReader::new(stream)).await;
            });
        }
    });
    port
}

/// Mail the fake SMTP server accepted.
pub type Inbox = Arc<Mutex<Vec<String>>>;

/// An SMTP server that takes mail for anyone, except that it refuses recipients named `unknown`, and
/// hangs up without answering once it has the mail for one named `hangup`.
pub async fn smtp(pki: &Pki, starttls: bool) -> (u16, Inbox) {
    let tls = &pki.tls;
    let inbox = Inbox::default();
    let received = inbox.clone();
    let acceptor = tls.clone();
    let port = listen(tls, starttls, move |io| {
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
    let mut hangup = false;
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
            Some("RCPT") if line.contains("<unknown@") => "550 5.1.1 user unknown\r\n",
            Some("RCPT") => {
                hangup |= line.contains("<hangup@");
                "250 ok\r\n"
            }
            Some("MAIL") => "250 ok\r\n",
            Some("DATA") => {
                io.write_all(b"354 go ahead\r\n").await?;
                let mut message = String::new();
                while !message.ends_with("\r\n.\r\n") {
                    io.read_line(&mut message).await?;
                }
                inbox.lock().unwrap().push(message);
                if hangup {
                    return Ok(());
                }
                "250 queued\r\n"
            }
            // Never answered, as by some servers behind proxies: the mail counts as sent anyway.
            Some("QUIT") => std::future::pending().await,
            _ => "502 unknown\r\n",
        };
        io.write_all(reply.as_bytes()).await?;
    }
}
