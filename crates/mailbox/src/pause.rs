//! Classifies server failures shared by sending and delivery reports.

use chrono::TimeZone;
use serde::Serialize;
use sqlx::SqliteConnection;
use ts_rs::TS;

use crate::MailError;

const MINUTE: i64 = 60_000;

#[derive(Debug, PartialEq, Eq)]
pub enum Failure {
    Pause(PauseKind),
    Email,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum PauseKind {
    Throttled(Throttle),
    LoginRejected,
    Connection,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Throttle {
    Hour,
    Daily,
}

/// An active pause as shown to the user; no server credentials are included.
#[derive(Debug, Serialize, TS)]
pub struct Pause {
    pub until: i64,
    pub reason: String,
}

/// Only for failures known to have happened before handover, or an explicit server rejection.
pub fn classify(error: &MailError) -> Failure {
    match error {
        MailError::Login(reply) | MailError::Refused(reply) => {
            if let Some(throttle) = throttle(reply) {
                Failure::Pause(PauseKind::Throttled(throttle))
            } else if matches!(error, MailError::Login(_)) {
                Failure::Pause(PauseKind::LoginRejected)
            } else {
                Failure::Email
            }
        }
        MailError::Recipient(_) => Failure::Email,
        MailError::Resolve { .. }
        | MailError::Connect { .. }
        | MailError::ProxyDown { .. }
        | MailError::ProxyFailed { .. }
        | MailError::TimedOut { .. }
        | MailError::Tls { .. }
        | MailError::Broken(_) => Failure::Pause(PauseKind::Connection),
    }
}

/// Uses the complete server reply, before any shortening for display.
pub fn throttle(reply: &str) -> Option<Throttle> {
    let text = reply.to_lowercase();
    let unavailable = text
        .split(|c: char| !c.is_ascii_alphanumeric())
        .any(|word| word == "421");
    if !unavailable && !THROTTLE_MARKERS.iter().any(|marker| text.contains(marker)) {
        return None;
    }
    Some(
        if ["daily", "per day", "今日", "当日", "每天", "每日"]
            .iter()
            .any(|marker| text.contains(marker))
        {
            Throttle::Daily
        } else {
            Throttle::Hour
        },
    )
}

impl PauseKind {
    pub fn at(self, now: i64) -> Result<Pause, sqlx::Error> {
        self.in_zone(now, &chrono::Local)
    }

    pub fn in_zone<T: TimeZone>(self, now: i64, zone: &T) -> Result<Pause, sqlx::Error> {
        let until = match self {
            Self::Throttled(Throttle::Daily) => {
                let date = zone
                    .timestamp_millis_opt(now)
                    .single()
                    .ok_or_else(|| sqlx::Error::Decode("invalid current time".into()))?;
                date.date_naive()
                    .succ_opt()
                    .and_then(|d| d.and_hms_opt(0, 0, 0))
                    .and_then(|d| zone.from_local_datetime(&d).earliest())
                    .ok_or_else(|| sqlx::Error::Decode("invalid next local day".into()))?
                    .timestamp_millis()
            }
            Self::Throttled(Throttle::Hour) | Self::LoginRejected => now + 60 * MINUTE,
            Self::Connection => now + MINUTE,
        };
        let reason = match self {
            Self::Throttled(Throttle::Daily) => {
                "邮箱服务商的每日发送额度用完了。可以减少每天的发信数量。"
            }
            Self::Throttled(Throttle::Hour) => "被邮箱服务商限流了。可以降低发信频率。",
            Self::LoginRejected => {
                "登录被拒绝。检查授权码是否正确、SMTP 服务是否开启，学校和公司的邮箱可以问管理员；改好邮箱设置后马上恢复。"
            }
            Self::Connection => {
                "连不上邮箱服务器。检查网络和代理，以及服务器地址、端口和加密方式。"
            }
        };
        Ok(Pause {
            until,
            reason: reason.into(),
        })
    }
}

/// In the caller's transaction, so the pause and its email outcome take effect together.
pub async fn apply(conn: &mut SqliteConnection, id: i64, pause: &Pause) -> Result<(), sqlx::Error> {
    sqlx::query!(
        "UPDATE mailbox SET paused_until = ?1, pause_reason = ?2
         WHERE id = ?3 AND (paused_until IS NULL OR paused_until < ?1)",
        pause.until,
        pause.reason,
        id,
    )
    .execute(conn)
    .await?;
    Ok(())
}

const THROTTLE_MARKERS: &[&str] = &[
    "flow over limit",
    "flow control",
    "rate limit",
    "rate-limit",
    "rate exceeded",
    "frequency limited",
    "frequency limit",
    "sending limit",
    "send limit",
    "sending quota",
    "send quota",
    "too many requests",
    "too many connections",
    "too many messages",
    "too many login attempts",
    "too many authentication attempts",
    "temporarily throttled",
    "throttling",
    "hl:rep",
    "hl:icc",
    "hl:ifc",
    "hl:mep",
    "mi:cel",
    "mi:dmc",
    "mi:ccl",
    "rp:drc",
    "dt:stc",
    "dt:stf",
    "发送频率",
    "发信频率",
    "发送过快",
    "请求过于频繁",
    "操作过于频繁",
    "连接数过多",
    "超过频率限制",
    "流量超限",
    "发送额度",
    "发信额度",
];

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn classifies_provider_replies_without_confusing_message_rejections() {
        for (reply, expected) in [
            ("550 Connection frequency limited", Some(Throttle::Hour)),
            (
                "451 4.7.0 Temporary server error. Please try again later. PRX4",
                None,
            ),
            (
                "451 4.7.0 Message rate limit exceeded",
                Some(Throttle::Hour),
            ),
            (
                "550 5.4.5 Daily user sending quota exceeded",
                Some(Throttle::Daily),
            ),
            ("550 5.4.6 Sending rate exceeded", Some(Throttle::Hour)),
            ("554 HL:REP", Some(Throttle::Hour)),
            ("550 HL:ICC", Some(Throttle::Hour)),
            ("450 HL:IFC", Some(Throttle::Hour)),
            ("554 HL:MEP", Some(Throttle::Hour)),
            ("554 MI:CEL", Some(Throttle::Hour)),
            ("550 MI:DMC", Some(Throttle::Hour)),
            ("450 MI:CCL", Some(Throttle::Hour)),
            ("550 RP:DRC", Some(Throttle::Hour)),
            ("554 DT:STC", Some(Throttle::Hour)),
            ("554 DT:STF", Some(Throttle::Hour)),
            ("421 Service not available", Some(Throttle::Hour)),
            ("535 too many login attempts", Some(Throttle::Hour)),
            ("550 sending limit per day exceeded", Some(Throttle::Daily)),
            ("554 今日发送额度已用完", Some(Throttle::Daily)),
            ("554 当日发信额度已用完", Some(Throttle::Daily)),
            ("554 超过每天发送额度", Some(Throttle::Daily)),
            ("554 DT:SPM spam message rejected", None),
            ("550 5.1.1 user unknown", None),
            ("552 5.2.2 recipient mailbox quota exceeded", None),
            ("550 5.5.3 too many recipients", None),
            ("550 reference 1421 or x421", None),
        ] {
            assert_eq!(throttle(reply), expected, "{reply}");
            let expected =
                expected.map_or(Failure::Email, |t| Failure::Pause(PauseKind::Throttled(t)));
            assert_eq!(
                classify(&MailError::Refused(reply.into())),
                expected,
                "{reply}"
            );
        }
        assert_eq!(
            classify(&MailError::Login("535 invalid credentials".into())),
            Failure::Pause(PauseKind::LoginRejected)
        );
        assert_eq!(
            classify(&MailError::Login("535 too many login attempts".into())),
            Failure::Pause(PauseKind::Throttled(Throttle::Hour))
        );
        assert_eq!(
            classify(&MailError::Recipient("bad".into())),
            Failure::Email
        );
        assert_eq!(
            classify(&MailError::Broken("connection reset".into())),
            Failure::Pause(PauseKind::Connection)
        );
    }

    #[test]
    fn daily_limit_ends_at_the_next_local_midnight() {
        let zone = chrono::FixedOffset::east_opt(8 * 3600).unwrap();
        let now = zone
            .with_ymd_and_hms(2026, 10, 26, 23, 59, 0)
            .unwrap()
            .timestamp_millis();
        let daily = PauseKind::Throttled(Throttle::Daily)
            .in_zone(now, &zone)
            .unwrap();
        assert_eq!(daily.until, now + MINUTE);
        assert_eq!(
            PauseKind::Throttled(Throttle::Hour)
                .in_zone(now, &zone)
                .unwrap()
                .until,
            now + 60 * MINUTE
        );
        assert_eq!(
            PauseKind::Connection.in_zone(now, &zone).unwrap().until,
            now + MINUTE
        );
        let midnight = PauseKind::Throttled(Throttle::Daily)
            .in_zone(daily.until, &zone)
            .unwrap();
        assert_eq!(midnight.until, daily.until + 24 * 60 * MINUTE);
    }
}
