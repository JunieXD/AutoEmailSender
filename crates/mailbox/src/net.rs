//! Connections to mail servers: through the system proxy when there is one, encrypted with TLS that
//! trusts what the system trusts.

use std::future::Future;
use std::net::IpAddr;
use std::sync::Arc;
use std::time::Duration;

use lettre::transport::smtp::client::{Certificate, TlsParameters, TlsParametersBuilder};
use rustls_platform_verifier::Verifier;
use tokio::io::{AsyncReadExt, AsyncWriteExt};
use tokio::net::TcpStream;
use tokio_rustls::TlsConnector;
use tokio_rustls::client::TlsStream;
use tokio_rustls::rustls::{self, ClientConfig, pki_types::CertificateDer, pki_types::ServerName};
use tokio_socks::tcp::Socks5Stream;

use crate::proxy::{self, Endpoint, Proxy};
use crate::{MailError, Server};

/// How long one check or send may take, from connecting to the last reply.
const TIMEOUT: Duration = Duration::from_secs(30);

/// TLS settings shared by every connection.
pub struct Net {
    tls: TlsConnector,
    /// Trusted besides the system's certificates; lettre needs them again for STARTTLS.
    roots: Vec<CertificateDer<'static>>,
}

impl Net {
    /// Trusts the system's certificates and `roots`, such as a test server's.
    pub fn new(roots: Vec<CertificateDer<'static>>) -> Result<Net, rustls::Error> {
        let provider = Arc::new(rustls::crypto::ring::default_provider());
        let verifier = Verifier::new_with_extra_roots(roots.clone(), provider.clone())?;
        let config = ClientConfig::builder_with_provider(provider)
            .with_safe_default_protocol_versions()?
            .dangerous()
            .with_custom_certificate_verifier(Arc::new(verifier))
            .with_no_client_auth();
        Ok(Net {
            tls: TlsConnector::from(Arc::new(config)),
            roots,
        })
    }

    /// Encrypts a connection to `host`.
    pub(crate) async fn tls(
        &self,
        host: &str,
        stream: TcpStream,
    ) -> Result<TlsStream<TcpStream>, MailError> {
        let name = ServerName::try_from(host.to_owned()).map_err(|e| MailError::Tls {
            host: host.into(),
            source: std::io::Error::other(e),
        })?;
        self.tls
            .connect(name, stream)
            .await
            .map_err(|source| MailError::Tls {
                host: host.into(),
                source,
            })
    }

    /// The same trust for lettre, which upgrades SMTP connections with STARTTLS itself.
    pub(crate) fn lettre_tls(&self, host: &str) -> Result<TlsParameters, MailError> {
        let builder = self
            .roots
            .iter()
            .try_fold(TlsParametersBuilder::new(host.into()), |builder, root| {
                Ok(builder.add_root_certificate(Certificate::from_der(root.to_vec())?))
            });
        builder
            .and_then(TlsParametersBuilder::build_rustls)
            .map_err(|e: lettre::transport::smtp::Error| MailError::Tls {
                host: host.into(),
                source: std::io::Error::other(e),
            })
    }
}

/// Runs one check or send against `server`, giving up after `TIMEOUT`.
pub(crate) async fn within<T>(
    server: &Server,
    work: impl Future<Output = Result<T, MailError>>,
) -> Result<T, MailError> {
    tokio::time::timeout(TIMEOUT, work)
        .await
        .unwrap_or_else(|_| {
            Err(MailError::TimedOut {
                host: server.host.to_string(),
                port: server.port,
            })
        })
}

/// A plain connection to `host:port`, through the system proxy unless the host is this computer.
pub(crate) async fn connect(host: &str, port: u16) -> Result<TcpStream, MailError> {
    let local = host.eq_ignore_ascii_case("localhost")
        || host.parse::<IpAddr>().is_ok_and(|ip| ip.is_loopback());
    let proxy = if local { None } else { proxy::system() };
    match &proxy {
        Some(p) => tracing::info!("connecting to {host}:{port} through {}", p.endpoint()),
        None => tracing::info!("connecting to {host}:{port}"),
    }
    tunnel(proxy.as_ref(), host, port).await
}

async fn tunnel(proxy: Option<&Proxy>, host: &str, port: u16) -> Result<TcpStream, MailError> {
    match proxy {
        None => direct(host, port).await,
        Some(Proxy::Socks5(p)) => {
            // The proxy looks up the name, so a poisoned local DNS cannot misdirect it.
            Socks5Stream::connect((p.host.as_str(), p.port), (host, port))
                .await
                .map(Socks5Stream::into_inner)
                .map_err(|e| match e {
                    tokio_socks::Error::Io(source) => MailError::ProxyDown {
                        proxy: p.to_string(),
                        source,
                    },
                    e => MailError::ProxyFailed {
                        proxy: p.to_string(),
                        host: host.into(),
                        port,
                        reason: e.to_string(),
                    },
                })
        }
        Some(Proxy::Http(p)) => http_connect(p, host, port).await,
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
    TcpStream::connect(&addrs[..])
        .await
        .map_err(|source| MailError::Connect {
            host: host.into(),
            port,
            source,
        })
}

/// Opens a tunnel through an HTTP proxy with CONNECT.
async fn http_connect(proxy: &Endpoint, host: &str, port: u16) -> Result<TcpStream, MailError> {
    let down = |source| MailError::ProxyDown {
        proxy: proxy.to_string(),
        source,
    };
    let failed = |reason: String| MailError::ProxyFailed {
        proxy: proxy.to_string(),
        host: host.into(),
        port,
        reason,
    };
    let mut stream = TcpStream::connect((proxy.host.as_str(), proxy.port))
        .await
        .map_err(down)?;
    stream
        .write_all(
            format!("CONNECT {host}:{port} HTTP/1.1\r\nHost: {host}:{port}\r\n\r\n").as_bytes(),
        )
        .await
        .map_err(|e| failed(e.to_string()))?;
    // Byte by byte, so that nothing the server sends after the reply is read away.
    let mut head = Vec::new();
    while !head.ends_with(b"\r\n\r\n") {
        if head.len() > 8192 {
            return Err(failed("reply too long".into()));
        }
        head.push(stream.read_u8().await.map_err(|e| failed(e.to_string()))?);
    }
    let head = String::from_utf8_lossy(&head);
    let status = head.lines().next().unwrap_or_default();
    if status.split(' ').nth(1) != Some("200") {
        return Err(failed(status.to_owned()));
    }
    Ok(stream)
}

#[cfg(test)]
mod tests {
    use tokio::io::{AsyncBufReadExt, BufReader};
    use tokio::net::TcpListener;

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
        let down = tunnel(Some(&Proxy::Http(at(closed))), "localhost", 465).await;
        assert!(matches!(down, Err(MailError::ProxyDown { .. })), "{down:?}");
        let refused = tunnel(Some(&Proxy::Http(at(http().await))), "localhost", 25).await;
        assert!(
            matches!(refused, Err(MailError::ProxyFailed { ref reason, .. }) if reason.contains("403")),
            "{refused:?}"
        );
    }
}
