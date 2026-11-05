//! Account terms and deletion.

use serde::{Deserialize, Serialize};

/// How long a deleted account waits before it goes, in milliseconds. Logging in with `restore`
/// within it cancels the deletion.
pub const COOLING_OFF: i64 = 15 * 24 * 60 * 60 * 1000;

/// Agrees to a version of the terms of service and privacy policy.
#[derive(Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Terms {
    pub terms: String,
}

/// The answer to a deletion: when the account goes.
#[derive(Debug, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct Deleting {
    pub delete_at: i64,
}
