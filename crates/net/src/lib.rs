//! What every connection to the outside shares: the system proxy, and TLS that trusts what the
//! system trusts (docs/design.md#桌面端).

use std::error::Error;
use std::net::IpAddr;
use std::sync::Arc;
use std::time::{Duration, SystemTime};

use rustls::ClientConfig;
use rustls::pki_types::CertificateDer;
use rustls_platform_verifier::Verifier;

pub mod proxy;

pub use rustls;

/// Cheap to clone: clones share the TLS settings and the HTTP connection pool.
#[derive(Clone)]
pub struct Net {
    tls: Arc<ClientConfig>,
    roots: Vec<CertificateDer<'static>>,
    http: reqwest::Client,
    public_only: bool,
}

impl Net {
    /// Trusts the system's certificates and `roots`, such as a test server's.
    pub fn new(roots: Vec<CertificateDer<'static>>) -> Result<Net, Box<dyn Error + Send + Sync>> {
        let provider = Arc::new(rustls::crypto::ring::default_provider());
        let verifier = Verifier::new_with_extra_roots(roots.clone(), provider.clone())?;
        let tls = ClientConfig::builder_with_provider(provider)
            .with_safe_default_protocol_versions()?
            .dangerous()
            .with_custom_certificate_verifier(Arc::new(verifier))
            .with_no_client_auth();
        let http = http_builder(reqwest::Client::builder())
            .tls_backend_preconfigured(tls.clone())
            .build()?;
        Ok(Net {
            tls: Arc::new(tls),
            roots,
            http,
            public_only: false,
        })
    }

    /// For the official server, which connects to the mail servers users name: those connections
    /// only go to public addresses, never into the server's own network.
    pub fn public_only(self) -> Net {
        Net {
            public_only: true,
            ..self
        }
    }

    /// Whether a mail server at `addr` may be connected to.
    pub fn reaches(&self, addr: IpAddr) -> bool {
        !self.public_only || public(addr.to_canonical())
    }

    pub fn tls(&self) -> Arc<ClientConfig> {
        self.tls.clone()
    }

    /// Trusted besides the system's certificates, for libraries that build their own TLS settings.
    pub fn roots(&self) -> &[CertificateDer<'static>] {
        &self.roots
    }

    /// For HTTP requests, through the system proxy.
    pub fn http(&self) -> &reqwest::Client {
        &self.http
    }

    /// Applies the same proxy and TLS policy to libraries that build their own HTTP client.
    pub fn configure_http(&self, builder: reqwest::ClientBuilder) -> reqwest::ClientBuilder {
        http_builder(builder).tls_backend_preconfigured((*self.tls).clone())
    }

    /// Crawlers must check every redirect before following it.
    pub fn page_http_builder(&self) -> reqwest::ClientBuilder {
        http_builder(reqwest::Client::builder())
            .redirect(reqwest::redirect::Policy::none())
            .tls_backend_preconfigured((*self.tls).clone())
    }

    /// Only for retrying public pages after a certificate error, never for credentials.
    pub fn untrusted_page_http_builder(&self) -> reqwest::ClientBuilder {
        let mut tls = (*self.tls).clone();
        tls.dangerous()
            .set_certificate_verifier(Arc::new(PageCertificate));
        http_builder(reqwest::Client::builder())
            .redirect(reqwest::redirect::Policy::none())
            .tls_backend_preconfigured(tls)
    }
}

/// Not this computer, its networks or addresses reserved for special uses.
fn public(addr: IpAddr) -> bool {
    match addr {
        IpAddr::V4(ip) => {
            let [a, b, ..] = ip.octets();
            !(ip.is_unspecified()
                || ip.is_loopback()
                || ip.is_private()
                || ip.is_link_local()
                || ip.is_broadcast()
                || ip.is_multicast()
                || ip.is_documentation()
                || a == 0
                || a >= 240
                // Shared by carriers' NAT (RFC 6598).
                || (a == 100 && b & 0xc0 == 64))
        }
        IpAddr::V6(ip) => {
            let first = ip.segments()[0];
            !(ip.is_unspecified()
                || ip.is_loopback()
                || ip.is_multicast()
                // Unique local and link-local.
                || first & 0xfe00 == 0xfc00
                || first & 0xffc0 == 0xfe80)
        }
    }
}

fn http_builder(builder: reqwest::ClientBuilder) -> reqwest::ClientBuilder {
    builder
        // Asked for each new connection, so changed system settings apply from the next one.
        .proxy(reqwest::Proxy::custom(|url| {
            proxy::for_host(url.host_str()?).map(|p| p.url())
        }))
        .user_agent(concat!("AutoEmailSender/", env!("CARGO_PKG_VERSION")))
}

#[derive(Debug)]
struct PageCertificate;

impl rustls::client::danger::ServerCertVerifier for PageCertificate {
    fn verify_server_cert(
        &self,
        _: &CertificateDer<'_>,
        _: &[CertificateDer<'_>],
        _: &rustls::pki_types::ServerName<'_>,
        _: &[u8],
        _: rustls::pki_types::UnixTime,
    ) -> Result<rustls::client::danger::ServerCertVerified, rustls::Error> {
        Ok(rustls::client::danger::ServerCertVerified::assertion())
    }

    fn verify_tls12_signature(
        &self,
        message: &[u8],
        cert: &CertificateDer<'_>,
        signature: &rustls::DigitallySignedStruct,
    ) -> Result<rustls::client::danger::HandshakeSignatureValid, rustls::Error> {
        rustls::crypto::verify_tls12_signature(
            message,
            cert,
            signature,
            &rustls::crypto::ring::default_provider().signature_verification_algorithms,
        )
    }

    fn verify_tls13_signature(
        &self,
        message: &[u8],
        cert: &CertificateDer<'_>,
        signature: &rustls::DigitallySignedStruct,
    ) -> Result<rustls::client::danger::HandshakeSignatureValid, rustls::Error> {
        rustls::crypto::verify_tls13_signature(
            message,
            cert,
            signature,
            &rustls::crypto::ring::default_provider().signature_verification_algorithms,
        )
    }

    fn supported_verify_schemes(&self) -> Vec<rustls::SignatureScheme> {
        rustls::crypto::ring::default_provider()
            .signature_verification_algorithms
            .supported_schemes()
    }
}

/// How long a refusing server asked to be left alone, in seconds or until a date. At most a day,
/// which no caller waits out anyway, so that deadlines computed from it stay representable.
pub fn retry_after(response: &reqwest::Response) -> Option<Duration> {
    let value = response
        .headers()
        .get(reqwest::header::RETRY_AFTER)?
        .to_str()
        .ok()?;
    let wait = value.parse().ok().map(Duration::from_secs).or_else(|| {
        httpdate::parse_http_date(value)
            .ok()
            .map(|at| at.duration_since(SystemTime::now()).unwrap_or_default())
    })?;
    Some(wait.min(Duration::from_secs(24 * 60 * 60)))
}

/// Whether the server's certificate was the problem rather than the handshake, however deep the
/// libraries wrapped the TLS error.
pub fn untrusted(e: &(dyn Error + 'static)) -> bool {
    let mut next = Some(e);
    while let Some(e) = next {
        if let Some(rustls::Error::InvalidCertificate(_)) = e.downcast_ref::<rustls::Error>() {
            return true;
        }
        // An io::Error's source skips the error it wraps.
        next = match e
            .downcast_ref::<std::io::Error>()
            .and_then(std::io::Error::get_ref)
        {
            Some(inner) => Some(inner),
            None => e.source(),
        };
    }
    false
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_official_server_reaches_only_public_addresses() {
        let addr = |a: &str| a.parse::<IpAddr>().unwrap();
        let server = Net::new(Vec::new()).unwrap().public_only();
        for a in ["220.181.12.12", "2408:8000::1", "::ffff:220.181.12.12"] {
            assert!(server.reaches(addr(a)), "{a}");
        }
        for a in [
            "127.0.0.1",
            "10.0.0.5",
            "172.17.0.1",
            "192.168.1.1",
            "169.254.169.254",
            "100.100.100.200",
            "0.0.0.0",
            "255.255.255.255",
            "::1",
            "fd00::1",
            "fe80::1",
            "::ffff:127.0.0.1",
        ] {
            assert!(!server.reaches(addr(a)), "{a}");
        }
        // The app connects to mail bridges on the computer itself.
        assert!(Net::new(Vec::new()).unwrap().reaches(addr("127.0.0.1")));
    }
}
