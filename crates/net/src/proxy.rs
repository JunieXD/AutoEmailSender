//! The system proxy, read for every connection so that changing it takes effect at once
//! (docs/design.md#桌面端).

use std::fmt;
use std::net::IpAddr;

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Proxy {
    Socks5(Endpoint),
    /// Tunnels with HTTP CONNECT.
    Http(Endpoint),
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Endpoint {
    pub host: String,
    pub port: u16,
}

impl fmt::Display for Endpoint {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "{}:{}", self.host, self.port)
    }
}

impl Proxy {
    pub fn endpoint(&self) -> &Endpoint {
        match self {
            Proxy::Socks5(e) | Proxy::Http(e) => e,
        }
    }

    /// As reqwest takes it; with SOCKS the proxy looks up the name.
    pub fn url(&self) -> String {
        match self {
            Proxy::Socks5(e) => format!("socks5h://{e}"),
            Proxy::Http(e) => format!("http://{e}"),
        }
    }
}

/// The proxy for a connection to `host`, unless the host is this computer.
pub fn for_host(host: &str) -> Option<Proxy> {
    let bare = host.trim_start_matches('[').trim_end_matches(']');
    let local = bare.eq_ignore_ascii_case("localhost")
        || bare.parse::<IpAddr>().is_ok_and(|ip| ip.is_loopback());
    if local { None } else { system() }
}

/// The proxy set in System Settings › Network, if any.
#[cfg(target_os = "macos")]
fn system() -> Option<Proxy> {
    use system_configuration::core_foundation::number::CFNumber;
    use system_configuration::core_foundation::string::CFString;
    use system_configuration::dynamic_store::SCDynamicStoreBuilder;

    let proxies = SCDynamicStoreBuilder::new("AutoEmailSender")
        .build()?
        .get_proxies()?;
    let number = |key: &str| {
        proxies
            .find(CFString::new(key))?
            .downcast::<CFNumber>()?
            .to_i64()
    };
    let endpoint = |kind: &str| {
        if number(&format!("{kind}Enable")) != Some(1) {
            return None;
        }
        let host = proxies
            .find(CFString::new(&format!("{kind}Proxy")))?
            .downcast::<CFString>()?
            .to_string();
        let port = u16::try_from(number(&format!("{kind}Port"))?).ok()?;
        Some(Endpoint { host, port })
    };
    // macOS's SOCKS proxy is SOCKS5; its HTTPS proxy is the one meant for CONNECT.
    endpoint("SOCKS")
        .map(Proxy::Socks5)
        .or_else(|| endpoint("HTTPS").map(Proxy::Http))
}

/// The proxy set in Settings › Network & internet › Proxy, if any.
#[cfg(windows)]
fn system() -> Option<Proxy> {
    use winreg::RegKey;
    use winreg::enums::HKEY_CURRENT_USER;

    let settings = RegKey::predef(HKEY_CURRENT_USER)
        .open_subkey(r"Software\Microsoft\Windows\CurrentVersion\Internet Settings")
        .ok()?;
    let enabled: u32 = settings.get_value("ProxyEnable").ok()?;
    if enabled == 0 {
        return None;
    }
    windows_proxy(&settings.get_value::<String, _>("ProxyServer").ok()?)
}

/// Windows' `ProxyServer`: `host:port` for every protocol, or `http=host:port;https=host:port;
/// socks=host:port` per protocol. HTTP comes first, as Windows may mean SOCKS4 by `socks`.
#[cfg(any(windows, test))]
fn windows_proxy(server: &str) -> Option<Proxy> {
    let endpoint = |s: &str| {
        let (host, port) = s.trim().rsplit_once(':')?;
        Some(Endpoint {
            host: host.to_owned(),
            port: port.parse().ok()?,
        })
    };
    if !server.contains('=') {
        return endpoint(server).map(Proxy::Http);
    }
    let entry = |scheme: &str| {
        server
            .split(';')
            .filter_map(|e| e.split_once('='))
            .find(|(k, _)| k.trim().eq_ignore_ascii_case(scheme))
            .and_then(|(_, v)| endpoint(v))
    };
    entry("https")
        .or_else(|| entry("http"))
        .map(Proxy::Http)
        .or_else(|| entry("socks").map(Proxy::Socks5))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn at(port: u16) -> Endpoint {
        Endpoint {
            host: "127.0.0.1".into(),
            port,
        }
    }

    #[test]
    fn windows_proxy_settings() {
        assert_eq!(windows_proxy("127.0.0.1:7890"), Some(Proxy::Http(at(7890))));
        assert_eq!(
            windows_proxy("http=127.0.0.1:1;https=127.0.0.1:2;socks=127.0.0.1:3"),
            Some(Proxy::Http(at(2)))
        );
        assert_eq!(
            windows_proxy("ftp=127.0.0.1:1;SOCKS=127.0.0.1:3"),
            Some(Proxy::Socks5(at(3)))
        );
        assert_eq!(windows_proxy("ftp=127.0.0.1:1"), None);
        assert_eq!(windows_proxy("127.0.0.1"), None);
    }
}
