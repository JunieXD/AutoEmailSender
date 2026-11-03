//! The relay between a computer's app and its paired phones. It forwards what the two ends
//! encrypted for each other and cannot read it; pairing and encryption happen at the ends.

use serde::{Deserialize, Serialize};

/// The computer connects here with its login, as a WebSocket, to hold its room.
pub const PATH: &str = "/v1/relay";

/// Whether a room's computer is online, and the version of its app.
pub fn status_path(room: &str) -> String {
    format!("{PATH}/{room}")
}

/// A phone connects here, as a WebSocket, to reach the room's computer.
pub fn phone_path(room: &str) -> String {
    format!("{PATH}/{room}/phone")
}

/// Rooms and devices are 128 random bits chosen by the computer, as 32 lowercase hex digits.
pub fn valid_id(value: &str) -> bool {
    crate::backup::valid_id(value)
}

/// The query of the computer's connection.
#[derive(Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Hold {
    pub room: String,
    /// The phone loads the interface of this version.
    pub version: String,
}

#[derive(Debug, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct Status {
    pub online: bool,
    pub version: Option<String>,
}

/// The longest WebSocket message either end may send: a Noise message of 64 KiB and a frame
/// header, with room to spare.
pub const MAX_MESSAGE: usize = 72 * 1024;

/// Close codes of a phone's WebSocket.
pub mod close {
    /// Sent by the computer: it does not know this phone, or the code it scanned has expired or
    /// been used. A phone that had paired before has been revoked.
    pub const UNPAIRED: u16 = 4401;
    /// Sent by the computer: the phone sent something this protocol does not have.
    pub const BROKEN: u16 = 4400;
    /// Sent by the computer: the user did not allow the phone to pair.
    pub const DENIED: u16 = 4403;
    /// The room's computer is not online, or went away.
    pub const OFFLINE: u16 = 4404;
    /// The room already has as many phones connected as it may.
    pub const FULL: u16 = 4429;
}

/// What a frame on the computer's connection is about. The phone's side carries only data.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Kind {
    /// A phone connected; no data.
    Connected = 1,
    Data = 2,
    /// From the relay, the phone's connection ended, however it ended; from the computer, close
    /// it, with the close code as two big-endian bytes.
    Closed = 3,
}

/// A frame on the computer's connection: `[connection u32][kind u8][data]`, the connection being
/// the relay's number for one phone connection within the room.
#[derive(Debug, PartialEq, Eq)]
pub struct Frame<'a> {
    pub connection: u32,
    pub kind: Kind,
    pub data: &'a [u8],
}

impl<'a> Frame<'a> {
    pub fn encode(&self) -> Vec<u8> {
        let mut out = Vec::with_capacity(5 + self.data.len());
        out.extend_from_slice(&self.connection.to_be_bytes());
        out.push(self.kind as u8);
        out.extend_from_slice(self.data);
        out
    }

    pub fn decode(bytes: &'a [u8]) -> Option<Self> {
        let (connection, rest) = bytes.split_first_chunk::<4>()?;
        let (&kind, data) = rest.split_first()?;
        let kind = match kind {
            1 => Kind::Connected,
            2 => Kind::Data,
            3 => Kind::Closed,
            _ => return None,
        };
        Some(Frame {
            connection: u32::from_be_bytes(*connection),
            kind,
            data,
        })
    }

    /// Asks the relay to close the phone's connection with `code`.
    pub fn close(connection: u32, code: u16) -> Vec<u8> {
        Frame {
            connection,
            kind: Kind::Closed,
            data: &code.to_be_bytes(),
        }
        .encode()
    }

    /// The close code a computer's closing frame carries.
    pub fn close_code(&self) -> Option<u16> {
        self.data.try_into().ok().map(u16::from_be_bytes)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn frames_round_trip_and_reject_unknown_kinds() {
        let frame = Frame {
            connection: 0x0102_0304,
            kind: Kind::Data,
            data: b"x",
        };
        assert_eq!(frame.encode(), [1, 2, 3, 4, 2, b'x']);
        assert_eq!(Frame::decode(&frame.encode()), Some(frame));
        assert_eq!(
            Frame::decode(&Frame::close(7, close::DENIED)).and_then(|f| f.close_code()),
            Some(close::DENIED)
        );
        assert_eq!(Frame::decode(&[0, 0, 0, 1, 4]), None);
        assert_eq!(Frame::decode(&[0, 0, 1]), None);
    }
}
