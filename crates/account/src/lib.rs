//! Official account access. Passwords live only for derivation; saved tokens never reach callers.

mod key;
pub use key::login_key;

use std::time::Duration;

use cloud_api::{Account, ApiError, Empty, ErrorCode, Session, path};
use reqwest::{Method, RequestBuilder};
use serde::{Serialize, de::DeserializeOwned};
use sqlx::SqlitePool;
use tokio::sync::Mutex;
use ts_rs::TS;

/// The production address will be set when the official service is deployed.
const SERVER_URL: &str = "http://127.0.0.1:8787";

#[derive(Debug, Serialize, TS)]
#[serde(
    tag = "kind",
    rename_all = "camelCase",
    rename_all_fields = "camelCase"
)]
#[ts(rename = "AccountState")]
pub enum State {
    LoggedOut,
    LoggedIn {
        email: String,
        sponsor_until: Option<i64>,
    },
    Expired {
        email: String,
    },
}

pub struct Client {
    // Serializing account changes prevents an old response from replacing a newer login.
    change: Mutex<()>,
    address: String,
    http: reqwest::Client,
}

impl Client {
    pub fn new(net: &net::Net) -> Self {
        let address = std::env::var_os("AUTOEMAIL_SERVER_URL")
            .map(|address| address.to_string_lossy().into_owned())
            .unwrap_or_else(|| SERVER_URL.into());
        Self::with_address(net, address)
    }

    pub fn with_address(net: &net::Net, address: String) -> Self {
        // Net already built this TLS configuration; disabling redirects cannot invalidate it.
        let http = net.page_http_builder().build().unwrap_or_else(|_| {
            unreachable!("validated network settings must build an account HTTP client")
        });
        Self {
            address: address.trim_end_matches('/').into(),
            change: Mutex::new(()),
            http,
        }
    }

    fn request(&self, method: Method, path: &str) -> RequestBuilder {
        // A redirect must never forward a login key or verification code to a different service.
        self.http
            .request(method, format!("{}{path}", self.address))
            .timeout(Duration::from_secs(30))
    }

    pub async fn state(&self, db: &SqlitePool) -> Result<State, Error> {
        let _guard = self.change.lock().await;
        let row = sqlx::query!("SELECT email, token FROM account")
            .fetch_optional(db)
            .await?;
        match row {
            None => Ok(State::LoggedOut),
            Some(row) if row.token.is_none() => Ok(State::Expired { email: row.email }),
            Some(row) => match self
                .authenticated::<Account>(db, Method::GET, path::ACCOUNT, None::<&Empty>)
                .await
            {
                Ok(account) => Ok(State::LoggedIn {
                    email: account.email,
                    sponsor_until: account.sponsor_until,
                }),
                Err(Error::SignedOut) => Ok(State::Expired { email: row.email }),
                Err(error) => Err(error),
            },
        }
    }

    pub async fn send_code(&self, email: &str, reset: bool) -> Result<(), Error> {
        let email = checked_email(email)?;
        let path = if reset {
            path::RESET_CODE
        } else {
            path::REGISTER_CODE
        };
        response::<Empty>(
            self.request(Method::POST, path)
                .json(&cloud_api::EmailRequest { email }),
        )
        .await?;
        Ok(())
    }

    pub async fn login(
        &self,
        db: &SqlitePool,
        email: &str,
        password: String,
    ) -> Result<State, Error> {
        let _guard = self.change.lock().await;
        let email = checked_email(email)?;
        let login_key = login_key(&email, password).await?;
        let session: Session = response(
            self.request(Method::POST, path::LOGIN)
                .json(&cloud_api::LoginRequest { email, login_key }),
        )
        .await?;
        save_session(db, session).await
    }

    pub async fn register(
        &self,
        db: &SqlitePool,
        email: &str,
        code: &str,
        password: String,
    ) -> Result<State, Error> {
        let _guard = self.change.lock().await;
        let request = verified(email, code, password).await?;
        let session: Session =
            response(self.request(Method::POST, path::REGISTER).json(&request)).await?;
        save_session(db, session).await
    }

    pub async fn reset_password(
        &self,
        db: &SqlitePool,
        email: &str,
        code: &str,
        password: String,
    ) -> Result<(), Error> {
        let _guard = self.change.lock().await;
        let request = verified(email, code, password).await?;
        response::<Empty>(
            self.request(Method::POST, path::RESET_PASSWORD)
                .json(&request),
        )
        .await?;
        sqlx::query!(
            "UPDATE account SET token = NULL WHERE email = ?",
            request.email
        )
        .execute(db)
        .await?;
        Ok(())
    }

    pub async fn logout(&self, db: &SqlitePool) -> Result<(), Error> {
        let _guard = self.change.lock().await;
        match self
            .authenticated::<Empty>(db, Method::POST, path::LOGOUT, None::<&Empty>)
            .await
        {
            Ok(_) | Err(Error::SignedOut) => {}
            Err(error) => return Err(error),
        }
        sqlx::query!("DELETE FROM account").execute(db).await?;
        Ok(())
    }

    pub async fn delete(&self, db: &SqlitePool, password: String) -> Result<(), Error> {
        let _guard = self.change.lock().await;
        let email = sqlx::query_scalar!("SELECT email FROM account WHERE token IS NOT NULL")
            .fetch_optional(db)
            .await?
            .ok_or(Error::SignedOut)?;
        let login_key = login_key(&email, password).await?;
        self.authenticated::<Empty>(
            db,
            Method::DELETE,
            path::ACCOUNT,
            Some(&cloud_api::DeleteRequest { login_key }),
        )
        .await?;
        sqlx::query!("DELETE FROM account").execute(db).await?;
        Ok(())
    }

    /// All official services use this: an expired session clears only that token, retaining the
    /// email for the next login. A response for an older token cannot clear a newer login.
    pub async fn authenticated<T: DeserializeOwned>(
        &self,
        db: &SqlitePool,
        method: Method,
        path: &str,
        body: Option<&impl Serialize>,
    ) -> Result<T, Error> {
        let token = sqlx::query_scalar!("SELECT token FROM account WHERE token IS NOT NULL")
            .fetch_optional(db)
            .await?
            .flatten()
            .ok_or(Error::SignedOut)?;
        let mut request = self.request(method, path).bearer_auth(&token);
        if let Some(body) = body {
            request = request.json(body);
        }
        match response(request).await {
            Err(Error::Server(ApiError {
                code: ErrorCode::SessionExpired,
                ..
            })) => {
                sqlx::query!("UPDATE account SET token = NULL WHERE token = ?", token)
                    .execute(db)
                    .await?;
                Err(Error::SignedOut)
            }
            result => result,
        }
    }
}

async fn response<T: DeserializeOwned>(request: RequestBuilder) -> Result<T, Error> {
    let response = request.send().await.map_err(Error::Network)?;
    if response.status().is_success() {
        response.json().await.map_err(Error::Network)
    } else {
        Err(Error::Server(
            response.json().await.map_err(Error::Network)?,
        ))
    }
}

fn checked_email(email: &str) -> Result<String, Error> {
    let email = cloud_api::normalize_email(email);
    if email.len() > 254 || !mentor::is_email(&email) {
        return Err(Error::Invalid("请填写正确的邮箱地址。"));
    }
    Ok(email)
}

async fn verified(
    email: &str,
    code: &str,
    password: String,
) -> Result<cloud_api::VerifyRequest, Error> {
    let email = checked_email(email)?;
    if password.chars().count() < 8 {
        return Err(Error::Invalid("密码至少需要 8 位。"));
    }
    let code = code.trim();
    if code.len() != 6 || !code.bytes().all(|c| c.is_ascii_digit()) {
        return Err(Error::Invalid("请填写 6 位数字验证码。"));
    }
    let login_key = login_key(&email, password).await?;
    Ok(cloud_api::VerifyRequest {
        email,
        code: code.into(),
        login_key,
    })
}

async fn save_session(db: &SqlitePool, session: Session) -> Result<State, Error> {
    sqlx::query!(
        r#"INSERT INTO account (id, email, token) VALUES (1, ?, ?)
        ON CONFLICT (id) DO UPDATE SET email = excluded.email, token = excluded.token"#,
        session.account.email,
        session.token
    )
    .execute(db)
    .await?;
    Ok(State::LoggedIn {
        email: session.account.email,
        sponsor_until: session.account.sponsor_until,
    })
}

#[derive(Debug)]
pub enum Error {
    Invalid(&'static str),
    Worker(tokio::task::JoinError),
    Database(sqlx::Error),
    Network(reqwest::Error),
    Server(ApiError),
    SignedOut,
}

impl From<sqlx::Error> for Error {
    fn from(error: sqlx::Error) -> Self {
        Self::Database(error)
    }
}

impl std::fmt::Display for Error {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::Database(error) => write!(f, "account database: {error}"),
            Self::Worker(error) => write!(f, "account derivation worker: {error}"),
            Self::Network(error) => write!(f, "account network: {error}"),
            Self::Server(error) => write!(f, "account server: {:?}", error.code),
            Self::Invalid(_) => write!(f, "invalid account input"),
            Self::SignedOut => write!(f, "account session expired"),
        }
    }
}
impl std::error::Error for Error {}

impl Error {
    pub fn message(&self) -> String {
        match self {
            Self::Invalid(message) => (*message).into(),
            Self::Worker(_) | Self::Database(_) => {
                "账号处理未能完成，请重试；仍然失败时导出日志联系开发者。".into()
            }
            Self::Network(error) if error.is_decode() => {
                "服务器返回的数据无法读取，请稍后重试或升级应用。".into()
            }
            Self::Network(_) => "连不上服务器，请检查网络和代理设置后重试。".into(),
            Self::SignedOut => "登录已失效，请重新登录。".into(),
            Self::Server(error) => match error.code {
                ErrorCode::InvalidInput => "填写的账号信息不正确，请检查后重试。".into(),
                ErrorCode::EmailRegistered => "这个邮箱已注册，请直接登录。".into(),
                ErrorCode::EmailNotRegistered => "这个邮箱还没注册，请先注册。".into(),
                ErrorCode::InvalidCode => "验证码不对或已过期，请重新获取验证码。".into(),
                ErrorCode::InvalidCredentials => "邮箱或密码不对，请检查后重试。".into(),
                ErrorCode::RateLimited => match error.retry_after_seconds {
                    Some(seconds) => {
                        format!("请求太频繁，请 {} 分钟后再试。", seconds.div_ceil(60))
                    }
                    None => "请求太频繁，请稍后再试。".into(),
                },
                ErrorCode::SessionExpired => "登录已失效，请重新登录。".into(),
                ErrorCode::MailUnavailable => "验证码暂时无法发送，请稍后重试。".into(),
                ErrorCode::Internal => "服务器暂时无法处理，请稍后重试。".into(),
                ErrorCode::NotFound | ErrorCode::MethodNotAllowed => {
                    "服务器不支持这个操作，请升级应用后重试。".into()
                }
            },
        }
    }
}
