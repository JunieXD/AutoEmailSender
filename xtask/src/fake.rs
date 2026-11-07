//! `cargo xtask fake`: a mailbox and a model on this computer, so that the development build can
//! be tried from first start to replies without real accounts (docs/development.md#假邮箱和假模型).

use std::collections::HashMap;
use std::path::PathBuf;
use std::time::Duration;

use axum::Json;
use axum::Router;
use axum::http::{StatusCode, header};
use axum::response::{IntoResponse, Response};
use axum::routing::{get, post};
use mail_parser::{Message, MessageParser};
use serde_json::{Value, json};
use tokio::net::TcpListener;

const SMTP_PORT: u16 = 1465;
const IMAP_PORT: u16 = 1993;
const MODEL_PORT: u16 = 1480;
const INBOX: &str = "INBOX";
const SENT: &str = "Sent Messages";
/// How long a mentor takes to answer, short enough to watch.
const ANSWER_AFTER: Duration = Duration::from_secs(20);

pub async fn run() -> crate::Result {
    let dir = crate::root().join(".dev-data").join("fake-mail");
    let pki = fakemail::lasting_pki(&dir)?;
    let (_, accepted) = fakemail::smtp_on(&pki, false, SMTP_PORT).await;
    let (_, store) = fakemail::imap_on(&pki, false, IMAP_PORT).await;
    store.create(SENT, true);
    let mailbox = Mailbox {
        store,
        dir: dir.join("mail"),
    };
    let mut mentors = Mentors::default();
    // Who plays which mentor follows from the letters already sent, so that it outlasts a restart.
    for raw in mailbox.load()? {
        let Some(message) = MessageParser::new().parse(raw.as_bytes()) else {
            continue;
        };
        for (address, _) in recipients(&message) {
            if address != fakemail::ADDRESS {
                mentors.letter(&address);
            }
        }
    }
    let router = Router::new()
        .route("/v1/models", get(models))
        .route("/v1/chat/completions", post(chat));
    let listener = TcpListener::bind(("127.0.0.1", MODEL_PORT)).await?;
    tokio::spawn(axum::serve(listener, router).into_future());
    println!(
        "假邮箱和假模型已启动，按 Ctrl+C 停止。先启动它再启动开发版，开发版才认得假邮箱的证书。\n\
        \n邮箱：{address}，授权码 {secret}\n  \
        发信服务器 localhost:{SMTP_PORT}（SSL），收信服务器 localhost:{IMAP_PORT}（SSL）\n\
        模型：自定义的 OpenAI 兼容接口 http://127.0.0.1:{MODEL_PORT}/v1，API Key 随便填，模型 fake\n\
        \n每位新的收件人按顺序扮演一种导师：{kinds}。\n\
        收件人叫 unknown 的发信服务器直接拒收。",
        address = fakemail::ADDRESS,
        secret = fakemail::SECRET,
        kinds = MENTORS.map(|m| m.describe()).join("、"),
    );
    loop {
        tokio::select! {
            result = tokio::signal::ctrl_c() => return Ok(result?),
            () = tokio::time::sleep(Duration::from_millis(500)) => {}
        }
        let taken = std::mem::take(&mut *accepted.lock().map_err(|e| e.to_string())?);
        for data in taken {
            let raw = unstuffed(&data);
            mailbox.deliver(SENT, &raw)?;
            let Some(message) = MessageParser::new().parse(raw.as_bytes()) else {
                continue;
            };
            for (address, name) in recipients(&message) {
                if address == fakemail::ADDRESS {
                    mailbox.deliver(INBOX, &raw)?;
                    continue;
                }
                let (mentor, letters) = mentors.letter(&address);
                let name = name.unwrap_or_else(|| address.clone());
                println!(
                    "收到给 {name} 的第 {letters} 封信，这位导师{}",
                    mentor.describe()
                );
                if let Some(answer) = mentor.answer(letters, &raw, &message, &address, &name) {
                    let mailbox = mailbox.clone();
                    tokio::spawn(async move {
                        tokio::time::sleep(ANSWER_AFTER).await;
                        if let Err(e) = mailbox.deliver(INBOX, &answer) {
                            eprintln!("回信放不进收件箱：{e}");
                        }
                    });
                }
            }
        }
    }
}

/// The letter's recipients, with their addresses in lower case.
fn recipients(letter: &Message) -> Vec<(String, Option<String>)> {
    letter
        .to()
        .iter()
        .flat_map(|list| list.mailboxes())
        .filter_map(|m| Some((m.address()?.to_lowercase(), m.name().map(str::to_owned))))
        .collect()
}

/// What the SMTP server took after DATA, as the message itself: without the ending dot line,
/// without the dots doubled at the start of lines, and with plain line breaks.
fn unstuffed(data: &str) -> String {
    data.strip_suffix(".\r\n")
        .unwrap_or(data)
        .split_inclusive("\r\n")
        .map(|line| line.strip_prefix('.').unwrap_or(line).replace("\r\n", "\n"))
        .collect()
}

/// The fake mailbox's folders, also kept as files so that the mail and its UIDs outlast a restart,
/// as on a real server.
#[derive(Clone)]
struct Mailbox {
    store: fakemail::Store,
    dir: PathBuf,
}

impl Mailbox {
    fn folder_dir(&self, folder: &str) -> PathBuf {
        self.dir
            .join(if folder == INBOX { "inbox" } else { "sent" })
    }

    /// Puts the saved mail back into the folders, and returns the letters sent in the order they
    /// were.
    fn load(&self) -> std::io::Result<Vec<String>> {
        let mut sent = Vec::new();
        for folder in [INBOX, SENT] {
            let dir = self.folder_dir(folder);
            if !dir.exists() {
                continue;
            }
            let mut files = Vec::new();
            for entry in std::fs::read_dir(&dir)? {
                let path = entry?.path();
                // Finder and the like leave files of their own here.
                if path.extension().is_some_and(|e| e == "eml") {
                    files.push(path);
                }
            }
            // Named by order of arrival, so that the UIDs come out the same.
            files.sort();
            for path in files {
                let at = path
                    .file_stem()
                    .and_then(|stem| stem.to_str()?.split_once('-')?.1.parse().ok())
                    .ok_or_else(|| {
                        std::io::Error::other(format!("{} 不是假邮箱存的信", path.display()))
                    })?;
                let raw = std::fs::read_to_string(&path)?;
                self.store.deliver(folder, at, &raw);
                if folder == SENT {
                    sent.push(raw);
                }
            }
        }
        Ok(sent)
    }

    fn deliver(&self, folder: &str, raw: &str) -> std::io::Result<()> {
        let at = chrono::Utc::now().timestamp_millis();
        let uid = self.store.deliver(folder, at, raw);
        let dir = self.folder_dir(folder);
        std::fs::create_dir_all(&dir)?;
        std::fs::write(dir.join(format!("{uid:010}-{at}.eml")), raw)
    }
}

/// How a mentor answers the student's letters.
#[derive(Clone, Copy)]
enum Mentor {
    Interested,
    LateNoSlot,
    Declines,
    Away,
    Gone,
    Forwards,
    Silent,
}

const MENTORS: [Mentor; 7] = [
    Mentor::Interested,
    Mentor::LateNoSlot,
    Mentor::Declines,
    Mentor::Away,
    Mentor::Gone,
    Mentor::Forwards,
    Mentor::Silent,
];

/// The kind of mentor each recipient plays, handed out in order of their first letter, and how
/// many letters they had.
#[derive(Default)]
struct Mentors(HashMap<String, (Mentor, u32)>);

impl Mentors {
    fn letter(&mut self, address: &str) -> (Mentor, u32) {
        let next = MENTORS[self.0.len() % MENTORS.len()];
        let (mentor, letters) = self.0.entry(address.to_owned()).or_insert((next, 0));
        *letters += 1;
        (*mentor, *letters)
    }
}

impl Mentor {
    fn describe(self) -> &'static str {
        match self {
            Self::Interested => "有意向，要成绩单并约电话",
            Self::LateNoSlot => "第一封不回，追问后说没名额",
            Self::Declines => "婉拒",
            Self::Away => "自动回复出差",
            Self::Gone => "地址已失效，退信",
            Self::Forwards => "说已转给别的老师",
            Self::Silent => "一直不回",
        }
    }

    /// The answer to the student's `nth` letter, if any.
    fn answer(
        self,
        nth: u32,
        raw: &str,
        letter: &Message,
        address: &str,
        name: &str,
    ) -> Option<String> {
        let say = |text: &str| Some(reply(letter, address, name, "Re: ", text, ""));
        match (self, nth) {
            (Self::Interested, 1) => say(
                "你好，邮件和简历都看了，欢迎报考。请在一周内把本科成绩单发给我，\
                我们再约个时间电话聊聊。",
            ),
            (Self::Interested, _) => {
                say("收到，谢谢。我们周三下午三点电话聊，方便的话回复确认一下。")
            }
            (Self::LateNoSlot, 2) => say("抱歉回复晚了。今年的名额已经招满了，祝你顺利。"),
            (Self::Declines, 1) => {
                say("谢谢你的来信。我组目前的方向和你的背景不太一致，建议你联系其他老师。")
            }
            (Self::Away, 1) => Some(reply(
                letter,
                address,
                name,
                "自动回复：",
                "我正在外地开会，下个月回来后再回复邮件。",
                "Auto-Submitted: auto-replied\n",
            )),
            (Self::Gone, _) => Some(bounce(raw, letter, address)),
            (Self::Forwards, 1) => say("邮件收到了，我已经转给课题组的张老师，请等他联系你。"),
            _ => None,
        }
    }
}

/// The answer's subject: mail clients do not add another "Re: " to one that has it.
fn subject(letter: &Message, prefix: &str) -> String {
    let subject = letter.subject().unwrap_or_default();
    if subject.starts_with(prefix) {
        subject.to_owned()
    } else {
        format!("{prefix}{subject}")
    }
}

fn date() -> String {
    chrono::Utc::now().to_rfc2822()
}

fn message_id() -> String {
    format!("<{}@example.edu.cn>", uuid::Uuid::new_v4())
}

fn bracketed(id: &str) -> String {
    format!("<{}>", id.trim_matches(['<', '>']))
}

/// A plain-text answer to `letter` that quotes it, as mail clients write.
fn reply(
    letter: &Message,
    address: &str,
    name: &str,
    prefix: &str,
    text: &str,
    extra_headers: &str,
) -> String {
    let parent = letter.message_id().map(bracketed).unwrap_or_default();
    let mut references: Vec<String> = letter
        .references()
        .iter()
        .flat_map(|list| list.iter())
        .map(bracketed)
        .collect();
    references.push(parent.clone());
    let quoted: String = letter
        .body_text(0)
        .unwrap_or_default()
        .lines()
        .map(|line| format!("> {line}\n"))
        .collect();
    format!(
        "From: {name} <{address}>\n\
        To: <{student}>\n\
        Subject: {subject}\n\
        Date: {date}\n\
        Message-ID: {id}\n\
        In-Reply-To: {parent}\n\
        References: {references}\n\
        {extra_headers}\
        MIME-Version: 1.0\n\
        Content-Type: text/plain; charset=utf-8\n\
        Content-Transfer-Encoding: 8bit\n\
        \n\
        {text}\n\
        \n\
        {name}\n\
        \n\
        {student} 写道：\n\
        {quoted}",
        student = fakemail::ADDRESS,
        subject = subject(letter, prefix),
        date = date(),
        id = message_id(),
        references = references.join(" "),
    )
}

/// A delivery status report saying the address no longer exists, with the letter's headers.
fn bounce(raw: &str, letter: &Message, address: &str) -> String {
    let headers = raw.split_once("\n\n").map_or(raw, |(head, _)| head);
    format!(
        "From: Mail Delivery System <MAILER-DAEMON@example.edu.cn>\n\
        To: <{student}>\n\
        Subject: 退信：{subject}\n\
        Date: {date}\n\
        Message-ID: {id}\n\
        MIME-Version: 1.0\n\
        Content-Type: multipart/report; report-type=delivery-status; boundary=\"fake-bounce\"\n\
        \n\
        --fake-bounce\n\
        Content-Type: text/plain; charset=utf-8\n\
        Content-Transfer-Encoding: 8bit\n\
        \n\
        你发给 {address} 的邮件没有送达：收件人不存在。\n\
        \n\
        --fake-bounce\n\
        Content-Type: message/delivery-status\n\
        \n\
        Reporting-MTA: dns; mx.example.edu.cn\n\
        \n\
        Final-Recipient: rfc822; {address}\n\
        Action: failed\n\
        Status: 5.1.1\n\
        Diagnostic-Code: smtp; 550 5.1.1 User unknown\n\
        \n\
        --fake-bounce\n\
        Content-Type: text/rfc822-headers\n\
        \n\
        {headers}\n\
        --fake-bounce--\n",
        student = fakemail::ADDRESS,
        subject = letter.subject().unwrap_or_default(),
        date = date(),
        id = message_id(),
    )
}

async fn models() -> Json<Value> {
    Json(json!({"object": "list", "data": [{"id": "fake", "object": "model"}]}))
}

/// Answers as an OpenAI-compatible model streaming its whole answer in one piece.
async fn chat(Json(request): Json<Value>) -> Response {
    let said = |role: &str| {
        request["messages"]
            .as_array()
            .into_iter()
            .flatten()
            .find(|m| m["role"] == role)
            .and_then(|m| m["content"].as_str())
    };
    let (system, user) = (said("system"), said("user").unwrap_or_default());
    let text = match answer(system, user) {
        Ok(text) => text,
        Err(message) => {
            let error = json!({"error": {"message": message, "type": "invalid_request_error"}});
            return (StatusCode::BAD_REQUEST, Json(error)).into_response();
        }
    };
    // Long enough to see the work going on.
    tokio::time::sleep(Duration::from_secs(1)).await;
    let asked = system.unwrap_or_default().chars().count() + user.chars().count();
    let events = [
        json!({"choices": [{"index": 0, "delta": {"content": text}, "finish_reason": null}]}),
        json!({"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]}),
        json!({"choices": [], "usage": {
            "prompt_tokens": asked.div_ceil(2),
            "completion_tokens": text.chars().count().div_ceil(2),
        }}),
    ];
    let body: String = events
        .iter()
        .map(|event| format!("data: {event}\n\n"))
        .chain(["data: [DONE]\n\n".to_owned()])
        .collect();
    ([(header::CONTENT_TYPE, "text/event-stream")], body).into_response()
}

/// A fixed answer for each kind of request the app makes, told apart by how its instructions
/// start. Reading web pages takes a real model.
fn answer(system: Option<&str>, user: &str) -> Result<String, &'static str> {
    // The connection test sends no instructions.
    let Some(system) = system else {
        return Ok("好".into());
    };
    let input: Value = serde_json::from_str(user).unwrap_or_default();
    let text = |value: &Value| value.as_str().unwrap_or_default().to_owned();
    let student = text(&input["个人资料"]["姓名"]);
    let mentor = text(&input["导师"]["姓名"]);
    let signature = if student.is_empty() {
        String::new()
    } else {
        format!("\n\n{student}")
    };
    if system.starts_with("你帮一位学生给导师写第一封联系邮件") {
        let current = &input["现在的信"];
        if current.is_object() {
            return Ok(format!(
                "<subject>{}</subject>\n<body>\n{}\n\n（假模型改写过这封信。）\n</body>",
                text(&current["主题"]),
                text(&current["正文"]).trim(),
            ));
        }
        let research = text(&input["导师"]["研究方向"]);
        let interest = if research.is_empty() {
            "您的研究工作".to_owned()
        } else {
            format!("您在{research}方面的工作")
        };
        let attached = if input["附件"].as_array().is_some_and(|a| !a.is_empty()) {
            "随信附上我的简历，期待您的回复。"
        } else {
            "期待您的回复。"
        };
        return Ok(format!(
            "<subject>报考咨询：希望加入您的课题组</subject>\n<body>\n{mentor}老师：\n\n\
            您好！这是假模型写的示例信。我读了{interest}的介绍，很希望能加入您的课题组读研。\n\n\
            {attached}\n\n祝好！{signature}\n</body>"
        ));
    }
    if system.starts_with("你正在帮学生写追问邮件") {
        return Ok(format!(
            "<body>\n{mentor}老师：\n\n您好！前几天给您发过一封邮件，想冒昧问一下您是否方便看看。\
            这是假模型写的追问。\n\n祝好！{signature}\n</body>"
        ));
    }
    if system.starts_with("你正在帮学生回复导师") {
        return Ok(
            "<body>\n老师您好：\n\n谢谢您的回复！【请补充：是否接受老师的安排】\
            这是假模型写的回复。\n\n祝好！\n</body>"
                .into(),
        );
    }
    if system.starts_with("你帮助学生判断与导师的研究方向") {
        let wanted = text(&input["学生"]["意向研究方向"]);
        let research = text(&input["导师"]["研究方向"]);
        let shared = research
            .chars()
            .filter(|c| !c.is_ascii() && wanted.contains(*c))
            .collect::<std::collections::BTreeSet<_>>()
            .len();
        let score = (35 + 5 * shared).min(95);
        return Ok(json!({
            "score": score,
            "reason": format!("假模型的打分：研究方向里有 {shared} 个字和你的意向相同。"),
            "directions": [],
            "highlights": [],
            "risks": ["这是假模型的回答，不代表真实的匹配程度"],
        })
        .to_string());
    }
    if system.starts_with("核对学生写给导师的邮件") {
        return Ok("[]".into());
    }
    let reply = text(&input["reply"]["text"]);
    if system.starts_with("你帮助学生标出导师回信的结果") {
        let outcome = if reply.contains("名额") {
            "no_slot"
        } else if reply.contains("不太一致") {
            "declined"
        } else if reply.contains("欢迎") || reply.contains("电话") {
            "interested"
        } else {
            "unclear"
        };
        return Ok(json!({ "outcome": outcome }).to_string());
    }
    if system.starts_with("你帮助学生从导师回信中提取待办") {
        let due = chrono::NaiveDate::parse_from_str(&text(&input["reply_date"]), "%Y-%m-%d")
            .ok()
            .map(|date| (date + chrono::Days::new(7)).to_string());
        let todos = if reply.contains("成绩单") {
            json!([{"text": "把本科成绩单发给老师", "dueDate": due, "dueTime": null}])
        } else if reply.contains("回复确认") {
            json!([{"text": "回复确认周三下午三点的电话", "dueDate": null, "dueTime": null}])
        } else {
            json!([])
        };
        return Ok(json!({ "todos": todos }).to_string());
    }
    Err("假模型只会写信、匹配、核对、标回信结果和提取待办；整理网页要用真实的模型")
}
