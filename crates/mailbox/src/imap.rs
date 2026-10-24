//! Receiving through IMAP with async-imap, over our own connection so that it can go through the
//! proxy. Only standard commands, folders opened read-only and messages read with `BODY.PEEK`, so
//! that nothing changes in the user's mailbox (docs/design.md#桌面端).

use std::time::Duration;

use async_imap::imap_proto::{AttributeValue, MailboxDatum, Response, Status};
use async_imap::types::NameAttribute;
use async_imap::{Client, Session};
use tokio::net::TcpStream;
use tokio_rustls::client::TlsStream;

use net::Net;

use crate::connect;
use crate::{Account, MailError, Security, Server};

/// Logs in to the mailbox's IMAP server, opens the inbox and leaves.
pub async fn check_receiving(net: &Net, account: &Account) -> Result<(), MailError> {
    let mut imap = Imap::open(net, account).await?;
    imap.examine("INBOX").await?;
    imap.close();
    Ok(())
}

/// How long the server may take to answer a command, and to send each message of the answer.
const PATIENCE: Duration = Duration::from_secs(60);

/// Names a sent folder may have when the server does not mark it `\Sent`, in modified UTF-7.
const SENT_NAMES: [&str; 7] = [
    "Sent",
    "Sent Messages",
    "Sent Items",
    "Sent Mail",
    // 已发送
    "&XfJT0ZAB-",
    // 已发送邮件
    "&XfJT0ZABkK5O9g-",
    // 发件箱
    "&U9FO9nux-",
];

/// A logged-in IMAP session.
pub struct Imap {
    session: Session<TlsStream<TcpStream>>,
    server: Server,
}

/// A folder opened for reading.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Opened {
    pub uidvalidity: u32,
    /// The highest UID in the folder; 0 when it is empty.
    pub last_uid: u32,
}

/// What a message's headers were fetched with.
pub struct Head {
    pub uid: u32,
    /// When the server got the message, in UTC milliseconds.
    pub received_at: Option<i64>,
    pub header: Vec<u8>,
}

impl Imap {
    pub async fn open(net: &Net, account: &Account) -> Result<Imap, MailError> {
        let session = connect::within(&account.imap, login(net, account)).await?;
        Ok(Imap {
            session,
            server: account.imap.clone(),
        })
    }

    /// The folder sent mail is kept in: the one the server marks `\Sent`, or else the first with a
    /// usual name.
    pub async fn sent_folder(&mut self) -> Result<Option<String>, MailError> {
        let mut folders: Vec<(String, bool)> = Vec::new();
        self.command("LIST \"\" *", |response| {
            if let Response::MailboxData(MailboxDatum::List {
                name_attributes,
                name,
                ..
            }) = response
            {
                let sent = name_attributes.contains(&NameAttribute::Sent);
                folders.push((name.to_string(), sent));
            }
        })
        .await?;
        let marked = folders.iter().find(|(_, sent)| *sent);
        let named = || {
            SENT_NAMES
                .iter()
                .find_map(|usual| folders.iter().find(|(name, _)| name == usual))
        };
        Ok(marked.or_else(named).map(|(name, _)| name.clone()))
    }

    /// Opens a folder read-only.
    pub async fn examine(&mut self, folder: &str) -> Result<Opened, MailError> {
        let server = self.server.clone();
        let mailbox = patient(&server, self.session.examine(folder)).await?;
        let uidvalidity = mailbox
            .uid_validity
            .ok_or_else(|| MailError::Broken(format!("{folder} has no UIDVALIDITY")))?;
        let mut last_uid = 0;
        if mailbox.exists > 0 {
            self.command("FETCH * UID", |response| {
                // Changed flags of other messages may come too, with their UIDs.
                if let Response::Fetch(_, attributes) = response {
                    last_uid = last_uid.max(uid(attributes).unwrap_or(0));
                }
            })
            .await?;
            if last_uid == 0 {
                return Err(MailError::Broken(format!(
                    "{folder}: no UID for the last message"
                )));
            }
        }
        Ok(Opened {
            uidvalidity,
            last_uid,
        })
    }

    /// The UIDs in the open folder after `after`, up to `last`, in order. UIDs are not consecutive,
    /// so they are searched for, not counted.
    pub async fn uids_between(&mut self, after: u32, last: u32) -> Result<Vec<u32>, MailError> {
        self.search(&format!("UID {}:{last}", after + 1)).await
    }

    /// The UIDs of mail in the open folder since `since`, in UTC milliseconds, from any of
    /// `senders`.
    pub async fn sent_by(&mut self, senders: &[&str], since: i64) -> Result<Vec<u32>, MailError> {
        let keys = senders.iter().map(|a| format!("FROM {}", quoted(a)));
        self.search(&format!("SINCE {} {}", day(since), any(keys)))
            .await
    }

    /// The UIDs of mail in the open folder since `since` to, copied or blind-copied to any of
    /// `recipients`.
    pub async fn sent_to(
        &mut self,
        recipients: &[&str],
        since: i64,
    ) -> Result<Vec<u32>, MailError> {
        let keys = recipients.iter().map(|a| {
            let a = quoted(a);
            format!("(OR TO {a} OR CC {a} BCC {a})")
        });
        self.search(&format!("SINCE {} {}", day(since), any(keys)))
            .await
    }

    async fn search(&mut self, criteria: &str) -> Result<Vec<u32>, MailError> {
        let mut uids = Vec::new();
        self.command(&format!("UID SEARCH {criteria}"), |response| {
            if let Response::MailboxData(MailboxDatum::Search(found)) = response {
                uids.extend(found);
            }
        })
        .await?;
        uids.sort_unstable();
        Ok(uids)
    }

    /// These header fields of the messages with these UIDs in the open folder.
    pub async fn headers(&mut self, uids: &[u32], fields: &[&str]) -> Result<Vec<Head>, MailError> {
        let query = format!(
            "(UID INTERNALDATE BODY.PEEK[HEADER.FIELDS ({})])",
            fields.join(" ")
        );
        let fetches = self.fetch(uids, &query).await?;
        Ok(fetches
            .into_iter()
            .filter_map(|f| {
                Some(Head {
                    uid: f.uid,
                    received_at: f.received_at,
                    header: f.data?,
                })
            })
            .collect())
    }

    /// The first `limit` bytes of the messages with these UIDs in the open folder, by UID.
    pub async fn starts(
        &mut self,
        uids: &[u32],
        limit: u32,
    ) -> Result<Vec<(u32, Vec<u8>)>, MailError> {
        let fetches = self
            .fetch(uids, &format!("(UID BODY.PEEK[]<0.{limit}>)"))
            .await?;
        Ok(fetches
            .into_iter()
            .filter_map(|f| Some((f.uid, f.data?)))
            .collect())
    }

    /// Some servers leave messages out of the answer for many that they give for each alone; one
    /// left out of that too is taken to be deleted meanwhile.
    async fn fetch(&mut self, uids: &[u32], query: &str) -> Result<Vec<Fetched>, MailError> {
        let mut fetches = self.fetch_set(uids, query).await?;
        if uids.len() > 1 {
            let missing: Vec<u32> = uids
                .iter()
                .filter(|&&uid| !fetches.iter().any(|f| f.uid == uid && f.data.is_some()))
                .copied()
                .collect();
            for uid in missing {
                fetches.extend(self.fetch_set(&[uid], query).await?);
            }
            fetches.sort_by_key(|f| f.uid);
        }
        Ok(fetches)
    }

    /// A fetch asking for one section of each message.
    async fn fetch_set(&mut self, uids: &[u32], query: &str) -> Result<Vec<Fetched>, MailError> {
        let set: Vec<String> = uids.iter().map(u32::to_string).collect();
        let mut fetches = Vec::new();
        self.command(
            &format!("UID FETCH {} {query}", set.join(",")),
            |response| {
                let Response::Fetch(_, attributes) = response else {
                    return;
                };
                // Without a UID it tells of changed flags, unasked.
                let Some(uid) = uid(attributes) else {
                    return;
                };
                let mut fetched = Fetched {
                    uid,
                    received_at: None,
                    data: None,
                };
                for attribute in attributes {
                    match attribute {
                        AttributeValue::InternalDate(date) => {
                            fetched.received_at = chrono::DateTime::parse_from_str(
                                date.trim(),
                                "%d-%b-%Y %H:%M:%S %z",
                            )
                            .ok()
                            .map(|d| d.timestamp_millis());
                        }
                        AttributeValue::BodySection { data, .. } => {
                            fetched.data = data.as_deref().map(<[u8]>::to_vec);
                        }
                        _ => {}
                    }
                }
                fetches.push(fetched);
            },
        )
        .await?;
        Ok(fetches)
    }

    /// Runs a command, handing the server's untagged responses to `each`. async-imap's own commands
    /// for these, unlike EXAMINE, take the server refusing for an empty answer.
    async fn command(
        &mut self,
        command: &str,
        mut each: impl FnMut(&Response<'_>),
    ) -> Result<(), MailError> {
        let server = self.server.clone();
        let tag = patient(&server, self.session.run_command(command)).await?;
        loop {
            let response = tokio::time::timeout(PATIENCE, self.session.read_response())
                .await
                .map_err(|_| timed_out(&server))?
                .map_err(|e| MailError::Broken(e.to_string()))?
                .ok_or_else(|| MailError::Broken("the server closed the connection".into()))?;
            match response.parsed() {
                Response::Done {
                    tag: done,
                    status,
                    information,
                    ..
                } if *done == tag => {
                    return match status {
                        Status::Ok => Ok(()),
                        _ => Err(MailError::Refused(
                            information.as_deref().unwrap_or_default().to_owned(),
                        )),
                    };
                }
                response => each(response),
            }
        }
    }

    /// Logs out in the background.
    pub fn close(mut self) {
        connect::farewell(
            &self.server.host,
            async move { self.session.logout().await },
        );
    }
}

async fn login(net: &Net, account: &Account) -> Result<Session<TlsStream<TcpStream>>, MailError> {
    let server = &account.imap;
    let tcp = connect::connect(&server.host, server.port).await?;
    let client = match server.security {
        Security::Tls => {
            let mut client = Client::new(connect::encrypt(net, &server.host, tcp).await?);
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
            Client::new(connect::encrypt(net, &server.host, client.into_inner()).await?)
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

/// Waits for a command's answer, giving up when the server says nothing for `PATIENCE`.
async fn patient<T>(
    server: &Server,
    answer: impl Future<Output = async_imap::error::Result<T>>,
) -> Result<T, MailError> {
    tokio::time::timeout(PATIENCE, answer)
        .await
        .map_err(|_| timed_out(server))?
        .map_err(refused)
}

/// What the server gave of a message.
struct Fetched {
    uid: u32,
    /// UTC milliseconds.
    received_at: Option<i64>,
    data: Option<Vec<u8>>,
}

fn uid(attributes: &[AttributeValue<'_>]) -> Option<u32> {
    attributes.iter().find_map(|a| match a {
        AttributeValue::Uid(uid) => Some(*uid),
        _ => None,
    })
}

fn timed_out(server: &Server) -> MailError {
    MailError::TimedOut {
        host: server.host.to_string(),
        port: server.port,
    }
}

/// A search for mail matching any of these keys.
fn any(keys: impl DoubleEndedIterator<Item = String>) -> String {
    let mut keys = keys.rev();
    let last = keys.next().unwrap_or_default();
    keys.fold(last, |rest, key| format!("OR {key} {rest}"))
}

/// A day as IMAP searches name it, such as `01-Jan-2026`, in English whatever the system's
/// language.
fn day(ms: i64) -> String {
    chrono::DateTime::from_timestamp_millis(ms)
        .unwrap_or_default()
        .format("%d-%b-%Y")
        .to_string()
}

/// A string for a search command.
fn quoted(s: &str) -> String {
    format!("\"{}\"", s.replace('\\', "\\\\").replace('"', "\\\""))
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
