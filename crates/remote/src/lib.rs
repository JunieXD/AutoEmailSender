//! Phone remote's transport (docs/development.md#手机远程): the computer's WebSocket to the
//! official relay, and the encrypted session with each phone over it. What a phone may do is the
//! app's business (`api::phones`).

mod session;
pub use session::{Error as SessionError, Hello, MAX_JSON, Session};

use cloud_api::relay::{Frame, Kind};
use futures_util::{
    SinkExt, StreamExt,
    stream::{SplitSink, SplitStream},
};
use reqwest_websocket::{Message, WebSocket};
use std::time::{Duration, Instant};

/// How often the computer pings the relay, which drops connections quiet for 90 seconds.
pub const PING: Duration = Duration::from_secs(30);
/// A relay that sent nothing for longer is gone: it pings as often as the computer, and answers a
/// ping at once.
pub const QUIET: Duration = Duration::from_secs(60);

/// What the relay says about one of the room's phone connections, by its number.
#[derive(Debug, PartialEq, Eq)]
pub enum Event {
    Connected(u32),
    Data(u32, Vec<u8>),
    Closed(u32),
}

#[derive(Debug)]
pub enum Error {
    WebSocket(reqwest_websocket::Error),
    /// The relay sent something that is not a frame of the protocol.
    Malformed,
    /// The relay sent nothing, not even a pong, for over `QUIET`.
    Quiet,
}

impl From<reqwest_websocket::Error> for Error {
    fn from(error: reqwest_websocket::Error) -> Self {
        Self::WebSocket(error)
    }
}

impl std::fmt::Display for Error {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::WebSocket(error) => write!(f, "relay connection: {error}"),
            Self::Malformed => write!(f, "malformed relay frame"),
            Self::Quiet => write!(f, "relay went quiet"),
        }
    }
}

/// The computer's connection to the relay, holding its room, as what it hears and what it sends,
/// so that a send waiting on the network holds up nothing else.
pub fn split(websocket: WebSocket) -> (Relay, Sender) {
    let (sink, stream) = websocket.split();
    (
        Relay {
            stream,
            heard: Instant::now(),
        },
        Sender(sink),
    )
}

pub struct Relay {
    stream: SplitStream<WebSocket>,
    heard: Instant,
}

impl Relay {
    /// The next event; none once the relay closed the connection. Cancelling it loses nothing.
    pub async fn next(&mut self) -> Result<Option<Event>, Error> {
        loop {
            let message = self.stream.next().await.transpose()?;
            self.heard = Instant::now();
            let bytes = match message {
                None | Some(Message::Close { .. }) => return Ok(None),
                Some(Message::Binary(bytes)) => bytes,
                // The WebSocket answers pings itself.
                Some(Message::Ping(_) | Message::Pong(_)) => continue,
                Some(Message::Text(_)) => return Err(Error::Malformed),
            };
            let frame = Frame::decode(&bytes).ok_or(Error::Malformed)?;
            return Ok(Some(match frame.kind {
                Kind::Connected => Event::Connected(frame.connection),
                Kind::Data => Event::Data(frame.connection, frame.data.to_vec()),
                Kind::Closed => Event::Closed(frame.connection),
            }));
        }
    }

    /// When the relay last sent anything, even a ping or a pong; it pings every 30 seconds.
    pub fn heard(&self) -> Instant {
        self.heard
    }
}

pub struct Sender(SplitSink<WebSocket, Message>);

impl Sender {
    pub async fn send(&mut self, connection: u32, data: &[u8]) -> Result<(), Error> {
        let frame = Frame {
            connection,
            kind: Kind::Data,
            data,
        };
        Ok(self.0.send(Message::Binary(frame.encode().into())).await?)
    }

    /// Closes a phone's connection with one of `cloud_api::relay::close`.
    pub async fn close(&mut self, connection: u32, code: u16) -> Result<(), Error> {
        Ok(self
            .0
            .send(Message::Binary(Frame::close(connection, code).into()))
            .await?)
    }

    pub async fn ping(&mut self) -> Result<(), Error> {
        Ok(self.0.send(Message::Ping(Default::default())).await?)
    }
}
