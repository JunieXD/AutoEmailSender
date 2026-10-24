//! A fake IMAP server with folders of mail that tests fill and inspect. It speaks as much IMAP as
//! the app uses: folders with special-use flags, read-only opening, UID searches and fetches of
//! header fields and of a message or its start.

use std::sync::{Arc, Mutex};

use tokio::io::{AsyncBufReadExt, AsyncWriteExt, BufReader};
use tokio_rustls::TlsAcceptor;

use crate::{ADDRESS, Pki, SECRET, Stream, listen};

/// The mail on the fake server.
#[derive(Clone)]
pub struct Store(Arc<Mutex<State>>);

struct State {
    folders: Vec<Folder>,
    /// Every command received, without its tag.
    commands: Vec<String>,
    /// The start of the next command to refuse, as a server limiting how much is asked of it.
    refuse: Option<String>,
    /// A message left out of the answers to fetches of several, as some servers do.
    left_out: Option<u32>,
}

struct Folder {
    name: String,
    sent: bool,
    uidvalidity: u32,
    next_uid: u32,
    mail: Vec<Mail>,
}

struct Mail {
    uid: u32,
    /// When the server got it, UTC.
    received: time::OffsetDateTime,
    raw: Vec<u8>,
}

impl Store {
    /// Adds a folder; `sent` marks it `\Sent`. Its name is as the server sends it, in modified
    /// UTF-7.
    pub fn create(&self, name: &str, sent: bool) {
        self.0.lock().unwrap().folders.push(Folder {
            name: name.into(),
            sent,
            uidvalidity: 1,
            next_uid: 1,
            mail: Vec::new(),
        });
    }

    /// Puts a message in a folder as received at `at`, in UTC milliseconds, and returns its UID.
    pub fn deliver(&self, folder: &str, at: i64, raw: &str) -> u32 {
        let mut state = self.0.lock().unwrap();
        let folder = state.folders.iter_mut().find(|f| f.name == folder).unwrap();
        let uid = folder.next_uid;
        // Far apart, as some servers' UIDs are.
        folder.next_uid += 1000;
        folder.mail.push(Mail {
            uid,
            received: time::OffsetDateTime::from_unix_timestamp_nanos(i128::from(at) * 1_000_000)
                .unwrap(),
            raw: raw.replace('\n', "\r\n").into_bytes(),
        });
        uid
    }

    /// Numbers a folder's mail afresh under a new UIDVALIDITY, as a server that rebuilt it.
    pub fn renumber(&self, folder: &str) {
        let mut state = self.0.lock().unwrap();
        let folder = state.folders.iter_mut().find(|f| f.name == folder).unwrap();
        folder.uidvalidity += 1;
        for (i, mail) in folder.mail.iter_mut().enumerate() {
            mail.uid = u32::try_from(i).unwrap() + 1;
        }
        folder.next_uid = u32::try_from(folder.mail.len()).unwrap() + 1;
    }

    /// Refuses the next command that starts with these words.
    pub fn refuse(&self, command: &str) {
        self.0.lock().unwrap().refuse = Some(command.into());
    }

    /// Leaves the message with this UID out of the answers to fetches of several.
    pub fn leave_out(&self, uid: u32) {
        self.0.lock().unwrap().left_out = Some(uid);
    }

    /// The commands received so far, without tags, and forgets them.
    pub fn commands(&self) -> Vec<String> {
        std::mem::take(&mut self.0.lock().unwrap().commands)
    }
}

/// An IMAP server with an empty INBOX. Like 163's, it opens no folder before the client sends ID.
pub async fn imap(pki: &Pki, starttls: bool) -> (u16, Store) {
    let store = Store(Arc::new(Mutex::new(State {
        folders: Vec::new(),
        commands: Vec::new(),
        refuse: None,
        left_out: None,
    })));
    store.create("INBOX", false);
    let tls = pki.tls.clone();
    let served = store.clone();
    let port = listen(&pki.tls, starttls, move |io| {
        session(io, tls.clone(), served.clone())
    })
    .await;
    (port, store)
}

async fn session(
    mut io: BufReader<Box<dyn Stream>>,
    tls: TlsAcceptor,
    store: Store,
) -> std::io::Result<()> {
    let login = format!("\"{ADDRESS}\" \"{SECRET}\"");
    let mut identified = false;
    let mut open: Option<String> = None;
    io.write_all(b"* OK fake IMAP ready\r\n").await?;
    loop {
        let mut line = String::new();
        if io.read_line(&mut line).await? == 0 {
            return Ok(());
        }
        let line = line.trim_end();
        let (tag, command) = line.split_once(' ').unwrap_or((line, ""));
        let refused = {
            let mut state = store.0.lock().unwrap();
            state.commands.push(command.into());
            let refused = state
                .refuse
                .as_ref()
                .is_some_and(|r| command.starts_with(r.as_str()));
            if refused {
                state.refuse = None;
            }
            refused
        };
        let (verb, rest) = command.split_once(' ').unwrap_or((command, ""));
        let reply: Vec<u8> = match verb.to_ascii_uppercase().as_str() {
            _ if refused => {
                format!("{tag} NO [LIMIT] too many commands, try again later\r\n").into()
            }
            "CAPABILITY" => {
                format!("* CAPABILITY IMAP4rev1 ID STARTTLS\r\n{tag} OK done\r\n").into()
            }
            "STARTTLS" => {
                io.write_all(format!("{tag} OK go ahead\r\n").as_bytes())
                    .await?;
                io = BufReader::new(Box::new(tls.accept(io.into_inner()).await?));
                continue;
            }
            "LOGIN" if rest == login => format!("{tag} OK logged in\r\n").into(),
            "LOGIN" => format!("{tag} NO [AUTHENTICATIONFAILED] invalid credentials\r\n").into(),
            "ID" => {
                identified = true;
                format!("* ID NIL\r\n{tag} OK done\r\n").into()
            }
            "SELECT" | "EXAMINE" if !identified => format!("{tag} NO Unsafe Login\r\n").into(),
            "SELECT" | "EXAMINE" => {
                let name = rest.trim_matches('"');
                let state = store.0.lock().unwrap();
                match state.folders.iter().find(|f| f.name == name) {
                    Some(f) => {
                        open = Some(name.into());
                        format!(
                            "* {} EXISTS\r\n* OK [UIDVALIDITY {}] ok\r\n{tag} OK [READ-ONLY] done\r\n",
                            f.mail.len(),
                            f.uidvalidity
                        )
                        .into()
                    }
                    None => format!("{tag} NO no such folder\r\n").into(),
                }
            }
            "LIST" => {
                let state = store.0.lock().unwrap();
                let mut reply = String::new();
                for f in &state.folders {
                    let flags = if f.sent {
                        "\\HasNoChildren \\Sent"
                    } else {
                        "\\HasNoChildren"
                    };
                    reply += &format!("* LIST ({flags}) \"/\" \"{}\"\r\n", f.name);
                }
                format!("{reply}{tag} OK done\r\n").into()
            }
            // The UID of the last message.
            "FETCH" if rest == "* UID" => {
                let state = store.0.lock().unwrap();
                let folder = folder(&state, open.as_deref());
                let last = folder.mail.last().unwrap();
                format!(
                    "* {} FETCH (UID {})\r\n{tag} OK done\r\n",
                    folder.mail.len(),
                    last.uid
                )
                .into()
            }
            "UID" => {
                let (verb, rest) = rest.split_once(' ').unwrap();
                let state = store.0.lock().unwrap();
                let folder = folder(&state, open.as_deref());
                match verb.to_ascii_uppercase().as_str() {
                    "SEARCH" => {
                        let tokens = tokens(rest);
                        let uids: Vec<String> = folder
                            .mail
                            .iter()
                            .filter(|m| all(&mut tokens.iter().peekable(), m))
                            .map(|m| m.uid.to_string())
                            .collect();
                        format!("* SEARCH {}\r\n{tag} OK done\r\n", uids.join(" ")).into()
                    }
                    "FETCH" => {
                        let (set, items) = rest.split_once(' ').unwrap();
                        let several = set.contains(',');
                        let mut reply = Vec::new();
                        for (i, m) in folder.mail.iter().enumerate() {
                            if in_set(set, m.uid) && !(several && state.left_out == Some(m.uid)) {
                                reply.extend(fetched(i + 1, m, items));
                            }
                        }
                        reply.extend(format!("{tag} OK done\r\n").bytes());
                        reply
                    }
                    _ => format!("{tag} BAD unknown\r\n").into(),
                }
            }
            // Never answered: the work is done by then.
            "LOGOUT" => std::future::pending().await,
            _ => format!("{tag} BAD unknown\r\n").into(),
        };
        io.write_all(&reply).await?;
    }
}

fn folder<'a>(state: &'a State, open: Option<&str>) -> &'a Folder {
    state
        .folders
        .iter()
        .find(|f| Some(f.name.as_str()) == open)
        .unwrap()
}

/// The FETCH response for one message: its UID, and what else was asked for of `INTERNALDATE`,
/// header fields and the start of the message.
fn fetched(seq: usize, m: &Mail, items: &str) -> Vec<u8> {
    let mut reply = format!("* {seq} FETCH (UID {}", m.uid).into_bytes();
    if items.contains("INTERNALDATE") {
        let format = time::macros::format_description!(
            "[day]-[month repr:short]-[year] [hour]:[minute]:[second] +0000"
        );
        reply.extend(format!(" INTERNALDATE \"{}\"", m.received.format(format).unwrap()).bytes());
    }
    if let Some((_, fields)) = items.split_once("HEADER.FIELDS (") {
        let (fields, _) = fields.split_once(')').unwrap();
        let wanted: Vec<String> = fields.split(' ').map(str::to_ascii_lowercase).collect();
        let raw = String::from_utf8_lossy(&m.raw);
        let (head, _) = raw.split_once("\r\n\r\n").unwrap_or((&raw, ""));
        let mut picked = String::new();
        let mut keep = false;
        for line in head.split("\r\n") {
            if !line.starts_with([' ', '\t']) {
                let name = line.split(':').next().unwrap().to_ascii_lowercase();
                keep = wanted.contains(&name);
            }
            if keep {
                picked += line;
                picked += "\r\n";
            }
        }
        picked += "\r\n";
        reply.extend(format!(" BODY[HEADER.FIELDS ({fields})] {{{}}}\r\n", picked.len()).bytes());
        reply.extend(picked.bytes());
    }
    if let Some((_, partial)) = items.split_once("BODY.PEEK[]<0.") {
        let (limit, _) = partial.split_once('>').unwrap();
        let start = &m.raw[..m.raw.len().min(limit.parse().unwrap())];
        reply.extend(format!(" BODY[]<0> {{{}}}\r\n", start.len()).bytes());
        reply.extend(start);
    }
    reply.extend(b")\r\n");
    reply
}

/// A search command's words, with quoted strings unquoted and parentheses on their own.
fn tokens(criteria: &str) -> Vec<String> {
    let mut tokens = Vec::new();
    let mut chars = criteria.chars().peekable();
    while let Some(c) = chars.next() {
        match c {
            ' ' => {}
            '(' | ')' => tokens.push(c.to_string()),
            '"' => {
                let mut word = String::new();
                while let Some(c) = chars.next() {
                    match c {
                        '\\' => word.push(chars.next().unwrap()),
                        '"' => break,
                        c => word.push(c),
                    }
                }
                tokens.push(word);
            }
            c => {
                let mut word = c.to_string();
                while let Some(&c) = chars.peek() {
                    if matches!(c, ' ' | '(' | ')') {
                        break;
                    }
                    word.push(c);
                    chars.next();
                }
                tokens.push(word);
            }
        }
    }
    tokens
}

type Tokens<'a> = std::iter::Peekable<std::slice::Iter<'a, String>>;

/// Whether every search key that is left matches, up to a closing parenthesis.
fn all(tokens: &mut Tokens, m: &Mail) -> bool {
    let mut matched = true;
    while tokens.peek().is_some_and(|t| *t != ")") {
        matched &= key(tokens, m);
    }
    tokens.next();
    matched
}

/// Whether the next search key matches.
fn key(tokens: &mut Tokens, m: &Mail) -> bool {
    let word = tokens.next().unwrap();
    match word.to_ascii_uppercase().as_str() {
        "(" => all(tokens, m),
        "OR" => {
            let (a, b) = (key(tokens, m), key(tokens, m));
            a || b
        }
        "ALL" => true,
        "UID" => in_set(tokens.next().unwrap(), m.uid),
        "SINCE" => {
            let format = time::macros::format_description!("[day]-[month repr:short]-[year]");
            m.received.date() >= time::Date::parse(tokens.next().unwrap(), format).unwrap()
        }
        field @ ("FROM" | "TO" | "CC" | "BCC") => {
            let wanted = tokens.next().unwrap().to_ascii_lowercase();
            let raw = String::from_utf8_lossy(&m.raw).to_ascii_lowercase();
            let (head, _) = raw.split_once("\r\n\r\n").unwrap_or((&raw, ""));
            let prefix = format!("{}:", field.to_ascii_lowercase());
            head.replace("\r\n ", " ")
                .replace("\r\n\t", " ")
                .split("\r\n")
                .any(|line| {
                    line.strip_prefix(&prefix)
                        .is_some_and(|v| v.contains(&wanted))
                })
        }
        other => panic!("search key {other} is not faked"),
    }
}

/// Whether `uid` is in a set such as `3`, `2:7`, `5:*` or `1,4`.
fn in_set(set: &str, uid: u32) -> bool {
    set.split(',').any(|range| match range.split_once(':') {
        Some((from, "*")) => uid >= from.parse().unwrap(),
        Some((from, to)) => (from.parse().unwrap()..=to.parse().unwrap()).contains(&uid),
        None => range.parse() == Ok(uid),
    })
}
