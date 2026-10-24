//! What every connection to the outside shares: the system proxy, and TLS that trusts what the
//! system trusts (docs/design.md#桌面端).

use std::error::Error;
use std::sync::Arc;

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
        let http = http_builder()
            .tls_backend_preconfigured(tls.clone())
            .build()?;
        Ok(Net {
            tls: Arc::new(tls),
            roots,
            http,
        })
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

    /// Crawlers must check every redirect before following it.
    pub fn page_http_builder(&self) -> reqwest::ClientBuilder {
        http_builder()
            .redirect(reqwest::redirect::Policy::none())
            .tls_backend_preconfigured((*self.tls).clone())
    }

    /// Only for retrying public pages after a certificate error, never for credentials.
    pub fn untrusted_page_http_builder(&self) -> reqwest::ClientBuilder {
        let mut tls = (*self.tls).clone();
        tls.dangerous()
            .set_certificate_verifier(Arc::new(PageCertificate));
        http_builder()
            .redirect(reqwest::redirect::Policy::none())
            .tls_backend_preconfigured(tls)
    }
}

fn http_builder() -> reqwest::ClientBuilder {
    reqwest::Client::builder()
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
