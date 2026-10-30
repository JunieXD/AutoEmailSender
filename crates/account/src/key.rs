use argon2::{Algorithm, Argon2, Params, Version};
use base64::{Engine, prelude::BASE64_URL_SAFE_NO_PAD};
use hkdf::Hkdf;
use sha2::Sha256;
use zeroize::Zeroizing;

use crate::Error;

/// The login key goes to the server; the backup key never leaves this computer.
pub struct Keys {
    pub login: String,
    pub backup: [u8; 32],
}

/// These parameters and the email normalization are part of the account's persistent identity.
pub async fn keys(email: &str, password: String) -> Result<Keys, Error> {
    let salt = format!("auto-email-sender:{}", cloud_api::normalize_email(email));
    let password = Zeroizing::new(password);
    tokio::task::spawn_blocking(move || {
        let params = Params::new(65_536, 3, 4, Some(32))
            .unwrap_or_else(|_| unreachable!("the fixed Argon2 parameters are valid"));
        let mut master = Zeroizing::new([0; 32]);
        Argon2::new(Algorithm::Argon2id, Version::V0x13, params)
            .hash_password_into(password.as_bytes(), salt.as_bytes(), master.as_mut())
            .unwrap_or_else(|_| unreachable!("the salt and output length satisfy Argon2"));
        let hkdf = Hkdf::<Sha256>::new(None, master.as_ref());
        let expand = |info: &[u8]| {
            let mut key = [0; 32];
            hkdf.expand(info, &mut key)
                .unwrap_or_else(|_| unreachable!("32 bytes fit within HKDF-SHA256's output limit"));
            key
        };
        let login = Zeroizing::new(expand(b"login"));
        Keys {
            login: BASE64_URL_SAFE_NO_PAD.encode(login.as_ref()),
            backup: expand(b"backup"),
        }
    })
    .await
    .map_err(Error::Worker)
}
