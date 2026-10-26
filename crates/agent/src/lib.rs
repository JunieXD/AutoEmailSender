//! The local transport shared by the desktop host and `autoemail`.

use std::io;
use std::path::{Path, PathBuf};

use serde::{Deserialize, Serialize};

const TOKEN_FILE: &str = "agent.token";

#[derive(Deserialize, Serialize)]
pub struct Credentials {
    pub token: String,
    #[cfg(windows)]
    pub pipe: String,
}

impl Credentials {
    pub fn generate() -> Self {
        Self {
            token: uuid::Uuid::new_v4().simple().to_string(),
            #[cfg(windows)]
            // Pipe names can be enumerated, so the name must not contain the token.
            pipe: format!(r"\\.\pipe\autoemail-{}", uuid::Uuid::new_v4().simple()),
        }
    }
}

pub fn token_path(dir: &Path) -> PathBuf {
    dir.join(TOKEN_FILE)
}

#[cfg(unix)]
pub type Incoming = tokio::net::UnixStream;
#[cfg(unix)]
pub type Listener = tokio::net::UnixListener;

#[cfg(unix)]
pub fn socket_path(dir: &Path) -> PathBuf {
    dir.join("agent.sock")
}

#[cfg(unix)]
pub fn listen(dir: &Path, _credentials: &Credentials) -> io::Result<Listener> {
    use std::os::unix::fs::PermissionsExt;
    let path = socket_path(dir);
    remove_if_present(&path)?;
    let listener = Listener::bind(&path)?;
    std::fs::set_permissions(path, std::fs::Permissions::from_mode(0o600))?;
    Ok(listener)
}

#[cfg(unix)]
pub async fn accept(listener: &mut Listener) -> io::Result<Incoming> {
    Ok(listener.accept().await?.0)
}

#[cfg(unix)]
pub async fn connect(dir: &Path, _credentials: &Credentials) -> io::Result<tokio::net::UnixStream> {
    tokio::net::UnixStream::connect(socket_path(dir)).await
}

#[cfg(windows)]
pub type Incoming = tokio::net::windows::named_pipe::NamedPipeServer;

#[cfg(windows)]
pub struct Listener {
    next: Incoming,
    name: String,
}

#[cfg(windows)]
pub fn listen(_dir: &Path, credentials: &Credentials) -> io::Result<Listener> {
    use tokio::net::windows::named_pipe::ServerOptions;
    let name = credentials.pipe.clone();
    let next = ServerOptions::new()
        .reject_remote_clients(true)
        .first_pipe_instance(true)
        .create(&name)?;
    Ok(Listener { next, name })
}

#[cfg(windows)]
pub async fn accept(listener: &mut Listener) -> io::Result<Incoming> {
    use tokio::net::windows::named_pipe::ServerOptions;
    listener.next.connect().await?;
    // Keep an instance alive while creating the next, so the name cannot be taken over.
    let next = ServerOptions::new()
        .reject_remote_clients(true)
        .create(&listener.name)?;
    Ok(std::mem::replace(&mut listener.next, next))
}

#[cfg(windows)]
pub async fn connect(
    _dir: &Path,
    credentials: &Credentials,
) -> io::Result<tokio::net::windows::named_pipe::NamedPipeClient> {
    use std::time::Duration;
    use tokio::net::windows::named_pipe::ClientOptions;
    // ERROR_PIPE_BUSY means the host is creating the next instance, as in Tokio's example.
    tokio::time::timeout(Duration::from_secs(5), async {
        loop {
            match ClientOptions::new().open(&credentials.pipe) {
                Ok(stream) => return Ok(stream),
                Err(error) if error.raw_os_error() == Some(231) => {
                    tokio::time::sleep(Duration::from_millis(25)).await;
                }
                Err(error) => return Err(error),
            }
        }
    })
    .await
    .map_err(|_| io::Error::new(io::ErrorKind::TimedOut, "agent pipe is busy"))?
}

pub fn remove_if_present(path: &Path) -> io::Result<()> {
    match std::fs::remove_file(path) {
        Ok(()) => Ok(()),
        Err(error) if error.kind() == io::ErrorKind::NotFound => Ok(()),
        Err(error) => Err(error),
    }
}
