//! Cloud backup. The server keeps encrypted objects it cannot read; each backup's manifest object
//! says which objects hold the database and the materials.

use serde::{Deserialize, Serialize};

/// The most one backup's objects may take together, in bytes; the interface calls it 30 MB.
pub const LIMIT: u64 = 30 * 1024 * 1024;
/// The most objects in one backup: its manifest, its database and the materials.
pub const OBJECTS: usize = 500;
/// How long a backup is kept after sponsorship ends, in milliseconds.
pub const RETENTION: i64 = 90 * 24 * 60 * 60 * 1000;

/// Object and device ids are 128 random bits chosen by the client, as 32 lowercase hex digits.
pub fn valid_id(value: &str) -> bool {
    value.len() == 32
        && value
            .bytes()
            .all(|c| matches!(c, b'0'..=b'9' | b'a'..=b'f'))
}

#[derive(Debug, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct Status {
    pub current: Option<Current>,
    pub sponsor_until: Option<i64>,
}

#[derive(Debug, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct Current {
    pub device_id: String,
    pub manifest: String,
    pub committed_at: i64,
    /// Bytes of all of the backup's objects.
    pub size: u64,
}

#[derive(Serialize, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct Commit {
    pub device_id: String,
    pub manifest: String,
    /// The backup's other objects: the database and the materials.
    pub objects: Vec<String>,
    /// The manifest of the current backup this one replaces, none when there is none; the commit
    /// fails with `backup_changed` when another has become current since.
    pub replaces: Option<String>,
}

/// Sent before uploading: the account's objects that no committed backup holds are what earlier
/// attempts left behind, and go, except those the client means to use again.
#[derive(Serialize, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct Discard {
    pub keep: Vec<String>,
}

#[derive(Debug, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct Committed {
    pub committed_at: i64,
}
