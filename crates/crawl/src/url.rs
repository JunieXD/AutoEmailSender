use std::net::{Ipv4Addr, Ipv6Addr};

use sqlx::{
    Decode, Encode, Sqlite, Type,
    encode::IsNull,
    error::BoxDynError,
    sqlite::{SqliteArgumentsBuffer, SqliteTypeInfo, SqliteValueRef},
};
use url::{Host, Url};

use crate::{CrawlError, Material};

/// A normalized, public HTTP URL. No DNS lookups: proxies may use fake IPs.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct PageUrl(Url);

impl Type<Sqlite> for PageUrl {
    fn type_info() -> SqliteTypeInfo {
        <str as Type<Sqlite>>::type_info()
    }
}

impl<'r> Decode<'r, Sqlite> for PageUrl {
    fn decode(value: SqliteValueRef<'r>) -> Result<Self, BoxDynError> {
        Ok(Self::parse(<&str as Decode<Sqlite>>::decode(value)?)?)
    }
}

impl Encode<'_, Sqlite> for PageUrl {
    fn encode_by_ref(&self, args: &mut SqliteArgumentsBuffer) -> Result<IsNull, BoxDynError> {
        <&str as Encode<Sqlite>>::encode(self.as_str(), args)
    }
}

impl PageUrl {
    pub fn parse(value: &str) -> Result<Self, CrawlError> {
        let mut url = Url::parse(value).map_err(|_| CrawlError::Url)?;
        if !matches!(url.scheme(), "http" | "https")
            || !url.username().is_empty()
            || url.password().is_some()
            || value.contains(char::is_whitespace)
        {
            return Err(CrawlError::Url);
        }
        if let Some(host) = url
            .host_str()
            .filter(|h| h.ends_with('.'))
            .map(str::to_owned)
        {
            url.set_host(Some(host.trim_end_matches('.')))
                .map_err(|_| CrawlError::Url)?;
        }
        match url.host().ok_or(CrawlError::Url)? {
            Host::Domain(host) => {
                if !host.contains('.')
                    || host == "localhost"
                    || host.ends_with(".localhost")
                    || host.ends_with(".local")
                    || host.ends_with(".internal")
                {
                    return Err(CrawlError::Url);
                }
            }
            Host::Ipv4(ip) if !public_v4(ip) => return Err(CrawlError::Url),
            Host::Ipv6(ip) if !public_v6(ip) => return Err(CrawlError::Url),
            _ => {}
        }
        url.set_fragment(None);
        Ok(Self(url))
    }

    pub fn join(&self, value: &str) -> Result<Self, CrawlError> {
        let url = self.0.join(value).map_err(|_| CrawlError::Url)?;
        Self::parse(url.as_str())
    }

    pub fn as_str(&self) -> &str {
        self.0.as_str()
    }

    pub(crate) fn is_normalization_of(&self, requested: &Self) -> bool {
        let (from, to) = (&requested.0, &self.0);
        let from_host = from
            .host_str()
            .map(|host| host.strip_prefix("www.").unwrap_or(host));
        let to_host = to
            .host_str()
            .map(|host| host.strip_prefix("www.").unwrap_or(host));
        (from.scheme() == to.scheme() || (from.scheme() == "http" && to.scheme() == "https"))
            && from_host == to_host
            && from.port() == to.port()
            && from.path().strip_suffix('/').unwrap_or(from.path())
                == to.path().strip_suffix('/').unwrap_or(to.path())
            && from.query() == to.query()
    }

    pub(crate) fn website(&self) -> String {
        match self.0.host() {
            Some(Host::Domain(host)) => psl::domain_str(host).unwrap_or(host).to_owned(),
            _ => self.0.host_str().unwrap_or_default().to_owned(),
        }
    }

    pub(crate) fn parsed(&self) -> &Url {
        &self.0
    }
}

fn public_v4(ip: Ipv4Addr) -> bool {
    let [a, b, _, _] = ip.octets();
    !ip.is_private()
        && !ip.is_loopback()
        && !ip.is_link_local()
        && !ip.is_unspecified()
        && !ip.is_broadcast()
        && !ip.is_multicast()
        && a != 0
        && a < 240
        && !(a == 100 && (64..=127).contains(&b))
        && !(a == 198 && (b == 18 || b == 19))
}

fn public_v6(ip: Ipv6Addr) -> bool {
    if let Some(ip) = ip.to_ipv4_mapped() {
        return public_v4(ip);
    }
    let first = ip.segments()[0];
    // Only global unicast; excludes unique-local, link-local, multicast and unspecified addresses.
    (0x2000..=0x3fff).contains(&first)
}

/// The website selected by the user. A linked external profile grants only that page.
pub struct Scope {
    website: String,
}

pub struct PageTarget {
    pub(crate) url: PageUrl,
}

impl PageTarget {
    pub fn url(&self) -> &PageUrl {
        &self.url
    }

    pub(crate) fn redirect(
        &self,
        current: &PageUrl,
        location: &str,
    ) -> Result<PageUrl, CrawlError> {
        let next = current.join(location)?;
        if next.website() != self.url.website() {
            return Err(CrawlError::OutsideSite);
        }
        Ok(next)
    }
}

impl Scope {
    pub fn new(start: &PageUrl) -> Self {
        Self {
            website: start.website(),
        }
    }

    pub fn page(&self, url: PageUrl) -> Result<PageTarget, CrawlError> {
        if url.website() != self.website {
            return Err(CrawlError::OutsideSite);
        }
        Ok(PageTarget { url })
    }

    /// Called after the next stage identifies a profile link in an actual list page.
    pub fn linked_profile(&self, list: &Material, url: &PageUrl) -> Result<PageTarget, CrawlError> {
        if list.url.website() != self.website
            || !list
                .units
                .iter()
                .flat_map(|u| &u.links)
                .any(|link| link.url == *url)
        {
            return Err(CrawlError::OutsideSite);
        }
        Ok(PageTarget { url: url.clone() })
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn normalization_keeps_the_same_page() {
        for (requested, destination) in [
            (
                "http://school.example.edu/profile",
                "https://school.example.edu/profile",
            ),
            (
                "https://school.example.edu/profile",
                "https://www.school.example.edu/profile",
            ),
            (
                "https://www.school.example.edu/profile",
                "https://school.example.edu/profile",
            ),
            (
                "https://SCHOOL.EXAMPLE.EDU/profile",
                "https://school.example.edu/profile",
            ),
            (
                "http://school.example.edu:80/profile",
                "http://school.example.edu/profile",
            ),
            (
                "https://school.example.edu:443/profile",
                "https://school.example.edu/profile",
            ),
            (
                "https://school.example.edu/profile",
                "https://school.example.edu/profile/",
            ),
            (
                "https://school.example.edu/profile/",
                "https://school.example.edu/profile",
            ),
            (
                "http://SCHOOL.EXAMPLE.EDU:80/profile?id=1",
                "https://www.school.example.edu:443/profile/?id=1",
            ),
        ] {
            assert!(
                PageUrl::parse(destination)
                    .unwrap()
                    .is_normalization_of(&PageUrl::parse(requested).unwrap()),
                "{requested} -> {destination}",
            );
        }
    }

    #[test]
    fn normalization_does_not_hide_other_url_changes() {
        let requested = PageUrl::parse("https://school.example.edu/profile?id=1").unwrap();
        for destination in [
            "http://school.example.edu/profile?id=1",
            "https://school.example.edu/another?id=1",
            "https://school.example.edu/Profile?id=1",
            "https://school.example.edu/profile//?id=1",
            "https://school.example.edu/profile?id=2",
            "https://school.example.edu/profile",
            "https://other.school.example.edu/profile?id=1",
            "https://www.www.school.example.edu/profile?id=1",
            "https://school.example.edu:8443/profile?id=1",
        ] {
            assert!(
                !PageUrl::parse(destination)
                    .unwrap()
                    .is_normalization_of(&requested),
                "{destination}",
            );
        }
    }
}
