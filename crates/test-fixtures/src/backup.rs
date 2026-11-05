//! An in-memory cloud backup server following the official server's rules (the server
//! repository's docs/backup.md), without accounts: every request acts for one account.
use std::collections::{BTreeMap, BTreeSet};

use cloud_api::backup::{Commit, Committed, Current, Discard, LIMIT, OBJECTS, Status};
use serde_json::json;

use crate::http::{Reply, error, ok};

#[derive(Default)]
pub struct State {
    pub now: i64,
    pub plus_until: Option<i64>,
    pub objects: BTreeMap<String, Vec<u8>>,
    pub current: Option<Current>,
    /// The objects the current backup holds.
    pub referenced: BTreeSet<String>,
    /// The ids of every upload, in order.
    pub uploads: Vec<String>,
    /// Runs once on the next commit before it is checked, as another computer would act meanwhile.
    pub before_commit: Option<Change>,
}

pub type Change = Box<dyn FnOnce(&mut State) + Send>;

pub type Server = crate::http::Server<State>;

impl Server {
    pub async fn start(state: State) -> std::io::Result<Self> {
        crate::http::Server::serve(state, respond).await
    }
}

#[expect(
    clippy::unwrap_used,
    reason = "test fixture request failures must panic at their source"
)]
fn respond(state: &mut State, method: &str, path: &str, body: Vec<u8>) -> Reply {
    let plus = |state: &State| state.plus_until.is_some_and(|until| until > state.now);
    match (method, path.strip_prefix("/v1/backup")) {
        ("GET", Some("")) => ok(serde_json::to_value(Status {
            current: state.current.as_ref().map(|current| Current {
                device_id: current.device_id.clone(),
                manifest: current.manifest.clone(),
                committed_at: current.committed_at,
                size: current.size,
            }),
            plus_until: state.plus_until,
        })
        .unwrap()),
        ("GET", Some(object)) => match state.objects.get(object.trim_start_matches("/objects/")) {
            Some(data) => (200, "application/octet-stream", data.clone()),
            None => error(404, "backup_not_found"),
        },
        ("PUT", Some(object)) => {
            if !plus(state) {
                return error(403, "plus_required");
            }
            let object = object.trim_start_matches("/objects/").to_owned();
            let stored: usize = state.objects.values().map(Vec::len).sum();
            if !state.objects.contains_key(&object)
                && ((stored + body.len()) as u64 > 2 * LIMIT || state.objects.len() >= 2 * OBJECTS)
            {
                return error(413, "backup_too_large");
            }
            state.uploads.push(object.clone());
            state.objects.entry(object).or_insert(body);
            ok(json!({}))
        }
        ("POST", Some("/discard")) => {
            let discard: Discard = serde_json::from_slice(&body).unwrap();
            state
                .objects
                .retain(|id, _| state.referenced.contains(id) || discard.keep.contains(id));
            ok(json!({}))
        }
        ("POST", Some("")) => {
            if let Some(before) = state.before_commit.take() {
                before(state);
            }
            if !plus(state) {
                return error(403, "plus_required");
            }
            let commit: Commit = serde_json::from_slice(&body).unwrap();
            if commit.replaces.as_ref() != state.current.as_ref().map(|current| &current.manifest) {
                return error(409, "backup_changed");
            }
            let mut referenced = commit.objects;
            referenced.push(commit.manifest.clone());
            if referenced.len() > OBJECTS {
                return error(413, "backup_too_large");
            }
            let Some(sizes) = referenced
                .iter()
                .map(|id| state.objects.get(id).map(|data| data.len() as u64))
                .collect::<Option<Vec<_>>>()
            else {
                return error(409, "backup_incomplete");
            };
            let size = sizes.iter().sum();
            if size > LIMIT {
                return error(413, "backup_too_large");
            }
            state.now += 1;
            state.objects.retain(|id, _| referenced.contains(id));
            state.referenced = referenced.into_iter().collect();
            state.current = Some(Current {
                device_id: commit.device_id,
                manifest: commit.manifest,
                committed_at: state.now,
                size,
            });
            ok(serde_json::to_value(Committed {
                committed_at: state.now,
            })
            .unwrap())
        }
        ("DELETE", Some("")) => {
            state.objects.clear();
            state.referenced.clear();
            state.current = None;
            ok(json!({}))
        }
        _ => error(404, "not_found"),
    }
}
