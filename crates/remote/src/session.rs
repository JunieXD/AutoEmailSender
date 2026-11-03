//! The encrypted session with one phone (docs/development.md#手机远程).

use cloud_api::relay::valid_id;
use snow::{HandshakeState, TransportState, params::NoiseParams};

const PATTERN: &str = "Noise_NNpsk0_25519_ChaChaPoly_SHA256";
const PROLOGUE: &str = "auto-email-sender remote 1";
/// A Noise message's authentication tag.
const TAG: usize = 16;
/// What one Noise message carries of a JSON message: at most 65535 bytes with the tag, less the
/// byte saying whether the part is the last.
const PART: usize = 65535 - TAG - 1;
/// The longest JSON message either end sends: uploading a 20 MB material, in base64.
pub const MAX_JSON: usize = 32 * 1024 * 1024;

#[derive(Debug)]
pub enum Error {
    /// The phone sent something that is not a message of this protocol.
    Malformed,
    TooLong,
    Noise(snow::Error),
}

impl From<snow::Error> for Error {
    fn from(error: snow::Error) -> Self {
        Self::Noise(error)
    }
}

impl std::fmt::Display for Error {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::Malformed => write!(f, "malformed phone message"),
            Self::TooLong => write!(f, "phone message longer than {MAX_JSON} bytes"),
            Self::Noise(error) => write!(f, "noise: {error}"),
        }
    }
}

/// The phone's first message on a connection: its device id in the clear, then the handshake's
/// first message.
pub struct Hello<'a> {
    pub device: &'a str,
    message: &'a [u8],
}

impl<'a> Hello<'a> {
    pub fn read(data: &'a [u8]) -> Result<Self, Error> {
        let (device, message) = data.split_at_checked(32).ok_or(Error::Malformed)?;
        let device = std::str::from_utf8(device).map_err(|_| Error::Malformed)?;
        if !valid_id(device) {
            return Err(Error::Malformed);
        }
        Ok(Self { device, message })
    }

    /// Answers with the phone's pairing key: the reply to send it, and the session. The handshake
    /// is bound to this room and this phone.
    pub fn accept(self, room: &str, key: &[u8; 32]) -> Result<(Vec<u8>, Session), Error> {
        let mut handshake = responder(room, self.device, key)?;
        handshake.read_message(self.message, &mut [])?;
        // The responder's ephemeral key and the tag of its empty payload.
        let mut reply = vec![0; 32 + TAG];
        handshake.write_message(&[], &mut reply)?;
        Ok((
            reply,
            Session {
                transport: handshake.into_transport_mode()?,
                partial: Vec::new(),
            },
        ))
    }
}

fn responder(room: &str, device: &str, key: &[u8; 32]) -> Result<HandshakeState, Error> {
    let params: NoiseParams = PATTERN
        .parse()
        .unwrap_or_else(|_| unreachable!("the pattern is a valid Noise protocol name"));
    let prologue = format!("{PROLOGUE}{room}{device}");
    Ok(snow::Builder::new(params)
        .psk(0, key)?
        .prologue(prologue.as_bytes())?
        .build_responder()?)
}

pub struct Session {
    transport: TransportState,
    partial: Vec<u8>,
}

impl Session {
    /// The Noise messages carrying `json`, which is never empty, in order.
    pub fn seal(&mut self, json: &[u8]) -> Result<Vec<Vec<u8>>, Error> {
        let count = json.len().div_ceil(PART);
        json.chunks(PART)
            .enumerate()
            .map(|(i, part)| {
                let mut plain = Vec::with_capacity(part.len() + 1);
                plain.push(u8::from(i + 1 == count));
                plain.extend_from_slice(part);
                let mut message = vec![0; plain.len() + TAG];
                self.transport.write_message(&plain, &mut message)?;
                Ok(message)
            })
            .collect()
    }

    /// Reads one Noise message: the JSON message once its last part has come.
    pub fn open(&mut self, message: &[u8]) -> Result<Option<Vec<u8>>, Error> {
        let mut plain = vec![0; message.len()];
        let length = self.transport.read_message(message, &mut plain)?;
        let (&last, part) = plain[..length].split_first().ok_or(Error::Malformed)?;
        if self.partial.len() + part.len() > MAX_JSON {
            return Err(Error::TooLong);
        }
        self.partial.extend_from_slice(part);
        Ok(match last {
            0 => None,
            1 => Some(std::mem::take(&mut self.partial)),
            _ => return Err(Error::Malformed),
        })
    }
}
