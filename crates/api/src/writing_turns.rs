//! Turns at the model for a composer's AI writing, which the user can cancel until the call goes
//! out (docs/design.md#联系).

use std::collections::HashMap;
use std::sync::Mutex;

use serde::Deserialize;
use tokio::sync::{OwnedSemaphorePermit, oneshot};
use ts_rs::TS;

use crate::{CallError, Context};

/// A composer's AI writing request.
#[derive(Debug, Deserialize, TS)]
pub struct Writing<T> {
    /// Chosen by the app's UI, so that `cancelWriting` can withdraw the call before it reaches the
    /// model; agents leave it out.
    #[serde(default)]
    #[ts(optional)]
    pub request: Option<String>,
    #[serde(flatten)]
    pub edit: T,
}

#[derive(Debug, Deserialize, TS)]
pub struct WritingRequest {
    pub request: String,
}

/// The UI's calls, by request, until each ends. The sender is there until `Turn::send` or
/// `cancel_writing` takes it, whichever comes first; dropping it wakes a call still waiting for a
/// turn.
#[derive(Default)]
pub struct Writings(Mutex<HashMap<String, Option<oneshot::Sender<()>>>>);
impl Writings {
    fn calls(&self) -> std::sync::MutexGuard<'_, HashMap<String, Option<oneshot::Sender<()>>>> {
        self.0
            .lock()
            .unwrap_or_else(|_| unreachable!("nothing panics while holding the lock"))
    }
}

/// A request in `Writings`, taken out however its call ends.
struct Listed<'a> {
    writings: &'a Writings,
    request: String,
}
impl Drop for Listed<'_> {
    fn drop(&mut self) {
        self.writings.calls().remove(&self.request);
    }
}

/// A turn at the model; until `send`, the UI can still withdraw the call. It keeps the request
/// taken until the call ends.
pub(crate) struct Turn<'a> {
    _permit: OwnedSemaphorePermit,
    listed: Option<Listed<'a>>,
}
impl Turn<'_> {
    /// Right before the call goes to the model: withdrawn by now, it does not go; from here on it
    /// goes whatever the UI does.
    pub(crate) fn send(&self) -> Result<(), CallError> {
        let Some(listed) = &self.listed else {
            return Ok(());
        };
        let mut calls = listed.writings.calls();
        match calls.get_mut(&listed.request).and_then(Option::take) {
            Some(_) => Ok(()),
            None => Err(canceled()),
        }
    }
}

fn canceled() -> CallError {
    CallError::invalid("这个请求已经取消".into())
}

impl Context {
    /// A turn at the model, unless the UI cancels `request` first.
    pub(crate) async fn writing_turn(
        &self,
        request: Option<String>,
    ) -> Result<Turn<'_>, CallError> {
        let Some(request) = request else {
            return Ok(Turn {
                _permit: self.model_limit.acquire().await,
                listed: None,
            });
        };
        let (cancel, canceled_early) = oneshot::channel();
        {
            let mut calls = self.writings.calls();
            if calls.contains_key(&request) {
                return Err(CallError::bad_input("这个请求编号已经在用"));
            }
            calls.insert(request.clone(), Some(cancel));
        }
        let listed = Listed {
            writings: &self.writings,
            request,
        };
        tokio::select! {
            permit = self.model_limit.acquire() => Ok(Turn {
                _permit: permit,
                listed: Some(listed),
            }),
            _ = canceled_early => Err(canceled()),
        }
    }
}

/// Withdraws a call not sent to the model yet; one already sent goes on, and its usage is
/// recorded.
pub async fn cancel_writing(ctx: &Context, input: WritingRequest) -> Result<(), CallError> {
    if let Some(call) = ctx.writings.calls().get_mut(&input.request) {
        call.take();
    }
    Ok(())
}
