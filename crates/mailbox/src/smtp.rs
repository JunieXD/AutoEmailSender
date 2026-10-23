//! Sending through SMTP with lettre, over our own connection so that it can go through the proxy.

use std::io;
use std::net::{Ipv4Addr, SocketAddr};
use std::pin::Pin;
use std::task::{Context, Poll};

use lettre::Message;
use lettre::message::header::ContentType;
use lettre::transport::smtp::authentication::{Credentials, Mechanism};
use lettre::transport::smtp::client::{AsyncSmtpConnection, AsyncTokioStream};
use lettre::transport::smtp::extension::ClientId;
use tokio::io::{AsyncRead, AsyncWrite, ReadBuf};
use tokio::net::TcpStream;
use tokio_rustls::client::TlsStream;

use net::Net;

use crate::connect;
use crate::{Account, MailError, Security};

/// Greets with an address rather than the computer's name, which in Chinese makes servers refuse
/// the greeting (docs/design.md#桌面端).
const HELLO: ClientId = ClientId::Ipv4(Ipv4Addr::LOCALHOST);

/// Logs in to the mailbox's SMTP server and leaves.
pub async fn check_sending(net: &Net, account: &Account) -> Result<(), MailError> {
    let conn = connect::within(&account.smtp, login(net, account)).await?;
    quit(&account.smtp.host, conn);
    Ok(())
}

/// Sends a short email from the mailbox to itself.
pub async fn send_test(net: &Net, account: &Account) -> Result<(), MailError> {
    let me =
        lettre::message::Mailbox::new(Some(account.sender_name.clone()), account.address.clone());
    let message = Message::builder()
        .message_id(Some(format!(
            "<{}@{}>",
            uuid::Uuid::new_v4(),
            account.address.domain()
        )))
        .from(me.clone())
        .to(me)
        .subject("Auto Email Sender 测试信")
        .header(ContentType::TEXT_PLAIN)
        .body(String::from(
            "这封信由 Auto Email Sender 发出，收到它说明这个邮箱能正常发信。",
        ))
        .map_err(|e| MailError::Broken(e.to_string()))?;
    let conn = connect::within(&account.smtp, async {
        let mut conn = login(net, account).await?;
        conn.send(message.envelope(), &message.formatted())
            .await
            .map_err(refused)?;
        Ok(conn)
    })
    .await?;
    quit(&account.smtp.host, conn);
    Ok(())
}

async fn login(net: &Net, account: &Account) -> Result<AsyncSmtpConnection, MailError> {
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
