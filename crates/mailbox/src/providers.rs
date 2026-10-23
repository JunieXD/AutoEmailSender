//! The provider table: what differs between mail providers lives here, as data
//! (docs/design.md#邮箱). Server settings follow Thunderbird's public ISPDB where it has the provider.

use std::borrow::Cow;

use serde::Serialize;
use ts_rs::TS;

use crate::{Security, Server};

/// The provider id of a mailbox whose servers the user filled in.
pub const CUSTOM: &str = "custom";

#[derive(Debug, Serialize, TS)]
pub struct Provider {
    pub id: &'static str,
    pub name: &'static str,
    /// The address domains it serves. Empty for services that host an organisation's own domain,
    /// which the user picks by name.
    pub domains: &'static [&'static str],
    pub setup: Setup,
}

#[derive(Debug, Serialize, TS)]
#[serde(
    tag = "kind",
    rename_all = "camelCase",
    rename_all_fields = "camelCase"
)]
pub enum Setup {
    /// SMTP and IMAP, logging in with a password made for mail apps.
    Password {
        smtp: Server,
        imap: Server,
        /// What the provider calls that password.
        secret: &'static str,
        /// How to turn on SMTP and IMAP and get the password.
        steps: &'static [&'static str],
        /// Where the steps start.
        url: &'static str,
    },
    /// Cannot be added; says why.
    Unsupported { reason: &'static str },
}

pub fn find(id: &str) -> Option<&'static Provider> {
    PROVIDERS.iter().find(|p| p.id == id)
}

const fn tls(host: &'static str, port: u16) -> Server {
    Server {
        host: Cow::Borrowed(host),
        port,
        security: Security::Tls,
    }
}

const NETEASE_STEPS: &[&str] = &[
    "在电脑上登录网页版邮箱，点上方的“设置”，选“POP3/SMTP/IMAP”。",
    "开启“IMAP/SMTP 服务”，按提示发短信验证。",
    "复制页面上显示的授权码（只显示一次），填到这里。",
];

const GENERIC_STEPS: &[&str] = &[
    "在电脑上登录网页版邮箱，在“设置”里找到 POP3/SMTP/IMAP。",
    "开启 IMAP/SMTP 服务，按提示生成授权码。",
    "复制授权码，填到这里。",
];

pub static PROVIDERS: &[Provider] = &[
    Provider {
        id: "qq",
        name: "QQ 邮箱",
        domains: &["qq.com", "foxmail.com", "vip.qq.com"],
        setup: Setup::Password {
            smtp: tls("smtp.qq.com", 465),
            imap: tls("imap.qq.com", 993),
            secret: "授权码",
            steps: &[
                "在电脑上登录 QQ 邮箱网页版，打开“设置”里的“账号与安全”。",
                "在“安全设置”里开启“POP3/IMAP/SMTP/Exchange/CardDAV 服务”，并勾选“SMTP 发信后保存到服务器”，否则发出的信不会出现在“已发送”里。",
                "点“生成授权码”，按提示验证后复制授权码，填到这里。",
            ],
            url: "https://mail.qq.com",
        },
    },
    Provider {
        id: "163",
        name: "163 邮箱",
        domains: &["163.com"],
        setup: Setup::Password {
            smtp: tls("smtp.163.com", 465),
            imap: tls("imap.163.com", 993),
            secret: "授权码",
            steps: NETEASE_STEPS,
            url: "https://mail.163.com",
        },
    },
    Provider {
        id: "126",
        name: "126 邮箱",
        domains: &["126.com"],
        setup: Setup::Password {
            smtp: tls("smtp.126.com", 465),
            imap: tls("imap.126.com", 993),
            secret: "授权码",
            steps: NETEASE_STEPS,
            url: "https://mail.126.com",
        },
    },
    Provider {
        id: "yeah",
        name: "yeah.net 邮箱",
        domains: &["yeah.net"],
        setup: Setup::Password {
            smtp: tls("smtp.yeah.net", 465),
            imap: tls("imap.yeah.net", 993),
            secret: "授权码",
            steps: NETEASE_STEPS,
            url: "https://mail.yeah.net",
        },
    },
    Provider {
        id: "sina",
        name: "新浪邮箱",
        domains: &["sina.com"],
        setup: Setup::Password {
            smtp: tls("smtp.sina.com", 465),
            imap: tls("imap.sina.com", 993),
            secret: "授权码",
            steps: GENERIC_STEPS,
            url: "https://mail.sina.com.cn",
        },
    },
    Provider {
        id: "sohu",
        name: "搜狐邮箱",
        domains: &["sohu.com"],
        setup: Setup::Password {
            smtp: tls("smtp.sohu.com", 465),
            imap: tls("imap.sohu.com", 993),
            secret: "授权码",
            steps: GENERIC_STEPS,
            url: "https://mail.sohu.com",
        },
    },
    Provider {
        id: "139",
        name: "139 邮箱",
        domains: &["139.com"],
        setup: Setup::Password {
            smtp: tls("smtp.139.com", 465),
            imap: tls("imap.139.com", 993),
            secret: "授权码",
            steps: GENERIC_STEPS,
            url: "https://mail.10086.cn",
        },
    },
    Provider {
        id: "gmail",
        name: "Gmail",
        domains: &["gmail.com", "googlemail.com"],
        setup: Setup::Password {
            smtp: tls("smtp.gmail.com", 465),
            imap: tls("imap.gmail.com", 993),
            secret: "应用专用密码",
            steps: &[
                "在 Google 账号的“安全性”里开启两步验证。",
                "打开“应用专用密码”页面，填个名称（比如 Auto Email Sender）后点“创建”。",
                "复制生成的 16 位密码，填到这里。",
            ],
            url: "https://myaccount.google.com/apppasswords",
        },
    },
    Provider {
        id: "icloud",
        name: "iCloud 邮件",
        domains: &["icloud.com", "me.com", "mac.com"],
        setup: Setup::Password {
            smtp: Server {
                host: Cow::Borrowed("smtp.mail.me.com"),
                port: 587,
                security: Security::Starttls,
            },
            imap: tls("imap.mail.me.com", 993),
            secret: "App 专用密码",
            steps: &[
                "登录 Apple 账户网站，进入“登录与安全”。",
                "选“App 专用密码”，生成一个新密码。",
                "复制密码，填到这里。",
            ],
            url: "https://account.apple.com",
        },
    },
    Provider {
        id: "yahoo",
        name: "Yahoo 邮箱",
        domains: &["yahoo.com", "ymail.com", "rocketmail.com"],
        setup: Setup::Password {
            smtp: tls("smtp.mail.yahoo.com", 465),
            imap: tls("imap.mail.yahoo.com", 993),
            secret: "应用密码",
            steps: &[
                "登录 Yahoo 账户，打开“账户安全”。",
                "点“生成应用密码”，填个名称后生成。",
                "复制密码，填到这里。",
            ],
            url: "https://login.yahoo.com/account/security",
        },
    },
    Provider {
        id: "aol",
        name: "AOL 邮箱",
        domains: &["aol.com"],
        setup: Setup::Password {
            smtp: tls("smtp.aol.com", 465),
            imap: tls("imap.aol.com", 993),
            secret: "应用密码",
            steps: &[
                "登录 AOL 账户，打开“账户安全”。",
                "点“生成应用密码”，填个名称后生成。",
                "复制密码，填到这里。",
            ],
            url: "https://login.aol.com/account/security",
        },
    },
    Provider {
        id: "zoho",
        name: "Zoho Mail",
        domains: &["zoho.com", "zohomail.com"],
        setup: Setup::Password {
            smtp: tls("smtp.zoho.com", 465),
            imap: tls("imap.zoho.com", 993),
            secret: "应用专用密码",
            steps: &[
                "在 Zoho Mail 的设置里开启 IMAP 访问。",
                "在 Zoho 账户的安全设置里生成一个应用专用密码。",
                "复制密码，填到这里。",
            ],
            url: "https://accounts.zoho.com",
        },
    },
    Provider {
        id: "exmail",
        name: "腾讯企业邮",
        domains: &[],
        setup: Setup::Password {
            smtp: tls("smtp.exmail.qq.com", 465),
            imap: tls("imap.exmail.qq.com", 993),
            secret: "客户端专用密码",
            steps: &[
                "登录腾讯企业邮网页版，在“设置”里开启 IMAP/SMTP 服务。",
                "开启了安全登录（绑定微信）的，在设置里生成客户端专用密码，填到这里；没开启的填邮箱密码。",
            ],
            url: "https://exmail.qq.com",
        },
    },
    Provider {
        id: "qiye163",
        name: "网易企业邮",
        domains: &[],
        setup: Setup::Password {
            smtp: tls("smtp.qiye.163.com", 465),
            imap: tls("imap.qiye.163.com", 993),
            secret: "客户端授权密码",
            steps: &[
                "登录网易企业邮网页版，在“设置”里开启 IMAP/SMTP 服务。",
                "按提示生成客户端授权密码，填到这里。",
            ],
            url: "https://qiye.163.com",
        },
    },
    Provider {
        id: "aliyun",
        name: "阿里邮箱",
        domains: &[],
        setup: Setup::Password {
            smtp: tls("smtp.qiye.aliyun.com", 465),
            imap: tls("imap.qiye.aliyun.com", 993),
            secret: "密码",
            steps: &[
                "登录阿里邮箱网页版，在“设置”里开启 IMAP/SMTP 服务。",
                "开启了三方客户端安全密码的，生成一个填到这里；没开启的填邮箱密码。",
            ],
            url: "https://qiye.aliyun.com",
        },
    },
    Provider {
        id: "outlook",
        name: "Outlook 邮箱",
        domains: &["outlook.com", "hotmail.com", "live.com", "msn.com"],
        setup: Setup::Unsupported {
            reason: "Outlook 邮箱要用微软账号授权登录，这个功能还在做，暂时不能添加。",
        },
    },
    Provider {
        id: "proton",
        name: "Proton Mail",
        domains: &["proton.me", "protonmail.com", "pm.me"],
        setup: Setup::Unsupported {
            reason: "Proton Mail 要通过电脑上的 Proton Bridge 收发信，这个功能还在做，暂时不能添加。",
        },
    },
    Provider {
        id: "tuta",
        name: "Tuta",
        domains: &[
            "tuta.com",
            "tutanota.com",
            "tutanota.de",
            "tutamail.com",
            "tuta.io",
            "keemail.me",
        ],
        setup: Setup::Unsupported {
            reason: "Tuta 不提供 SMTP 和 IMAP，没法用来发信，请换一个邮箱。",
        },
    },
];
