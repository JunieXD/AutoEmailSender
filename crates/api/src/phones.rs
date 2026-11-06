//! Using the app from a phone (docs/design.md#手机远程): pairing, the connection to the relay and
//! each phone's session over it. A phone calls the same operations as the window, except those
//! only the window may call (`crate::window_only`).

use crate::{CallError, Context, Nothing};
use base64::prelude::{BASE64_URL_SAFE_NO_PAD, Engine};
use cloud_api::relay::{self, close};
use remote::{Event as RelayEvent, Hello, Session};
use serde::{Deserialize, Serialize};
use serde_json::{Value, json, value::RawValue};
use std::{
    collections::HashMap,
    sync::{Arc, Mutex, MutexGuard, OnceLock},
    time::Duration,
};
use tokio::{
    sync::{mpsc, watch},
    time::Instant,
};
use ts_rs::TS;

/// How long a pairing code works, and how long the user has to answer the phone that used it.
const PAIRING_FOR: Duration = Duration::from_secs(5 * 60);
/// How long a phone may take from connecting to saying what it is.
const HANDSHAKE: Duration = Duration::from_secs(30);
pub const MAX_PHONES: i64 = 5;

pub enum Event {
    /// The phone with the code on screen asks to pair, under this name; none when it left before
    /// the user answered.
    Pairing(Option<String>),
    /// The connection to the relay, the paired phones or which of them are online changed.
    Changed,
    /// A phone's call went through; what it changed may be on screen.
    Called,
}

#[derive(Default)]
pub struct Phones {
    state: Mutex<State>,
    events: OnceLock<Box<dyn Fn(Event) + Send + Sync>>,
    commands: OnceLock<mpsc::UnboundedSender<Command>>,
}

#[derive(Default)]
struct State {
    status: RelayStatus,
    pairing: Option<Pairing>,
    /// How many connections each paired phone has now.
    online: HashMap<String, usize>,
}

struct Pairing {
    /// The room the code was made for; a later login's room is not it.
    room: String,
    device: String,
    key: [u8; 32],
    stage: Stage,
}

enum Stage {
    /// The code is on screen until then.
    Shown(Instant),
    /// A phone connected with the code, which no other may use now.
    Used(u32),
    /// The phone said what it is; waiting for the user's answer.
    Asked { connection: u32, name: String },
}

enum Command {
    /// Phone access, or the login it connects with, may have changed.
    Changed,
    Push(Arc<[u8]>),
    Revoked(String),
    Answered {
        connection: u32,
        allowed: bool,
    },
    Close(u32),
}

impl Phones {
    fn state(&self) -> MutexGuard<'_, State> {
        self.state
            .lock()
            .unwrap_or_else(|_| unreachable!("phone state updates cannot panic while holding it"))
    }

    fn emit(&self, event: Event) {
        if let Some(emit) = self.events.get() {
            emit(event);
        }
    }

    fn command(&self, command: Command) {
        if let Some(commands) = self.commands.get() {
            // Only fails once the app is closing.
            let _ = commands.send(command);
        }
    }

    /// Passes an event the window gets on to the phones; `payload` is its JSON.
    pub fn push(&self, event: &str, payload: &str) {
        let payload = RawValue::from_string(payload.into())
            .unwrap_or_else(|_| unreachable!("event payloads are serialized JSON"));
        let message = json!({ "event": event, "payload": payload }).to_string();
        self.command(Command::Push(message.into_bytes().into()));
    }

    fn set_status(&self, status: RelayStatus) {
        self.state().status = status;
        self.emit(Event::Changed);
    }

    /// Makes the code on screen useless, and turns away the phone pairing with it.
    fn end_pairing(&self) {
        let Some(pairing) = self.state().pairing.take() else {
            return;
        };
        if let Some(connection) = pairing.stage.connection() {
            self.command(Command::Close(connection));
        }
        self.emit(Event::Pairing(None));
    }
}

#[derive(Debug, Default, Clone, Serialize, TS)]
#[serde(tag = "kind", rename_all = "camelCase")]
pub enum RelayStatus {
    /// Phone access is off, or nobody is logged in.
    #[default]
    Off,
    Connecting,
    Online,
    /// Trying again soon.
    Failed {
        reason: String,
    },
}

#[derive(Serialize, TS)]
pub struct PhoneAccess {
    pub enabled: bool,
    pub status: RelayStatus,
}

#[derive(Deserialize, TS)]
pub struct PhoneAccessEdit {
    pub enabled: bool,
}

pub async fn get_phone_access(ctx: &Context, _: Nothing) -> Result<PhoneAccess, CallError> {
    Ok(PhoneAccess {
        enabled: profile::phone_access(&ctx.db).await?,
        status: ctx.phones.state().status.clone(),
    })
}

pub async fn set_phone_access(ctx: &Context, input: PhoneAccessEdit) -> Result<(), CallError> {
    profile::set_phone_access(&ctx.db, input.enabled).await?;
    if !input.enabled {
        ctx.phones.end_pairing();
    }
    ctx.phones.command(Command::Changed);
    Ok(())
}

#[derive(Serialize, TS)]
#[serde(rename_all = "camelCase")]
pub struct PairedPhone {
    pub id: String,
    pub name: String,
    pub paired_at: i64,
    pub seen_at: Option<i64>,
    pub online: bool,
}

pub async fn list_phones(ctx: &Context, _: Nothing) -> Result<Vec<PairedPhone>, CallError> {
    let rows = sqlx::query!(
        r#"SELECT id AS "id!", name, paired_at, seen_at FROM phone ORDER BY paired_at"#
    )
    .fetch_all(&ctx.db)
    .await?;
    let state = ctx.phones.state();
    Ok(rows
        .into_iter()
        .map(|row| PairedPhone {
            online: state.online.contains_key(&row.id),
            id: row.id,
            name: row.name,
            paired_at: row.paired_at,
            seen_at: row.seen_at,
        })
        .collect())
}

#[derive(Deserialize, TS)]
pub struct PhoneId {
    pub id: String,
}

pub async fn revoke_phone(ctx: &Context, input: PhoneId) -> Result<(), CallError> {
    sqlx::query!("DELETE FROM phone WHERE id = ?", input.id)
        .execute(&ctx.db)
        .await?;
    ctx.phones.command(Command::Revoked(input.id));
    ctx.phones.emit(Event::Changed);
    Ok(())
}

/// The code a phone scans to pair: the address it opens, with the pairing key after `#`.
#[derive(Serialize, TS)]
#[serde(rename_all = "camelCase")]
pub struct PhoneCode {
    pub url: String,
    pub svg: String,
    pub expires_at: i64,
}

pub async fn add_phone(ctx: &Context, _: Nothing) -> Result<PhoneCode, CallError> {
    if !profile::phone_access(&ctx.db).await? {
        return Err(CallError::invalid("请先打开“允许手机连接”。".into()));
    }
    if paired_count(&ctx.db).await? >= MAX_PHONES {
        return Err(CallError::invalid(format!(
            "最多配对 {MAX_PHONES} 部手机，先撤销一部。"
        )));
    }
    let room = room(&ctx.db).await?.ok_or(account::Error::SignedOut)?;
    let (device, key) = (random_id()?, random_key()?);
    let url = format!(
        "{}/m/#p={room}.{device}.{}",
        ctx.account.address(),
        BASE64_URL_SAFE_NO_PAD.encode(key)
    );
    let svg = qrcode::QrCode::new(&url)
        .map_err(CallError::internal)?
        .render::<qrcode::render::svg::Color>()
        .min_dimensions(240, 240)
        .build();
    let replaced = ctx.phones.state().pairing.replace(Pairing {
        room,
        device,
        key,
        stage: Stage::Shown(Instant::now() + PAIRING_FOR),
    });
    if let Some(connection) = replaced.and_then(|p| p.stage.connection()) {
        ctx.phones.command(Command::Close(connection));
    }
    Ok(PhoneCode {
        url,
        svg,
        expires_at: crate::now() + PAIRING_FOR.as_millis() as i64,
    })
}

impl Stage {
    fn connection(&self) -> Option<u32> {
        match self {
            Stage::Shown(_) => None,
            Stage::Used(connection) | Stage::Asked { connection, .. } => Some(*connection),
        }
    }
}

pub async fn cancel_phone_pairing(ctx: &Context, _: Nothing) -> Result<(), CallError> {
    ctx.phones.end_pairing();
    Ok(())
}

#[derive(Deserialize, TS)]
pub struct PhonePairingAnswer {
    pub allow: bool,
}

pub async fn answer_phone_pairing(
    ctx: &Context,
    input: PhonePairingAnswer,
) -> Result<(), CallError> {
    // Taking the pairing decides it: the phone, past its time meanwhile, waits for this answer.
    let (room, device, key, connection, name) = {
        let mut state = ctx.phones.state();
        match state.pairing.take() {
            Some(Pairing {
                room,
                device,
                key,
                stage: Stage::Asked { connection, name },
            }) => (room, device, key, connection, name),
            other => {
                state.pairing = other;
                return Err(CallError::invalid(
                    "手机已经断开，请重新生成二维码。".into(),
                ));
            }
        }
    };
    let saved = if input.allow {
        save_phone(&ctx.db, &room, &device, &name, &key)
            .await
            .map(|()| true)
    } else {
        Ok(false)
    };
    // The phone hears the answer however it went, so that it never waits for another.
    ctx.phones.command(Command::Answered {
        connection,
        allowed: matches!(saved, Ok(true)),
    });
    ctx.phones.emit(Event::Changed);
    saved.map(drop)
}

async fn save_phone(
    db: &sqlx::SqlitePool,
    room: &str,
    device: &str,
    name: &str,
    key: &[u8; 32],
) -> Result<(), CallError> {
    // A phone may have been paired from another code meanwhile.
    if paired_count(db).await? >= MAX_PHONES {
        return Err(CallError::invalid(format!(
            "最多配对 {MAX_PHONES} 部手机，先撤销一部。"
        )));
    }
    let key = key.as_slice();
    let now = crate::now();
    // Another account logging in meanwhile cleared the room, with the phones paired to it.
    let saved = sqlx::query!(
        "INSERT INTO phone (id, name, key, paired_at)
        SELECT ?, ?, ?, ? FROM profile WHERE phone_room = ?",
        device,
        name,
        key,
        now,
        room
    )
    .execute(db)
    .await?;
    if saved.rows_affected() == 0 {
        return Err(CallError::invalid(
            "登录的账号变了，请重新添加这部手机。".into(),
        ));
    }
    Ok(())
}

async fn paired_count(db: &sqlx::SqlitePool) -> Result<i64, sqlx::Error> {
    sqlx::query_scalar!("SELECT count(*) FROM phone")
        .fetch_one(db)
        .await
}

/// The room of the logged-in account, made when it is first needed; none when nobody is logged in.
async fn room(db: &sqlx::SqlitePool) -> Result<Option<String>, CallError> {
    let mut tx = db.begin_with("BEGIN IMMEDIATE").await?;
    let row = sqlx::query!(
        r#"SELECT a.server_id, p.phone_room
        FROM account a, profile p
        WHERE a.token IS NOT NULL"#
    )
    .fetch_optional(&mut *tx)
    .await?;
    let Some(row) = row else {
        return Ok(None);
    };
    let room = match row.phone_room {
        Some(room) => room,
        None => {
            let room = random_id()?;
            sqlx::query!(
                "UPDATE profile SET phone_room = ?, phone_room_account = ?",
                room,
                row.server_id
            )
            .execute(&mut *tx)
            .await?;
            room
        }
    };
    tx.commit().await?;
    Ok(Some(room))
}

fn random_id() -> Result<String, CallError> {
    let mut bytes = [0; 16];
    getrandom::fill(&mut bytes).map_err(CallError::internal)?;
    Ok(bytes.iter().map(|b| format!("{b:02x}")).collect())
}

fn random_key() -> Result<[u8; 32], CallError> {
    let mut key = [0; 32];
    getrandom::fill(&mut key).map_err(CallError::internal)?;
    Ok(key)
}

/// A name for the phone from its browser's User-Agent, such as "iPhone · Safari".
fn phone_name(user_agent: &str) -> String {
    let device = [
        ("iPhone", "iPhone"),
        ("iPad", "iPad"),
        ("HarmonyOS", "华为"),
        ("Android", "Android 手机"),
        ("Windows", "Windows 电脑"),
        ("Macintosh", "Mac"),
    ]
    .into_iter()
    .find(|(token, _)| user_agent.contains(token))
    .map_or("手机", |(_, name)| name);
    // Browsers built on others name those too, so the specific ones come first.
    let browser = [
        ("MicroMessenger", "微信"),
        ("QQ/", "QQ"),
        ("EdgA", "Edge"),
        ("EdgiOS", "Edge"),
        ("Edg/", "Edge"),
        ("FxiOS", "Firefox"),
        ("Firefox", "Firefox"),
        ("CriOS", "Chrome"),
        ("Chrome", "Chrome"),
        ("Safari", "Safari"),
    ]
    .into_iter()
    .find(|(token, _)| user_agent.contains(token))
    .map_or("浏览器", |(_, name)| name);
    format!("{device} · {browser}")
}

/// Starts the connection to the relay, kept while phone access is on and someone is logged in.
pub fn start(
    ctx: Arc<Context>,
    events: impl Fn(Event) + Send + Sync + 'static,
) -> tokio::task::JoinHandle<()> {
    let (commands, receiver) = mpsc::unbounded_channel();
    if ctx.phones.events.set(Box::new(events)).is_err()
        || ctx.phones.commands.set(commands).is_err()
    {
        unreachable!("the app starts phone remote once");
    }
    tokio::spawn(run(ctx, receiver))
}

/// After a failed connection, waits 1, 2, 4… seconds, at most a minute.
const LONGEST_WAIT: Duration = Duration::from_secs(60);

async fn run(ctx: Arc<Context>, mut commands: mpsc::UnboundedReceiver<Command>) {
    let mut account = ctx.account.subscribe();
    let mut wait = Duration::from_secs(1);
    loop {
        let (login, room) = match target(&ctx).await {
            Ok(Some(target)) => target,
            Ok(None) => {
                ctx.phones.set_status(RelayStatus::Off);
                changed(&mut commands, &mut account).await;
                continue;
            }
            Err(error) => {
                ctx.phones.set_status(RelayStatus::Failed {
                    reason: error.message,
                });
                changed(&mut commands, &mut account).await;
                continue;
            }
        };
        ctx.phones.set_status(RelayStatus::Connecting);
        let path = format!(
            "{}?room={room}&version={}",
            relay::PATH,
            env!("CARGO_PKG_VERSION")
        );
        let connected = tokio::select! {
            connected = ctx.account.websocket(&ctx.db, &login, &path) => connected,
            () = changed(&mut commands, &mut account) => continue,
        };
        let reason = match connected {
            Ok(websocket) => {
                tracing::info!("phone relay connected");
                ctx.phones.set_status(RelayStatus::Online);
                wait = Duration::from_secs(1);
                match serve(
                    &ctx,
                    remote::split(websocket),
                    &room,
                    &mut commands,
                    &mut account,
                )
                .await
                {
                    Ended::Changed => continue,
                    Ended::Closed => {
                        tracing::info!("phone relay closed the connection");
                        "服务器断开了连接".into()
                    }
                    Ended::Failed(error) => {
                        tracing::warn!("phone relay: {error}");
                        "和服务器的连接断开了".into()
                    }
                }
            }
            Err(account::Error::SignedOut) => continue,
            Err(error) => {
                tracing::warn!("connect phone relay: {error}");
                error.message()
            }
        };
        ctx.phones.set_status(RelayStatus::Failed { reason });
        tokio::select! {
            () = tokio::time::sleep(wait) => {}
            () = changed(&mut commands, &mut account) => {}
        }
        wait = (wait * 2).min(LONGEST_WAIT);
    }
}

/// The login and room to connect with, when phone access is on and someone is logged in who has
/// agreed to the terms.
async fn target(ctx: &Context) -> Result<Option<(account::Login, String)>, CallError> {
    if !profile::phone_access(&ctx.db).await? {
        return Ok(None);
    }
    let login = match account::saved_login(&ctx.db).await {
        // Agreeing changes the login, which connects again.
        Ok(login) if login.terms_outdated => return Ok(None),
        Ok(login) => login,
        Err(account::Error::SignedOut) => return Ok(None),
        Err(error) => return Err(error.into()),
    };
    Ok(room(&ctx.db).await?.map(|room| (login, room)))
}

/// Waits for phone access or the login to change; nothing else matters while not connected.
async fn changed(
    commands: &mut mpsc::UnboundedReceiver<Command>,
    account: &mut watch::Receiver<()>,
) {
    loop {
        tokio::select! {
            Some(command) = commands.recv() => {
                if let Command::Changed = command {
                    return;
                }
            }
            Ok(()) = account.changed() => return,
        }
    }
}

enum Ended {
    /// Phone access or the login changed.
    Changed,
    Closed,
    Failed(remote::Error),
}

/// What a phone's connection is told by the relay or the app.
enum Incoming {
    Data(Vec<u8>),
    Push(Arc<[u8]>),
    Revoked(String),
    Answered(bool),
    Close,
}

enum Outgoing {
    Data(u32, Vec<u8>),
    Close(u32, u16),
    Ping,
}

/// Forwards between the relay and a task for each phone connection.
async fn serve(
    ctx: &Arc<Context>,
    (mut relay, mut sender): (remote::Relay, remote::Sender),
    room: &str,
    commands: &mut mpsc::UnboundedReceiver<Command>,
    account: &mut watch::Receiver<()>,
) -> Ended {
    let mut phones: HashMap<u32, mpsc::UnboundedSender<Incoming>> = HashMap::new();
    let (outgoing, mut sending) = mpsc::unbounded_channel();
    // A send waiting on the network holds up neither what the relay says nor phone access going
    // off.
    let send = async {
        while let Some(message) = sending.recv().await {
            let sent = match message {
                Outgoing::Data(connection, data) => sender.send(connection, &data).await,
                Outgoing::Close(connection, code) => sender.close(connection, code).await,
                Outgoing::Ping => sender.ping().await,
            };
            if let Err(error) = sent {
                return error;
            }
        }
        unreachable!("serve keeps a sender")
    };
    tokio::pin!(send);
    let mut ping = tokio::time::interval(remote::PING);
    loop {
        tokio::select! {
            event = relay.next() => match event {
                Ok(Some(RelayEvent::Connected(connection))) => {
                    let (incoming, receiver) = mpsc::unbounded_channel();
                    phones.insert(connection, incoming);
                    let phone = Phone {
                        ctx: ctx.clone(),
                        room: room.into(),
                        connection,
                        incoming: receiver,
                        outgoing: outgoing.clone(),
                    };
                    tokio::spawn(phone.run());
                }
                Ok(Some(RelayEvent::Data(connection, data))) => {
                    if let Some(phone) = phones.get(&connection) {
                        let _ = phone.send(Incoming::Data(data));
                    }
                }
                // Every connection ends with this, however it ended; dropping the sender ends the
                // phone's task if it still runs.
                Ok(Some(RelayEvent::Closed(connection))) => {
                    phones.remove(&connection);
                }
                Ok(None) => return Ended::Closed,
                Err(error) => return Ended::Failed(error),
            },
            Some(command) = commands.recv() => match command {
                Command::Changed => return Ended::Changed,
                Command::Push(message) => {
                    for phone in phones.values() {
                        let _ = phone.send(Incoming::Push(message.clone()));
                    }
                }
                Command::Revoked(device) => {
                    for phone in phones.values() {
                        let _ = phone.send(Incoming::Revoked(device.clone()));
                    }
                }
                Command::Answered { connection, allowed } => {
                    if let Some(phone) = phones.get(&connection) {
                        let _ = phone.send(Incoming::Answered(allowed));
                    }
                }
                Command::Close(connection) => {
                    if let Some(phone) = phones.get(&connection) {
                        let _ = phone.send(Incoming::Close);
                    }
                }
            },
            error = &mut send => return Ended::Failed(error),
            _ = ping.tick() => {
                if relay.heard().elapsed() > remote::QUIET {
                    return Ended::Failed(remote::Error::Quiet);
                }
                let _ = outgoing.send(Outgoing::Ping);
            }
            Ok(()) = account.changed() => return Ended::Changed,
        }
    }
}

/// One phone connection, from the handshake on.
struct Phone {
    ctx: Arc<Context>,
    room: String,
    connection: u32,
    incoming: mpsc::UnboundedReceiver<Incoming>,
    outgoing: mpsc::UnboundedSender<Outgoing>,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct PhoneHello {
    hello: HelloFields,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct HelloFields {
    user_agent: String,
}

/// What a phone sends once it is in: a call, or a ping to learn whether the computer still hears it,
/// as a phone back from the background does.
#[derive(Deserialize)]
#[serde(untagged)]
enum FromPhone {
    Call(Request),
    Ping {
        #[expect(dead_code, reason = "a ping says nothing more")]
        ping: bool,
    },
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Request {
    id: u64,
    op: String,
    input: Value,
}

/// Why a phone's connection ends.
enum Leave {
    /// The phone or the relay went away.
    Gone,
    Close(u16),
}

impl Phone {
    async fn run(mut self) {
        let mut device = None;
        let leave = self.session(&mut device).await;
        if let Some(device) = &device {
            {
                let mut state = self.ctx.phones.state();
                if let Some(count) = state.online.get_mut(device) {
                    *count -= 1;
                    if *count == 0 {
                        state.online.remove(device);
                    }
                }
            }
            seen(&self.ctx, device).await;
            self.ctx.phones.emit(Event::Changed);
        }
        let pairing_left = self
            .ctx
            .phones
            .state()
            .pairing
            .take_if(|p| p.stage.connection() == Some(self.connection))
            .is_some();
        if pairing_left {
            self.ctx.phones.emit(Event::Pairing(None));
        }
        if let Leave::Close(code) = leave {
            let _ = self.outgoing.send(Outgoing::Close(self.connection, code));
        }
    }

    /// Runs the connection until it ends, saying in `device` which phone it was once ready.
    async fn session(&mut self, device: &mut Option<String>) -> Leave {
        let (mut session, phone, pairing, user_agent) =
            match tokio::time::timeout(HANDSHAKE, self.handshake()).await {
                Ok(Ok(shaken)) => shaken,
                Ok(Err(leave)) => return leave,
                Err(_) => return Leave::Close(close::BROKEN),
            };
        if pairing && let Err(leave) = self.pair(phone_name(&user_agent)).await {
            return leave;
        }
        *self
            .ctx
            .phones
            .state()
            .online
            .entry(phone.clone())
            .or_default() += 1;
        seen(&self.ctx, &phone).await;
        self.ctx.phones.emit(Event::Changed);
        *device = Some(phone.clone());
        let computer = whoami::devicename()
            .inspect_err(|e| tracing::warn!("computer name: {e}"))
            .unwrap_or_else(|_| "电脑".into());
        // The phone loads the page of the app's version, so a computer updated meanwhile has it
        // load the page again.
        let welcome = json!({ "welcome": {
            "computer": computer,
            "version": env!("CARGO_PKG_VERSION"),
        } });
        if self.seal(&mut session, welcome).is_err() {
            return Leave::Close(close::BROKEN);
        }
        let (reply, mut replies) = mpsc::unbounded_channel::<Value>();
        loop {
            let message = tokio::select! {
                incoming = self.incoming.recv() => match incoming {
                    Some(Incoming::Data(data)) => match session.open(&data) {
                        Ok(None) => continue,
                        Ok(Some(json)) => match serde_json::from_slice::<FromPhone>(&json) {
                            Ok(FromPhone::Ping { .. }) => json!({ "pong": true }),
                            Ok(FromPhone::Call(request)) => {
                                let (ctx, reply) = (self.ctx.clone(), reply.clone());
                                tokio::spawn(async move {
                                    let message = match call(&ctx, &request.op, request.input).await
                                    {
                                        Ok(output) => json!({ "id": request.id, "output": output }),
                                        Err(error) => json!({ "id": request.id, "error": error }),
                                    };
                                    let _ = reply.send(message);
                                });
                                continue;
                            }
                            Err(error) => {
                                tracing::warn!("phone request: {error}");
                                return Leave::Close(close::BROKEN);
                            }
                        },
                        Err(error) => {
                            tracing::warn!("phone message: {error}");
                            return Leave::Close(close::BROKEN);
                        }
                    },
                    Some(Incoming::Push(message)) => {
                        if self.seal_bytes(&mut session, &message).is_err() {
                            return Leave::Close(close::BROKEN);
                        }
                        continue;
                    }
                    Some(Incoming::Revoked(revoked)) if revoked == phone => {
                        return Leave::Close(close::UNPAIRED);
                    }
                    Some(Incoming::Revoked(_) | Incoming::Answered(_) | Incoming::Close) => continue,
                    None => return Leave::Gone,
                },
                Some(message) = replies.recv() => message,
            };
            if self.seal(&mut session, message).is_err() {
                return Leave::Close(close::BROKEN);
            }
        }
    }

    /// The handshake and what the phone says it is: the session, the phone, whether it is pairing
    /// with the code on screen, and its browser's User-Agent.
    async fn handshake(&mut self) -> Result<(Session, String, bool, String), Leave> {
        let first = loop {
            match self.incoming.recv().await {
                Some(Incoming::Data(first)) => break first,
                // Nothing else is for a phone not known yet.
                Some(Incoming::Push(_) | Incoming::Revoked(_) | Incoming::Answered(_)) => {}
                Some(Incoming::Close) => return Err(Leave::Close(close::DENIED)),
                None => return Err(Leave::Gone),
            }
        };
        let hello = Hello::read(&first).map_err(|_| Leave::Close(close::BROKEN))?;
        let phone = hello.device.to_owned();
        let (key, pairing) = self
            .key(&phone)
            .await
            .ok_or(Leave::Close(close::UNPAIRED))?;
        let (answer, mut session) = hello
            .accept(&self.room, &key)
            .map_err(|_| Leave::Close(close::UNPAIRED))?;
        self.send_raw(answer);
        let user_agent = self.hello(&mut session, &phone).await?;
        Ok((session, phone, pairing, user_agent))
    }

    /// The phone's pairing key, and whether it is pairing with the code on screen, which it uses
    /// up; none for a phone this computer does not know.
    async fn key(&self, device: &str) -> Option<([u8; 32], bool)> {
        let paired = self
            .ctx
            .writes
            .retry("phone pairing key", || {
                sqlx::query_scalar!("SELECT key FROM phone WHERE id = ?", device)
                    .fetch_optional(&self.ctx.db)
            })
            .await;
        if let Some(key) = paired {
            let key = key
                .try_into()
                .unwrap_or_else(|_| unreachable!("the table keeps 32-byte keys"));
            return Some((key, false));
        }
        let mut state = self.ctx.phones.state();
        match &mut state.pairing {
            Some(pairing)
                if pairing.room == self.room
                    && pairing.device == device
                    && matches!(pairing.stage, Stage::Shown(until) if until > Instant::now()) =>
            {
                pairing.stage = Stage::Used(self.connection);
                Some((pairing.key, true))
            }
            _ => None,
        }
    }

    /// What the phone says it is, the first thing it sends.
    async fn hello(&mut self, session: &mut Session, phone: &str) -> Result<String, Leave> {
        loop {
            match self.incoming.recv().await {
                Some(Incoming::Data(data)) => match session.open(&data) {
                    Ok(None) => {}
                    Ok(Some(json)) => match serde_json::from_slice::<PhoneHello>(&json) {
                        Ok(hello) => return Ok(hello.hello.user_agent),
                        Err(_) => return Err(Leave::Close(close::BROKEN)),
                    },
                    Err(_) => return Err(Leave::Close(close::BROKEN)),
                },
                Some(Incoming::Revoked(revoked)) if revoked == phone => {
                    return Err(Leave::Close(close::UNPAIRED));
                }
                Some(Incoming::Close) => return Err(Leave::Close(close::DENIED)),
                Some(Incoming::Push(_) | Incoming::Revoked(_) | Incoming::Answered(_)) => {}
                None => return Err(Leave::Gone),
            }
        }
    }

    /// Asks the user whether the phone may pair.
    async fn pair(&mut self, name: String) -> Result<(), Leave> {
        let asked = match &mut self.ctx.phones.state().pairing {
            Some(pairing) if pairing.stage.connection() == Some(self.connection) => {
                pairing.stage = Stage::Asked {
                    connection: self.connection,
                    name: name.clone(),
                };
                true
            }
            _ => false,
        };
        if !asked {
            return Err(Leave::Close(close::UNPAIRED));
        }
        self.ctx.phones.emit(Event::Pairing(Some(name)));
        let incoming = &mut self.incoming;
        let answered = async {
            loop {
                match incoming.recv().await {
                    Some(Incoming::Answered(true)) => return Ok(()),
                    Some(Incoming::Answered(false) | Incoming::Close) => {
                        return Err(Leave::Close(close::DENIED));
                    }
                    // Nothing more is expected from the phone until the user answers.
                    Some(Incoming::Data(_) | Incoming::Push(_) | Incoming::Revoked(_)) => {}
                    None => return Err(Leave::Gone),
                }
            }
        };
        tokio::pin!(answered);
        if let Ok(answer) = tokio::time::timeout(PAIRING_FOR, &mut answered).await {
            return answer;
        }
        // Whoever took the pairing first tells this phone how it went: an answer being saved, or a
        // new code or a cancel turning it away.
        let unanswered = self
            .ctx
            .phones
            .state()
            .pairing
            .take_if(|p| p.stage.connection() == Some(self.connection))
            .is_some();
        if !unanswered {
            return answered.await;
        }
        self.ctx.phones.emit(Event::Pairing(None));
        Err(Leave::Close(close::DENIED))
    }

    fn send_raw(&self, data: Vec<u8>) {
        let _ = self.outgoing.send(Outgoing::Data(self.connection, data));
    }

    fn seal(&self, session: &mut Session, message: Value) -> Result<(), remote::SessionError> {
        self.seal_bytes(session, message.to_string().as_bytes())
    }

    fn seal_bytes(&self, session: &mut Session, json: &[u8]) -> Result<(), remote::SessionError> {
        for message in session.seal(json)? {
            self.send_raw(message);
        }
        Ok(())
    }
}

async fn seen(ctx: &Context, device: &str) {
    let now = crate::now();
    ctx.writes
        .retry("record phone connection", || {
            sqlx::query!("UPDATE phone SET seen_at = ? WHERE id = ?", now, device).execute(&ctx.db)
        })
        .await;
}

/// A phone's call: what the window may do, except what only the window may.
async fn call(ctx: &Context, op: &str, input: Value) -> Result<Value, CallError> {
    if crate::window_only(op) {
        return Err(CallError::invalid("这个操作只能在电脑上做。".into()));
    }
    let output = crate::call(ctx, op, input).await?;
    ctx.phones.emit(Event::Called);
    Ok(output)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[sqlx::test(migrator = "db::MIGRATOR")]
    async fn a_phone_not_in_yet_lets_events_pass_and_must_be_quick(db: sqlx::SqlitePool) {
        let ctx = Arc::new(Context::new(
            db,
            net::Net::new(vec![]).unwrap(),
            std::path::Path::new(""),
        ));
        let (outgoing, mut sent) = mpsc::unbounded_channel();
        let phone = |incoming| Phone {
            ctx: ctx.clone(),
            room: "0".repeat(32),
            connection: 1,
            incoming,
            outgoing: outgoing.clone(),
        };

        // An event for the phones comes before this one's first message, which is not a hello.
        let (incoming, receiver) = mpsc::unbounded_channel();
        incoming
            .send(Incoming::Push(Arc::from(&b"{}"[..])))
            .unwrap();
        incoming.send(Incoming::Data(b"hello?".to_vec())).unwrap();
        phone(receiver).run().await;
        assert!(matches!(
            sent.try_recv(),
            Ok(Outgoing::Close(1, close::BROKEN))
        ));

        // One that says nothing is let go.
        tokio::time::pause();
        let (_incoming, receiver) = mpsc::unbounded_channel();
        tokio::time::timeout(HANDSHAKE * 2, phone(receiver).run())
            .await
            .unwrap();
        assert!(matches!(
            sent.try_recv(),
            Ok(Outgoing::Close(1, close::BROKEN))
        ));
    }
}
