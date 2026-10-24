//! Sending through SMTP with lettre, over our own connection so that it can go through the proxy.

use std::io;
use std::net::{Ipv4Addr, SocketAddr};
use std::pin::Pin;
use std::task::{Context, Poll};

use lettre::Message;
use lettre::message::header::ContentType;
use lettre::message::{Attachment, MultiPart};
use lettre::transport::smtp::authentication::{Credentials, Mechanism};
use lettre::transport::smtp::client::{AsyncSmtpConnection, AsyncTokioStream};
use lettre::transport::smtp::commands::{Data, Mail, Rcpt};
use lettre::transport::smtp::extension::{ClientId, MailParameter};
use tokio::io::{AsyncRead, AsyncWrite, ReadBuf};
use tokio::net::TcpStream;
use tokio_rustls::client::TlsStream;

use net::Net;

use crate::connect;
use crate::{Account, MailError, Security, Server};

/// Greets with an address rather than the computer's name, which in Chinese makes servers refuse
/// the greeting (docs/design.md#桌面端).
const HELLO: ClientId = ClientId::Ipv4(Ipv4Addr::LOCALHOST);

/// Logs in to the mailbox's SMTP server and leaves.
pub async fn check_sending(net: &Net, account: &Account) -> Result<(), MailError> {
    login(net, account).await?.quit();
    Ok(())
}

/// Sends a short email from the mailbox to itself.
pub async fn send_test(net: &Net, account: &Account) -> Result<(), MailError> {
    let me =
        lettre::message::Mailbox::new(Some(account.sender_name.clone()), account.address.clone());
    let message = Message::builder()
        .message_id(Some(message_id(account)))
        .from(me.clone())
        .to(me)
        .subject("Auto Email Sender 测试信")
        .header(ContentType::TEXT_PLAIN)
        .body(String::from(
            "这封信由 Auto Email Sender 发出，收到它说明这个邮箱能正常发信。",
        ))
        .map_err(|e| MailError::Broken(e.to_string()))?;
    login(net, account)
        .await?
        .send(&Outgoing(message))
        .await
        .map_err(|(SendError::NotSent(e) | SendError::Unknown(e))| e)
}

/// A new Message-ID for an email from this mailbox, named after its domain rather than the computer.
pub fn message_id(account: &Account) -> String {
    format!("<{}@{}>", uuid::Uuid::new_v4(), account.address.domain())
}

/// What an email to a mentor is made of.
pub struct Letter<'a> {
    pub message_id: &'a str,
    pub to_name: &'a str,
    pub to: &'a str,
    pub subject: &'a str,
    pub html: &'a str,
    pub text: &'a str,
    pub attachments: &'a [File],
}

/// A file sent with a letter, under the name the mentor sees.
pub struct File {
    pub name: String,
    pub data: Vec<u8>,
}

/// An email ready to hand to the server.
pub struct Outgoing(Message);

/// Puts an email from the mailbox together, with the letter as HTML and as plain text for mail apps
/// that show no HTML, and its files after.
pub fn compose(account: &Account, letter: &Letter) -> Result<Outgoing, MailError> {
    let to = letter
        .to
        .parse()
        .map_err(|_| MailError::Recipient(letter.to.to_owned()))?;
    let mut body =
        MultiPart::alternative_plain_html(letter.text.to_owned(), letter.html.to_owned());
    if !letter.attachments.is_empty() {
        let mut mixed = MultiPart::mixed().multipart(body);
        for file in letter.attachments {
            mixed = mixed.singlepart(
                Attachment::new(file.name.clone())
                    .body(file.data.clone(), content_type(&file.name)?),
            );
        }
        body = mixed;
    }
    Message::builder()
        .message_id(Some(letter.message_id.to_owned()))
        .from(lettre::message::Mailbox::new(
            Some(account.sender_name.clone()),
            account.address.clone(),
        ))
        .to(lettre::message::Mailbox::new(
            Some(letter.to_name.to_owned()),
            to,
        ))
        .subject(letter.subject)
        .multipart(body)
        .map(Outgoing)
        .map_err(|e| MailError::Broken(e.to_string()))
}

/// By the extension of a material's file: PDF, DOCX or DOC.
fn content_type(file_name: &str) -> Result<ContentType, MailError> {
    let extension = file_name
        .rsplit_once('.')
        .map(|(_, e)| e.to_ascii_lowercase());
    let mime = match extension.as_deref() {
        Some("pdf") => "application/pdf",
        Some("docx") => "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        Some("doc") => "application/msword",
        _ => "application/octet-stream",
    };
    ContentType::parse(mime).map_err(|e| MailError::Broken(e.to_string()))
}

/// Why an email may not have gone out.
#[derive(Debug)]
pub enum SendError {
    /// It did not go out: the server turned it down, or the connection failed before it was
    /// handed over.
    NotSent(MailError),
    /// The connection broke off or timed out while it was being handed over: it may have gone out.
    Unknown(MailError),
}

impl std::fmt::Display for SendError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            SendError::NotSent(e) => write!(f, "not sent: {e}"),
            SendError::Unknown(e) => write!(f, "may have gone out: {e}"),
        }
    }
}

impl SendError {
    /// What happened and what to do about it.
    pub fn message(&self) -> String {
        match self {
            SendError::NotSent(MailError::Refused(reply)) => format!(
                "服务器拒收了这封邮件（服务器回复：{reply}）。检查导师的邮箱地址有没有填错；回复里提到垃圾邮件或发送频率时，稍后再重试。"
            ),
            SendError::NotSent(e) => e.message(),
            SendError::Unknown(_) => "交给服务器的途中连接中断，无法确定是否已发出。".into(),
        }
    }
}

/// A connection to a mailbox's SMTP server, logged in.
pub struct Smtp {
    server: Server,
    conn: AsyncSmtpConnection,
}

/// Connects to the mailbox's SMTP server and logs in; nothing is sent yet.
pub async fn login(net: &Net, account: &Account) -> Result<Smtp, MailError> {
    let conn = connect::within(&account.smtp, open(net, account)).await?;
    Ok(Smtp {
        server: account.smtp.clone(),
        conn,
    })
}

impl Smtp {
    /// Hands the email over and leaves. Takes the steps one by one rather than with lettre's
    /// `send`, which on a refusal waits for the server to answer QUIT, and some never do.
    pub async fn send(self, email: &Outgoing) -> Result<(), SendError> {
        let Smtp { server, mut conn } = self;
        let envelope = email.0.envelope();
        let ready = connect::within(&server, async {
            // A server without SMTPUTF8 turns down addresses that are not ASCII.
            let utf8 = envelope
                .to()
                .iter()
                .chain(envelope.from())
                .any(|a| !(a.user().is_ascii() && a.domain().is_ascii()));
            let options = if utf8 {
                vec![MailParameter::SmtpUtfEight]
            } else {
                Vec::new()
            };
            conn.command(Mail::new(envelope.from().cloned(), options))
                .await
                .map_err(refused)?;
            for to in envelope.to() {
                conn.command(Rcpt::new(to.clone(), Vec::new()))
                    .await
                    .map_err(refused)?;
            }
            conn.command(Data).await.map_err(refused)
        })
        .await;
        if let Err(e) = ready {
            quit(&server.host, conn);
            return Err(SendError::NotSent(e));
        }
        // From here on, a server that got the whole email may have taken it, whatever happens next.
        let message = email.0.formatted();
        let sent = connect::uploading(&server, message.len(), async {
            conn.message(&message).await.map_err(refused)
        })
        .await;
        match sent {
            Ok(_) => {
                quit(&server.host, conn);
                Ok(())
            }
            Err(e @ MailError::Refused(_)) => {
                quit(&server.host, conn);
                Err(SendError::NotSent(e))
            }
            Err(e) => Err(SendError::Unknown(e)),
        }
    }

    pub fn quit(self) {
        quit(&self.server.host, self.conn);
    }
}

async fn open(net: &Net, account: &Account) -> Result<AsyncSmtpConnection, MailError> {
    let server = &account.smtp;
    let tcp = connect::connect(&server.host, server.port).await?;
    let mut conn = match server.security {
        Security::Tls => {
            let tls = connect::encrypt(net, &server.host, tcp).await?;
            AsyncSmtpConnection::connect_with_transport(Box::new(Tls(tls)), &HELLO)
                .await
                .map_err(refused)?
        }
        Security::Starttls => {
            let mut conn = AsyncSmtpConnection::connect_with_transport(Box::new(tcp), &HELLO)
                .await
                .map_err(refused)?;
            conn.starttls(connect::lettre_tls(net, &server.host)?, &HELLO)
                .await
                .map_err(|e| {
                    if e.status().is_some() {
                        refused(e)
                    } else {
                        // Without a reply to refuse with, what failed is the handshake.
                        MailError::Tls {
                            host: server.host.to_string(),
                            source: io::Error::other(e),
                        }
                    }
                })?;
            conn
        }
    };
    let credentials = Credentials::new(account.address.to_string(), account.secret.clone());
    conn.auth(&[Mechanism::Plain, Mechanism::Login], &credentials)
        .await
        .map_err(|e| match refused(e) {
            MailError::Refused(reply) => MailError::Login(reply),
            e => e,
        })?;
    Ok(conn)
}

fn quit(host: &str, mut conn: AsyncSmtpConnection) {
    connect::farewell(host, async move { conn.quit().await.map(drop) });
}

/// A reply from the server, worded by lettre like "permanent error (535): ...", is a refusal;
/// anything else means the connection broke.
fn refused(e: lettre::transport::smtp::Error) -> MailError {
    if e.status().is_some() {
        MailError::Refused(e.to_string())
    } else {
        MailError::Broken(e.to_string())
    }
}

/// A TLS connection lettre can drive; it takes only streams of its own trait.
#[derive(Debug)]
struct Tls(TlsStream<TcpStream>);

impl AsyncRead for Tls {
    fn poll_read(
        mut self: Pin<&mut Self>,
        cx: &mut Context<'_>,
        buf: &mut ReadBuf<'_>,
    ) -> Poll<io::Result<()>> {
        Pin::new(&mut self.0).poll_read(cx, buf)
    }
}

impl AsyncWrite for Tls {
    fn poll_write(
        mut self: Pin<&mut Self>,
        cx: &mut Context<'_>,
        buf: &[u8],
    ) -> Poll<io::Result<usize>> {
        Pin::new(&mut self.0).poll_write(cx, buf)
    }

    fn poll_flush(mut self: Pin<&mut Self>, cx: &mut Context<'_>) -> Poll<io::Result<()>> {
        Pin::new(&mut self.0).poll_flush(cx)
    }

    fn poll_shutdown(mut self: Pin<&mut Self>, cx: &mut Context<'_>) -> Poll<io::Result<()>> {
        Pin::new(&mut self.0).poll_shutdown(cx)
    }
}

impl AsyncTokioStream for Tls {
    fn peer_addr(&self) -> io::Result<SocketAddr> {
        self.0.get_ref().0.peer_addr()
    }
}
