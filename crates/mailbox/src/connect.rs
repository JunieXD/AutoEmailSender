//! Connections to mail servers: plain TCP through the system proxy, which lettre and async-imap
//! cannot open themselves, then TLS with the app's shared settings.

use std::fmt::Display;
use std::future::Future;
use std::io;
use std::net::SocketAddr;
use std::time::Duration;

use futures_util::StreamExt;
use futures_util::stream::FuturesUnordered;

use lettre::transport::smtp::client::{Certificate, TlsParameters, TlsParametersBuilder};
use net::Net;
use net::proxy::{self, Proxy};
use tokio::io::{AsyncReadExt, AsyncWriteExt};
use tokio::net::TcpStream;
use tokio_rustls::TlsConnector;
use tokio_rustls::client::TlsStream;
use tokio_rustls::rustls::pki_types::ServerName;
use tokio_socks::tcp::Socks5Stream;

use crate::{MailError, Server};

/// How long one check or send may take, from connecting to the server's last answer that matters.
const TIMEOUT: Duration = Duration::from_secs(30);

/// The slowest an email may upload, in bytes a second, before it is given up on as cut off. A large
/// attachment takes minutes on a slow line.
const MIN_UPLOAD_RATE: u64 = 32 * 1024;

/// How long to wait for one address before also trying the next (RFC 8305).
const STAGGER: Duration = Duration::from_millis(250);

/// How long a server whose work is done gets to answer a goodbye.
const FAREWELL: Duration = Duration::from_secs(5);

/// Encrypts a connection to `host`.
pub(crate) async fn encrypt(
    net: &Net,
    host: &str,
    stream: TcpStream,
) -> Result<TlsStream<TcpStream>, MailError> {
    let name = ServerName::try_from(host.to_owned()).map_err(|e| MailError::Tls {
        host: host.into(),
        source: io::Error::other(e),
    })?;
    TlsConnector::from(net.tls())
        .connect(name, stream)
        .await
        .map_err(|source| MailError::Tls {
            host: host.into(),
            source,
        })
}

/// The same trust for lettre, which upgrades SMTP connections with STARTTLS itself.
pub(crate) fn lettre_tls(net: &Net, host: &str) -> Result<TlsParameters, MailError> {
    let builder = net
        .roots()
        .iter()
        .try_fold(TlsParametersBuilder::new(host.into()), |builder, root| {
            Ok(builder.add_root_certificate(Certificate::from_der(root.to_vec())?))
        });
    builder
        .and_then(TlsParametersBuilder::build_rustls)
        .map_err(|e: lettre::transport::smtp::Error| MailError::Tls {
            host: host.into(),
            source: io::Error::other(e),
        })
}

/// Runs one check or send against `server`, giving up after `TIMEOUT`.
pub(crate) async fn within<T>(
    server: &Server,
    work: impl Future<Output = Result<T, MailError>>,
) -> Result<T, MailError> {
    limit(TIMEOUT, server, work).await
}

/// Uploads an email of `size` bytes to `server`, giving up after `TIMEOUT` and the time the upload
/// may take.
pub(crate) async fn uploading<T>(
    server: &Server,
    size: usize,
    work: impl Future<Output = Result<T, MailError>>,
) -> Result<T, MailError> {
    let upload = Duration::from_secs(size as u64 / MIN_UPLOAD_RATE);
    limit(TIMEOUT + upload, server, work).await
}

async fn limit<T>(
    time: Duration,
    server: &Server,
    work: impl Future<Output = Result<T, MailError>>,
) -> Result<T, MailError> {
    tokio::time::timeout(time, work).await.unwrap_or_else(|_| {
        Err(MailError::TimedOut {
            host: server.host.to_string(),
            port: server.port,
        })
    })
}

/// Says goodbye to a server in the background once its work is done. Its answer changes nothing, and
/// some servers, such as Gmail through a proxy, drop the connection or never answer.
pub(crate) fn farewell<E: Display>(
    server: &str,
    goodbye: impl Future<Output = Result<(), E>> + Send + 'static,
) {
    let server = server.to_owned();
    tokio::spawn(async move {
        match tokio::time::timeout(FAREWELL, goodbye).await {
            Ok(Ok(())) => {}
            Ok(Err(e)) => tracing::debug!("goodbye to {server}: {e}"),
            Err(_) => tracing::debug!("goodbye to {server}: no answer"),
        }
    });
}

/// A plain connection to `host:port`, through the system proxy unless the host is this computer.
pub(crate) async fn connect(host: &str, port: u16) -> Result<TcpStream, MailError> {
    let proxy = proxy::for_host(host);
    match &proxy {
        Some(p) => tracing::info!("connecting to {host}:{port} through {}", p.endpoint()),
        None => tracing::info!("connecting to {host}:{port}"),
    }
    tunnel(proxy.as_ref(), host, port).await
}

async fn tunnel(proxy: Option<&Proxy>, host: &str, port: u16) -> Result<TcpStream, MailError> {
    let Some(proxy) = proxy else {
        return direct(host, port).await;
    };
    let endpoint = proxy.endpoint();
    let down = |source| MailError::ProxyDown {
        proxy: endpoint.to_string(),
        source,
    };
    let failed = |reason: String| MailError::ProxyFailed {
        proxy: endpoint.to_string(),
        host: host.into(),
        port,
        reason,
    };
    let addrs: Vec<_> = tokio::net::lookup_host((endpoint.host.as_str(), endpoint.port))
        .await
        .map_err(down)?
        .collect();
    let stream = first_to_answer(&addrs).await.map_err(down)?;
    match proxy {
        // The proxy looks up the name, so a poisoned local DNS cannot misdirect it.
        Proxy::Socks5(_) => Socks5Stream::connect_with_socket(stream, (host, port))
            .await
            .map(Socks5Stream::into_inner)
            .map_err(|e| failed(e.to_string())),
        Proxy::Http(_) => http_connect(stream, host, port).await.map_err(failed),
    }
}

async fn direct(host: &str, port: u16) -> Result<TcpStream, MailError> {
    let addrs: Vec<_> = tokio::net::lookup_host((host, port))
        .await
        .map_err(|source| MailError::Resolve {
            host: host.into(),
            source,
        })?
        .collect();
    first_to_answer(&addrs)
        .await
        .map_err(|source| MailError::Connect {
            host: host.into(),
            port,
            source,
        })
}

/// Connects to whichever address answers first, starting on the next one every `STAGGER` until one
/// does (RFC 8305), so that an IPv6 route that swallows connections does not hold up working IPv4.
async fn first_to_answer(addrs: &[SocketAddr]) -> io::Result<TcpStream> {
    let mut waiting = addrs.iter();
    let mut attempts = FuturesUnordered::new();
    let mut error = io::Error::new(io::ErrorKind::NotFound, "no address");
    loop {
        match waiting.next() {
            Some(addr) => attempts.push(TcpStream::connect(*addr)),
            None if attempts.is_empty() => return Err(error),
            None => {}
        }
        tokio::select! {
            Some(attempt) = attempts.next() => match attempt {
                Ok(stream) => return Ok(stream),
                Err(e) => error = e,
            },
            () = tokio::time::sleep(STAGGER), if waiting.len() > 0 => {}
        }
    }
}

/// Asks an HTTP proxy, already connected, to open a tunnel; the error is the proxy's reason.
async fn http_connect(mut stream: TcpStream, host: &str, port: u16) -> Result<TcpStream, String> {
    stream
        .write_all(
            format!("CONNECT {host}:{port} HTTP/1.1\r\nHost: {host}:{port}\r\n\r\n").as_bytes(),
        )
        .await
        .map_err(|e| e.to_string())?;
    // Byte by byte, so that nothing the server sends after the reply is read away.
    let mut head = Vec::new();
    while !head.ends_with(b"\r\n\r\n") {
        if head.len() > 8192 {
            return Err("reply too long".into());
        }
        head.push(stream.read_u8().await.map_err(|e| e.to_string())?);
    }
    let head = String::from_utf8_lossy(&head);
    let status = head.lines().next().unwrap_or_default();
    if status.split(' ').nth(1) != Some("200") {
        return Err(status.to_owned());
    }
    Ok(stream)
}

#[cfg(test)]
mod tests {
    use tokio::io::{AsyncBufReadExt, BufReader};
    use tokio::net::TcpListener;

    use net::proxy::Endpoint;

    use super::*;

    /// Serves each connection with `serve` on a free port.
    async fn listen<F, Fut>(serve: F) -> u16
    where
        F: Fn(TcpStream) -> Fut + Send + 'static,
        Fut: Future<Output = std::io::Result<()>> + Send + 'static,
    {
        let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
        let port = listener.local_addr().unwrap().port();
        tokio::spawn(async move {
            loop {
                let (stream, _) = listener.accept().await.unwrap();
                tokio::spawn(serve(stream));
            }
        });
        port
    }

    async fn echo() -> u16 {
        listen(|mut stream| async move {
            let (mut read, mut write) = stream.split();
            tokio::io::copy(&mut read, &mut write).await.map(drop)
        })
        .await
    }

    /// A SOCKS5 proxy without a password that looks up the name it is given.
    async fn socks5() -> u16 {
        listen(|mut client| async move {
            let mut greeting = [0; 2];
            client.read_exact(&mut greeting).await?;
            client.read_exact(&mut vec![0; greeting[1].into()]).await?;
            client.write_all(&[5, 0]).await?;
            let mut request = [0; 5];
            client.read_exact(&mut request).await?;
            assert_eq!(request[..4], [5, 1, 0, 3], "CONNECT to a name");
            let mut name = vec![0; request[4].into()];
            client.read_exact(&mut name).await?;
            let port = client.read_u16().await?;
            let mut server = TcpStream::connect((String::from_utf8(name).unwrap(), port)).await?;
            client.write_all(&[5, 0, 0, 1, 0, 0, 0, 0, 0, 0]).await?;
            tokio::io::copy_bidirectional(&mut client, &mut server)
                .await
                .map(drop)
        })
        .await
    }

    /// An HTTP proxy that tunnels with CONNECT, except to port 25, as many refuse.
    async fn http() -> u16 {
        listen(|client| async move {
            let mut client = BufReader::new(client);
            let mut request = String::new();
            while !request.ends_with("\r\n\r\n") {
                client.read_line(&mut request).await?;
            }
            let target = request.split(' ').nth(1).unwrap().to_owned();
            if target.ends_with(":25") {
                return client.write_all(b"HTTP/1.1 403 Forbidden\r\n\r\n").await;
            }
            let mut server = TcpStream::connect(target).await?;
            client
                .write_all(b"HTTP/1.1 200 Connection established\r\n\r\n")
                .await?;
            tokio::io::copy_bidirectional(&mut client, &mut server)
                .await
                .map(drop)
        })
        .await
    }

    fn at(port: u16) -> Endpoint {
        Endpoint {
            host: "127.0.0.1".into(),
            port,
        }
    }

    #[tokio::test]
    async fn tunnels_through_socks5_and_http_proxies() {
        let echo = echo().await;
        for proxy in [
            Proxy::Socks5(at(socks5().await)),
            Proxy::Http(at(http().await)),
        ] {
            let mut stream = tunnel(Some(&proxy), "localhost", echo).await.unwrap();
            stream.write_all(b"EHLO").await.unwrap();
            let mut back = [0; 4];
            stream.read_exact(&mut back).await.unwrap();
            assert_eq!(&back, b"EHLO", "{proxy:?}");
        }
    }

    #[tokio::test]
    async fn proxy_failures_say_which_side_failed() {
        let closed = {
            let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
            listener.local_addr().unwrap().port()
        };
        for proxy in [Proxy::Socks5(at(closed)), Proxy::Http(at(closed))] {
            let down = tunnel(Some(&proxy), "localhost", 465).await;
            assert!(matches!(down, Err(MailError::ProxyDown { .. })), "{down:?}");
        }
        let refused = tunnel(Some(&Proxy::Http(at(http().await))), "localhost", 25).await;
        assert!(
            matches!(refused, Err(MailError::ProxyFailed { ref reason, .. }) if reason.contains("403")),
            "{refused:?}"
        );
    }
}
