//! Receiving through IMAP with async-imap, over our own connection so that it can go through the
//! proxy.

use async_imap::{Client, Session};
use tokio::net::TcpStream;
use tokio_rustls::client::TlsStream;

use crate::net::{self, Net};
use crate::{Account, MailError, Security};

/// Logs in to the mailbox's IMAP server, opens the inbox and leaves.
pub async fn check_receiving(net: &Net, account: &Account) -> Result<(), MailError> {
    let mut session = net::within(&account.imap, async {
        let mut session = login(net, account).await?;
        session.select("INBOX").await.map_err(refused)?;
        Ok(session)
    })
    .await?;
    net::farewell(&account.imap.host, async move { session.logout().await });
    Ok(())
}

async fn login(net: &Net, account: &Account) -> Result<Session<TlsStream<TcpStream>>, MailError> {
    let server = &account.imap;
    let tcp = net::connect(&server.host, server.port).await?;
    let client = match server.security {
        Security::Tls => {
            let mut client = Client::new(net.tls(&server.host, tcp).await?);
            greeting(&mut client).await?;
            client
        }
        Security::Starttls => {
            let mut client = Client::new(tcp);
            greeting(&mut client).await?;
            client
                .run_command_and_check_ok("STARTTLS", None)
                .await
                .map_err(refused)?;
            Client::new(net.tls(&server.host, client.into_inner()).await?)
        }
    };
    let mut session = client
        .login(account.address.to_string(), &account.secret)
        .await
        .map_err(|(e, _)| match refused(e) {
            MailError::Refused(reply) => MailError::Login(reply),
            e => e,
        })?;
    // Some servers, such as 163's, refuse to open folders for clients that do not say who they are.
    if session.capabilities().await.map_err(refused)?.has_str("ID") {
        session
            .id([
                ("name", Some("Auto Email Sender")),
                ("version", Some(env!("CARGO_PKG_VERSION"))),
            ])
            .await
            .map_err(refused)?;
    }
    Ok(session)
}

async fn greeting<T>(client: &mut Client<T>) -> Result<(), MailError>
where
    T: tokio::io::AsyncRead + tokio::io::AsyncWrite + Unpin + std::fmt::Debug + Send,
{
    match client.read_response().await {
        Ok(Some(_)) => Ok(()),
        Ok(None) => Err(MailError::Broken(
            "connection closed before the greeting".into(),
        )),
        Err(e) => Err(MailError::Broken(e.to_string())),
    }
}

/// A NO or BAD from the server is a refusal; anything else means the connection broke.
fn refused(e: async_imap::error::Error) -> MailError {
    match e {
        async_imap::error::Error::No(reply) | async_imap::error::Error::Bad(reply) => {
            MailError::Refused(server_words(reply))
        }
        e => MailError::Broken(e.to_string()),
    }
}

/// The server's own words in a refusal, which async-imap hands over only inside its debug wording,
/// `code: Some(..), info: Some("..")`.
fn server_words(reply: String) -> String {
    reply
        .split_once("info: Some(\"")
        .and_then(|(_, words)| words.strip_suffix("\")"))
        .map(|words| words.replace("\\\"", "\"").replace("\\\\", "\\"))
        .unwrap_or(reply)
}
