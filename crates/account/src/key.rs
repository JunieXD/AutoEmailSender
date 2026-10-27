use argon2::{Algorithm, Argon2, Params, Version};
use base64::{Engine, prelude::BASE64_URL_SAFE_NO_PAD};
use hkdf::Hkdf;
use sha2::Sha256;
use zeroize::Zeroizing;

use crate::Error;

/// These parameters and the email normalization are part of the account's persistent identity.
pub async fn login_key(email: &str, password: String) -> Result<String, Error> {
    let salt = format!("auto-email-sender:{}", cloud_api::normalize_email(email));
    let password = Zeroizing::new(password);
    tokio::task::spawn_blocking(move || {
        let params = Params::new(65_536, 3, 4, Some(32)).map_err(|_| Error::Key)?;
        let mut master = Zeroizing::new([0; 32]);
        Argon2::new(Algorithm::Argon2id, Version::V0x13, params)
            .hash_password_into(password.as_bytes(), salt.as_bytes(), master.as_mut())
            .map_err(|_| Error::Key)?;
        let mut login = Zeroizing::new([0; 32]);
        Hkdf::<Sha256>::new(None, master.as_ref())
            .expand(b"login", login.as_mut())
            .map_err(|_| Error::Key)?;
        Ok(BASE64_URL_SAFE_NO_PAD.encode(login.as_ref()))
    })
    .await
    .map_err(Error::Worker)?
}
