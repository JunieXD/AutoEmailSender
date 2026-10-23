//! What every connection to the outside shares: the system proxy, and TLS that trusts what the
//! system trusts (docs/design.md#桌面端).

use std::error::Error;
use std::sync::Arc;

use rustls::ClientConfig;
use rustls::pki_types::CertificateDer;
use rustls_platform_verifier::Verifier;

pub mod proxy;

pub use rustls;

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
        let http = reqwest::Client::builder()
            .tls_backend_preconfigured(tls.clone())
            // Asked for every request, so changing the system proxy takes effect at once.
            .proxy(reqwest::Proxy::custom(|url| {
                proxy::for_host(url.host_str()?).map(|p| p.url())
            }))
            .user_agent(concat!("AutoEmailSender/", env!("CARGO_PKG_VERSION")))
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
