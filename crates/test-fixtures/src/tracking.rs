//! An in-memory email tracking server following the official server's rules (the server
//! repository's docs/tracking.md), for one account.
use std::collections::BTreeMap;

use cloud_api::{
    Account,
    tracking::{ALLOWANCE, Open, Opens, Quota, Register, Registered},
};

use crate::http::{Reply, error, ok};

pub struct State {
    pub now: i64,
    pub email: String,
    pub plus_until: Option<i64>,
    /// What the packs have left; the allowance of the 30 days is used up.
    pub left: u32,
    /// Registered ids, and the opens of those opened.
    pub emails: BTreeMap<String, Option<Opened>>,
    pub revision: i64,
    /// Opens per page; the server's is `cloud_api::tracking::PAGE`.
    pub page: usize,
    /// Fetching the opens after this revision fails.
    pub fail_after: Option<i64>,
}

#[derive(Clone)]
pub struct Opened {
    pub opens: u32,
    pub first_at: i64,
    pub last_at: i64,
    pub revision: i64,
}

impl Default for State {
    fn default() -> Self {
        Self {
            now: 0,
            email: "student@example.invalid".into(),
            plus_until: None,
            left: 0,
            emails: BTreeMap::new(),
            revision: 0,
            page: cloud_api::tracking::PAGE,
            fail_after: None,
        }
    }
}

impl State {
    fn quota(&self) -> Quota {
        Quota {
            used: ALLOWANCE,
            limit: ALLOWANCE,
            pack_left: self.left,
            pack_expires_at: (self.left > 0)
                .then_some(self.now + cloud_api::tracking::PACK_LIFETIME),
        }
    }

    /// The image of a registered email loads.
    #[expect(clippy::unwrap_used, reason = "tests open only emails they registered")]
    pub fn open(&mut self, id: &str) {
        self.revision += 1;
        let (now, revision) = (self.now, self.revision);
        let opened = self.emails.get_mut(id).unwrap();
        let first_at = opened.as_ref().map_or(now, |o| o.first_at);
        let opens = opened.as_ref().map_or(0, |o| o.opens) + 1;
        *opened = Some(Opened {
            opens,
            first_at,
            last_at: now,
            revision,
        });
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
    let plus = state.plus_until.is_some_and(|until| until > state.now);
    match (method, path.split_once('?').unwrap_or((path, ""))) {
        ("GET", ("/v1/account", _)) => ok(serde_json::to_value(Account {
            email: state.email.clone(),
            plus_until: state.plus_until,
            tracking: state.quota(),
            // No older than any terms the app has, so its login counts as agreed.
            terms: Some("9999-12-31".into()),
        })
        .unwrap()),
        ("POST", ("/v1/tracking", _)) => {
            let register: Register = serde_json::from_slice(&body).unwrap();
            let (mut accepted, mut rejected) = (Vec::new(), Vec::new());
            for id in register.ids {
                if state.emails.contains_key(&id) {
                    accepted.push(id);
                } else if plus && state.left > 0 {
                    state.left -= 1;
                    state.emails.insert(id.clone(), None);
                    accepted.push(id);
                } else {
                    rejected.push(id);
                }
            }
            ok(serde_json::to_value(Registered {
                accepted,
                rejected,
                plus_ended: !plus,
                tracking: state.quota(),
            })
            .unwrap())
        }
        ("GET", ("/v1/tracking/opens", query)) => {
            let after: i64 = query.strip_prefix("after=").unwrap().parse().unwrap();
            if state.fail_after == Some(after) {
                return error(500, "internal");
            }
            let mut opened: Vec<_> = state
                .emails
                .iter()
                .filter_map(|(id, o)| o.clone().map(|o| (id.clone(), o)))
                .filter(|(_, o)| o.revision > after)
                .collect();
            opened.sort_by_key(|(_, o)| o.revision);
            let more = opened.len() > state.page;
            opened.truncate(state.page);
            ok(serde_json::to_value(Opens {
                revision: opened.last().map_or(after, |(_, o)| o.revision),
                opens: opened
                    .into_iter()
                    .map(|(id, o)| Open {
                        id,
                        opens: o.opens,
                        first_at: o.first_at,
                        last_at: o.last_at,
                    })
                    .collect(),
                more,
            })
            .unwrap())
        }
        _ => error(404, "not_found"),
    }
}
