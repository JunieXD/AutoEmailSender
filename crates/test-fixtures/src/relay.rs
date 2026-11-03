//! An in-memory relay following the official server's rules (the server repository's
//! docs/relay.md), and phones that reach the app through it as the phone page does.
use std::{
    collections::HashMap,
    io,
    sync::{Arc, Mutex},
};

use async_tungstenite::{
    WebSocketStream,
    tokio::{TokioAdapter, accept_hdr_async},
    tungstenite::{
        Message,
        handshake::server::{ErrorResponse, Request, Response},
        protocol::{CloseFrame, frame::coding::CloseCode},
    },
};
use base64::prelude::{BASE64_URL_SAFE_NO_PAD, Engine};
use cloud_api::relay::{Frame, Kind, close};
use futures_util::StreamExt;
use serde_json::{Value, json};
use tokio::{
    net::{TcpListener, TcpStream},
    sync::mpsc,
    task::JoinHandle,
};

type Socket = WebSocketStream<TokioAdapter<TcpStream>>;

#[derive(Default)]
pub struct State {
    /// The login the computer must connect with.
    pub token: String,
    /// The room and app version of the computer connected now.
    pub computer: Option<(String, String)>,
    to_computer: Option<mpsc::UnboundedSender<Message>>,
    phones: HashMap<u32, mpsc::UnboundedSender<Message>>,
    next: u32,
}

pub struct Relay {
    pub address: String,
    pub state: Arc<Mutex<State>>,
    task: JoinHandle<()>,
}

impl Drop for Relay {
    fn drop(&mut self) {
        self.task.abort();
    }
}

impl Relay {
    #[expect(
        clippy::unwrap_used,
        reason = "test fixture listener failures must panic at their source"
    )]
    pub async fn start(token: &str) -> io::Result<Self> {
        let listener = TcpListener::bind("127.0.0.1:0").await?;
        let address = format!("http://127.0.0.1:{}", listener.local_addr()?.port());
        let state = Arc::new(Mutex::new(State {
            token: token.into(),
            ..State::default()
        }));
        let shared = state.clone();
        let task = tokio::spawn(async move {
            loop {
                let (stream, _) = listener.accept().await.unwrap();
                tokio::spawn(serve(stream, shared.clone()));
            }
        });
        Ok(Self {
            address,
            state,
            task,
        })
    }

    /// Closes the computer's connection, as a restarting server would.
    #[expect(clippy::unwrap_used, reason = "test fixture state failures must panic")]
    pub fn drop_computer(&self) {
        let mut state = self.state.lock().unwrap();
        state.computer = None;
        if let Some(computer) = state.to_computer.take() {
            let _ = computer.send(Message::Close(None));
        }
    }
}

#[expect(
    clippy::unwrap_used,
    reason = "test fixture request failures must panic at their source"
)]
#[expect(
    clippy::result_large_err,
    reason = "tungstenite decides the handshake callback's type"
)]
async fn serve(stream: TcpStream, state: Arc<Mutex<State>>) {
    let mut path = String::new();
    let mut authorization = String::new();
    let socket = accept_hdr_async(stream, |request: &Request, response: Response| {
        path = request.uri().to_string();
        authorization = request
            .headers()
            .get("authorization")
            .map(|v| v.to_str().unwrap().to_owned())
            .unwrap_or_default();
        let token = state.lock().unwrap().token.clone();
        if path.starts_with("/v1/relay?") && authorization != format!("Bearer {token}") {
            let mut refused = ErrorResponse::new(Some(r#"{"code":"session_expired"}"#.into()));
            *refused.status_mut() = 401.try_into().unwrap();
            return Err(refused);
        }
        Ok(response)
    })
    .await;
    let Ok(socket) = socket else {
        return;
    };
    if let Some(query) = path.strip_prefix("/v1/relay?") {
        let fields: HashMap<_, _> = query
            .split('&')
            .filter_map(|pair| pair.split_once('='))
            .collect();
        computer(
            socket,
            state,
            fields["room"].into(),
            fields["version"].into(),
        )
        .await;
    } else if let Some(room) = path
        .strip_prefix("/v1/relay/")
        .and_then(|rest| rest.strip_suffix("/phone"))
    {
        phone(socket, state, room).await;
    }
}

#[expect(
    clippy::unwrap_used,
    reason = "test fixture state failures must panic at their source"
)]
async fn computer(socket: Socket, state: Arc<Mutex<State>>, room: String, version: String) {
    let (mut sink, mut stream) = socket.split();
    let (to_computer, mut outgoing) = mpsc::unbounded_channel();
    {
        let mut state = state.lock().unwrap();
        state.computer = Some((room, version));
        state.to_computer = Some(to_computer.clone());
    }
    loop {
        tokio::select! {
            message = outgoing.recv() => {
                let Some(message) = message else { break };
                let closing = matches!(message, Message::Close(_));
                if sink.send(message).await.is_err() || closing {
                    break;
                }
            }
            message = stream.next() => {
                let Some(Ok(Message::Binary(bytes))) = message else {
                    if matches!(message, Some(Ok(Message::Ping(_) | Message::Pong(_)))) {
                        continue;
                    }
                    break;
                };
                let frame = Frame::decode(&bytes).unwrap();
                let phone = state.lock().unwrap().phones.get(&frame.connection).cloned();
                let Some(phone) = phone else { continue };
                let _ = phone.send(match frame.kind {
                    Kind::Data => Message::Binary(frame.data.to_vec().into()),
                    Kind::Closed => Message::Close(Some(CloseFrame {
                        code: CloseCode::from(frame.close_code().unwrap()),
                        reason: "".into(),
                    })),
                    Kind::Connected => unreachable!("the computer does not announce phones"),
                });
            }
        }
    }
    let phones = {
        let mut state = state.lock().unwrap();
        if state
            .to_computer
            .as_ref()
            .is_some_and(|current| current.same_channel(&to_computer))
        {
            state.to_computer = None;
            state.computer = None;
        }
        std::mem::take(&mut state.phones)
    };
    for phone in phones.into_values() {
        let _ = phone.send(Message::Close(Some(CloseFrame {
            code: CloseCode::from(close::OFFLINE),
            reason: "".into(),
        })));
    }
}

#[expect(
    clippy::unwrap_used,
    reason = "test fixture state failures must panic at their source"
)]
async fn phone(socket: Socket, state: Arc<Mutex<State>>, room: &str) {
    let (mut sink, mut stream) = socket.split();
    let (to_phone, mut outgoing) = mpsc::unbounded_channel();
    let joined = {
        let mut state = state.lock().unwrap();
        match (&state.computer, state.to_computer.clone()) {
            (Some((current, _)), Some(computer)) if current == room => {
                state.next += 1;
                let connection = state.next;
                state.phones.insert(connection, to_phone);
                Some((connection, computer))
            }
            _ => None,
        }
    };
    let Some((connection, computer)) = joined else {
        let _ = sink
            .send(Message::Close(Some(CloseFrame {
                code: CloseCode::from(close::OFFLINE),
                reason: "".into(),
            })))
            .await;
        return;
    };
    let frame = |kind, data: &[u8]| {
        Message::Binary(
            Frame {
                connection,
                kind,
                data,
            }
            .encode()
            .into(),
        )
    };
    let _ = computer.send(frame(Kind::Connected, &[]));
    loop {
        tokio::select! {
            message = outgoing.recv() => {
                let Some(message) = message else { break };
                let closing = matches!(message, Message::Close(_));
                if sink.send(message).await.is_err() || closing {
                    break;
                }
            }
            message = stream.next() => match message {
                Some(Ok(Message::Binary(bytes))) => {
                    let _ = computer.send(frame(Kind::Data, &bytes));
                }
                Some(Ok(Message::Ping(_) | Message::Pong(_))) => {}
                _ => break,
            },
        }
    }
    let removed = state.lock().unwrap().phones.remove(&connection).is_some();
    if removed {
        let _ = computer.send(frame(Kind::Closed, &[]));
    }
}

/// A phone's view of a pairing: what the code it scanned holds.
#[derive(Clone)]
pub struct Pairing {
    pub room: String,
    pub device: String,
    pub key: [u8; 32],
}

impl Pairing {
    /// Reads the code's address, `<server>/m/#p=<room>.<device>.<key>`.
    #[expect(clippy::unwrap_used, reason = "a test's code must be well formed")]
    pub fn read(url: &str) -> Self {
        let (_, fragment) = url.split_once("#p=").unwrap();
        let mut parts = fragment.split('.');
        let (room, device, key) = (
            parts.next().unwrap(),
            parts.next().unwrap(),
            parts.next().unwrap(),
        );
        Self {
            room: room.into(),
            device: device.into(),
            key: BASE64_URL_SAFE_NO_PAD
                .decode(key)
                .unwrap()
                .try_into()
                .unwrap(),
        }
    }
}

/// What a phone ended up with.
#[derive(Debug, PartialEq, Eq)]
pub enum Closed {
    Code(u16),
    Gone,
}

/// A phone page connected through the relay, written apart from the app's code so that it checks
/// the protocol: Noise NNpsk0 with the prologue, then JSON in parts.
pub struct Phone {
    socket: async_tungstenite::WebSocketStream<async_tungstenite::tokio::ConnectStream>,
    transport: snow::TransportState,
    partial: Vec<u8>,
    next_id: u64,
}

impl Phone {
    /// Connects, shakes hands and says it is `user_agent`; the computer closes the connection
    /// instead when it does not know the phone.
    #[expect(
        clippy::unwrap_used,
        reason = "test phone failures must panic at their source"
    )]
    pub async fn connect(relay: &str, pairing: &Pairing, user_agent: &str) -> Result<Self, Closed> {
        let url = format!(
            "{}/v1/relay/{}/phone",
            relay.replacen("http", "ws", 1),
            pairing.room
        );
        let (mut socket, _) = async_tungstenite::tokio::connect_async(url).await.unwrap();
        let prologue = format!(
            "auto-email-sender remote 1{}{}",
            pairing.room, pairing.device
        );
        let mut handshake =
            snow::Builder::new("Noise_NNpsk0_25519_ChaChaPoly_SHA256".parse().unwrap())
                .psk(0, &pairing.key)
                .unwrap()
                .prologue(prologue.as_bytes())
                .unwrap()
                .build_initiator()
                .unwrap();
        let mut first = pairing.device.as_bytes().to_vec();
        let mut message = [0; 64];
        let length = handshake.write_message(&[], &mut message).unwrap();
        first.extend_from_slice(&message[..length]);
        socket.send(Message::Binary(first.into())).await.unwrap();
        let answer = next(&mut socket).await?;
        handshake.read_message(&answer, &mut []).unwrap();
        let mut phone = Self {
            socket,
            transport: handshake.into_transport_mode().unwrap(),
            partial: Vec::new(),
            next_id: 0,
        };
        phone
            .send(&json!({ "hello": { "userAgent": user_agent } }))
            .await;
        Ok(phone)
    }

    #[expect(
        clippy::unwrap_used,
        reason = "test phone failures must panic at their source"
    )]
    pub async fn send(&mut self, message: &Value) {
        let json = message.to_string().into_bytes();
        let parts: Vec<_> = json.chunks(65535 - 16 - 1).collect();
        for (i, part) in parts.iter().enumerate() {
            let mut plain = vec![u8::from(i + 1 == parts.len())];
            plain.extend_from_slice(part);
            let mut sealed = vec![0; plain.len() + 16];
            self.transport.write_message(&plain, &mut sealed).unwrap();
            self.socket
                .send(Message::Binary(sealed.into()))
                .await
                .unwrap();
        }
    }

    /// The next message from the computer, or how the connection ended.
    #[expect(
        clippy::unwrap_used,
        reason = "test phone failures must panic at their source"
    )]
    pub async fn receive(&mut self) -> Result<Value, Closed> {
        loop {
            let sealed = next(&mut self.socket).await?;
            let mut plain = vec![0; sealed.len()];
            let length = self.transport.read_message(&sealed, &mut plain).unwrap();
            self.partial.extend_from_slice(&plain[1..length]);
            if plain[0] == 1 {
                return Ok(serde_json::from_slice(&std::mem::take(&mut self.partial)).unwrap());
            }
        }
    }

    /// Calls an operation and waits for its answer, letting events pass.
    pub async fn call(&mut self, op: &str, input: Value) -> Result<Value, Value> {
        self.next_id += 1;
        let id = self.next_id;
        self.send(&json!({ "id": id, "op": op, "input": input }))
            .await;
        loop {
            let message = self.receive().await.unwrap_or_else(|closed| {
                unreachable!("the connection ended during a call: {closed:?}")
            });
            if message["id"] == id {
                return match message.get("output") {
                    Some(output) => Ok(output.clone()),
                    None => Err(message["error"].clone()),
                };
            }
        }
    }
}

#[expect(
    clippy::unwrap_used,
    reason = "test phone failures must panic at their source"
)]
async fn next(
    socket: &mut async_tungstenite::WebSocketStream<async_tungstenite::tokio::ConnectStream>,
) -> Result<Vec<u8>, Closed> {
    loop {
        match socket.next().await.transpose().unwrap() {
            Some(Message::Binary(bytes)) => return Ok(bytes.to_vec()),
            Some(Message::Close(frame)) => {
                return Err(frame.map_or(Closed::Gone, |f| Closed::Code(u16::from(f.code))));
            }
            Some(_) => {}
            None => return Err(Closed::Gone),
        }
    }
}
