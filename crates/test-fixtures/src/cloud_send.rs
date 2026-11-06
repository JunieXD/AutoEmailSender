//! An in-memory cloud sending server following the official server's rules (the server
//! repository's docs/cloud-send.md), for one account. Tests decide what becomes of each email.
use std::collections::BTreeMap;

use base64::prelude::{BASE64_URL_SAFE_NO_PAD, Engine};
use cloud_api::{
    Account, Empty,
    cloud_send::{
        EMAILS, EmailUpload, Finished, Ids, MAILBOXES, Mailbox, MailboxSaved, MailboxUpload,
        Mailboxes, Outcome, RESULTS, RESULTS_ACK, Results, Withdrawal,
    },
    tracking::Quota,
};
use transport::pause::Pause;

use crate::http::{Reply, error, ok};

pub struct State {
    pub now: i64,
    pub email: String,
    pub plus_until: Option<i64>,
    /// By address.
    pub mailboxes: BTreeMap<String, Kept>,
    /// Addresses the server cannot log in to, with why.
    pub refused: BTreeMap<String, String>,
    /// By id.
    pub emails: BTreeMap<String, Email>,
    /// Ids withdrawn, refused from then on, with whether each was waiting then.
    pub withdrawn: BTreeMap<String, bool>,
    /// Uploads are kept, then answered with an internal error, as when the answer to the commit
    /// was lost.
    pub fail_after_keeping: bool,
    /// Acknowledgements fail.
    pub fail_ack: bool,
}

impl Default for State {
    fn default() -> Self {
        Self {
            now: 0,
            email: "student@example.invalid".into(),
            plus_until: None,
            mailboxes: BTreeMap::new(),
            refused: BTreeMap::new(),
            emails: BTreeMap::new(),
            withdrawn: BTreeMap::new(),
            fail_after_keeping: false,
            fail_ack: false,
        }
    }
}

/// A mailbox as uploaded.
pub struct Kept {
    pub upload: MailboxUpload,
    pub pause: Option<Pause>,
}

pub struct Email {
    pub upload: EmailUpload,
    /// The whole message, uncompressed.
    pub message: String,
    pub state: Sending,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Sending {
    Waiting,
    Sending,
    Done(Outcome),
}

impl State {
    /// What becomes of email `id`; its mailbox goes once it has nothing left to send.
    #[expect(clippy::unwrap_used, reason = "tests finish only emails they uploaded")]
    pub fn finish(&mut self, id: &str, outcome: Outcome) {
        let email = self.emails.get_mut(id).unwrap();
        email.state = Sending::Done(outcome);
        let mailbox = email.upload.mailbox.clone();
        self.release(&mailbox);
    }

    fn release(&mut self, address: &str) {
        let waiting = self.emails.values().any(|e| {
            e.upload.mailbox == address && matches!(e.state, Sending::Waiting | Sending::Sending)
        });
        if !waiting {
            self.mailboxes.remove(address);
        }
    }

    fn listed(&self) -> Vec<Mailbox> {
        self.mailboxes
            .iter()
            .map(|(address, kept)| Mailbox {
                address: address.clone(),
                pause: kept.pause.clone(),
            })
            .collect()
    }

    fn plus(&self) -> bool {
        self.plus_until.is_some_and(|until| until > self.now)
    }
}

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
    let id = path.strip_prefix(EMAILS).and_then(|p| p.strip_prefix('/'));
    match (method, path, id) {
        ("GET", "/v1/account", _) => ok(serde_json::to_value(Account {
            email: state.email.clone(),
            plus_until: state.plus_until,
            tracking: Quota {
                used: 0,
                limit: 0,
                pack_left: 0,
                pack_expires_at: None,
            },
            terms: Some("9999-12-31".into()),
        })
        .unwrap()),
        ("GET", MAILBOXES, _) => ok(serde_json::to_value(Mailboxes {
            mailboxes: state.listed(),
        })
        .unwrap()),
        ("PUT", MAILBOXES, _) => {
            if !state.plus() {
                return error(403, "plus_required");
            }
            let upload: MailboxUpload = serde_json::from_slice(&body).unwrap();
            if let Some(why) = state.refused.get(&upload.address) {
                return ok(serde_json::to_value(MailboxSaved {
                    login_error: Some(why.clone()),
                })
                .unwrap());
            }
            state.mailboxes.insert(
                upload.address.clone(),
                Kept {
                    upload,
                    pause: None,
                },
            );
            ok(serde_json::to_value(MailboxSaved { login_error: None }).unwrap())
        }
        ("PUT", _, Some(id)) => {
            let upload: EmailUpload = serde_json::from_slice(&body).unwrap();
            if state.emails.contains_key(id) {
                return ok(serde_json::to_value(Empty {}).unwrap());
            }
            if state.withdrawn.contains_key(id) {
                return error(400, "invalid_input");
            }
            if !state.plus()
                || state
                    .plus_until
                    .is_some_and(|until| upload.planned_at > until)
            {
                return error(403, "plus_required");
            }
            if !state.mailboxes.contains_key(&upload.mailbox) {
                return error(400, "unknown_mailbox");
            }
            if upload.latest_at <= upload.planned_at || upload.latest_at <= state.now {
                return error(400, "invalid_input");
            }
            let compressed = BASE64_URL_SAFE_NO_PAD.decode(&upload.message).unwrap();
            let message = String::from_utf8(zstd::decode_all(&compressed[..]).unwrap()).unwrap();
            state.emails.insert(
                id.into(),
                Email {
                    upload,
                    message,
                    state: Sending::Waiting,
                },
            );
            if state.fail_after_keeping {
                return error(500, "internal");
            }
            ok(serde_json::to_value(Empty {}).unwrap())
        }
        ("DELETE", _, Some(id)) => {
            let answer = match state.emails.get(id).map(|e| e.state.clone()) {
                None if *state.withdrawn.entry(id.into()).or_insert(false) => Withdrawal::Withdrawn,
                None => Withdrawal::Unknown,
                Some(Sending::Waiting) => {
                    let email = state.emails.remove(id).unwrap();
                    state.release(&email.upload.mailbox);
                    state.withdrawn.insert(id.into(), true);
                    Withdrawal::Withdrawn
                }
                Some(Sending::Sending) => Withdrawal::Sending,
                Some(Sending::Done(outcome)) => Withdrawal::Finished { outcome },
            };
            ok(serde_json::to_value(answer).unwrap())
        }
        ("POST", RESULTS, _) => {
            let ids: Ids = serde_json::from_slice(&body).unwrap();
            let results = ids
                .ids
                .into_iter()
                .filter_map(|id| match &state.emails.get(&id)?.state {
                    Sending::Done(outcome) => Some(Finished {
                        id,
                        outcome: outcome.clone(),
                    }),
                    _ => None,
                })
                .collect();
            ok(serde_json::to_value(Results {
                results,
                mailboxes: state.listed(),
            })
            .unwrap())
        }
        ("POST", RESULTS_ACK, _) if state.fail_ack => error(500, "internal"),
        ("POST", RESULTS_ACK, _) => {
            let ids: Ids = serde_json::from_slice(&body).unwrap();
            for id in ids.ids {
                if matches!(
                    state.emails.get(&id).map(|e| &e.state),
                    Some(Sending::Done(_))
                ) {
                    state.emails.remove(&id);
                }
            }
            ok(serde_json::to_value(Empty {}).unwrap())
        }
        _ => error(404, "not_found"),
    }
}
