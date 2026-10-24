//! Generated mentors and mail, for the development app and the benchmark. Fixed seed, so every run
//! produces the same data.

use std::collections::HashSet;
use std::time::{SystemTime, UNIX_EPOCH};

use fastrand::Rng;
use mentor::{Affiliation, MentorFields};
use sqlx::SqlitePool;

use crate::Result;

pub const DAY: i64 = 86_400_000;

pub struct Scale {
    pub mentors: usize,
    pub emails: usize,
    pub messages: usize,
}

pub fn now() -> i64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map_or(0, |d| d.as_millis() as i64)
}

const SURNAMES: &str = "王李张刘陈杨黄赵吴周徐孙马朱胡郭何高林罗郑梁谢宋唐许韩冯邓曹彭曾肖田董袁潘于蒋蔡余杜叶程苏魏吕丁任沈姚卢姜崔钟谭陆汪范金石廖贾夏韦付方白邹孟熊秦邱江尹薛闫段雷侯龙史黎贺顾毛郝龚邵万钱";
const GIVEN: &str = "伟芳娜敏静丽强磊军洋勇艳杰娟涛明超秀霞平刚桂英华玉萍红梅鹏飞斌宇浩凯健俊帆帅旭宁婷雪琳晶欣颖慧佳倩";
const UNIVERSITIES: &[&str] = &[
    "清华大学",
    "北京大学",
    "浙江大学",
    "上海交通大学",
    "复旦大学",
    "南京大学",
    "中国科学技术大学",
    "哈尔滨工业大学",
    "西安交通大学",
    "华中科技大学",
    "武汉大学",
    "中山大学",
    "北京航空航天大学",
    "同济大学",
    "东南大学",
    "天津大学",
    "南开大学",
    "厦门大学",
    "四川大学",
    "电子科技大学",
    "中国科学院自动化研究所",
    "西湖大学",
    "南方科技大学",
    "山东大学",
    "吉林大学",
];
const SCHOOLS: &[&str] = &[
    "计算机科学与技术学院",
    "软件学院",
    "人工智能学院",
    "电子信息与电气工程学院",
    "信息科学技术学院",
    "自动化学院",
    "网络空间安全学院",
    "数学科学学院",
    "智能与计算学部",
];
const DEPARTMENTS: &[&str] = &[
    "计算机系",
    "软件工程系",
    "人工智能系",
    "数据科学系",
    "网络工程系",
];
const TOPICS: &[&str] = &[
    "机器学习",
    "深度学习",
    "计算机视觉",
    "自然语言处理",
    "强化学习",
    "图神经网络",
    "数据挖掘",
    "推荐系统",
    "分布式系统",
    "操作系统",
    "编译器",
    "程序分析",
    "软件工程",
    "网络安全",
    "密码学",
    "数据库",
    "知识图谱",
    "多模态学习",
    "大语言模型",
    "智能体",
    "机器人",
    "三维重建",
    "神经渲染",
    "医学影像分析",
    "联邦学习",
    "因果推断",
    "可解释机器学习",
    "视频理解",
    "语音识别",
    "量子计算",
];
const TITLES: &[Option<&str>] = &[
    Some("教授"),
    Some("副教授"),
    Some("讲师"),
    Some("研究员"),
    Some("副研究员"),
    Some("助理教授"),
    None,
];
const TAGS: &[(&str, &str)] = &[
    ("梦导", "#991b1b"),
    ("夏令营", "#3f6aa8"),
    ("直博", "#2c7a4f"),
    ("保底", "#985a0c"),
];

fn pick<'a, T>(rng: &mut Rng, items: &'a [T]) -> &'a T {
    &items[rng.usize(..items.len())]
}

fn chars(rng: &mut Rng, from: &str, n: usize) -> String {
    let all: Vec<char> = from.chars().collect();
    (0..n).map(|_| *pick(rng, &all)).collect()
}

fn affiliation(rng: &mut Rng) -> Affiliation {
    Affiliation {
        university: Some((*pick(rng, UNIVERSITIES)).into()),
        school: Some((*pick(rng, SCHOOLS)).into()),
        department: (rng.f32() < 0.6).then(|| (*pick(rng, DEPARTMENTS)).into()),
        title: pick(rng, TITLES).map(Into::into),
    }
}

fn mentor(rng: &mut Rng, i: usize) -> MentorFields {
    // The first topic is the recent focus; the rest are listed.
    let n = rng.usize(3..6);
    let mut topics = rng.choose_multiple(TOPICS.iter().copied(), n);
    rng.shuffle(&mut topics);
    let given = rng.usize(1..3);
    // A few work in two places, or write from a second address too.
    let mut affiliations = vec![affiliation(rng)];
    if rng.f32() < 0.05 {
        affiliations.push(affiliation(rng));
    }
    let mut emails = Vec::new();
    if rng.f32() < 0.9 {
        emails.push(format!("m{i}@example.edu.cn"));
    }
    if rng.f32() < 0.1 {
        emails.push(format!("m{i}@example.com"));
    }
    MentorFields {
        name: chars(rng, SURNAMES, 1) + &chars(rng, GIVEN, given),
        aliases: Vec::new(),
        emails,
        affiliations,
        homepages: vec![format!("https://example.edu.cn/~m{i}")],
        research: Some(format!(
            "{}，近期关注{}方向的应用。",
            topics[1..].join("、"),
            topics[0]
        )),
    }
}

pub async fn fill(db: &SqlitePool, scale: Scale, now: i64) -> Result {
    let mut rng = Rng::with_seed(7);
    let mut tx = db.begin().await?;
    sqlx::query(
        // A domain with no mail servers, so testing this mailbox reaches no one.
        "INSERT INTO mailbox VALUES (1, 'linxiao@example.com', '林晓', 'custom', 'smtp.example.com', 465,
           'tls', 'imap.example.com', 993, 'tls', 'password', 'sample', 100, 60, 1, NULL, NULL, NULL, NULL, ?)",
    )
    .bind(now)
    .execute(&mut *tx)
    .await?;
    for (i, (name, color)) in TAGS.iter().enumerate() {
        sqlx::query("INSERT INTO tag (name, color, position) VALUES (?, ?, ?)")
            .bind(name)
            .bind(color)
            .bind(i as i64)
            .execute(&mut *tx)
            .await?;
    }

    for i in 1..=scale.mentors {
        let created = now - rng.i64(0..365) * DAY;
        let id = mentor::create(&mut *tx, &mentor(&mut rng, i), created).await?;
        let roll = rng.f32();
        let state = match roll {
            r if r < 0.45 => Some("done"),
            r if r < 0.48 => Some("queued"),
            r if r < 0.49 => Some("running"),
            r if r < 0.50 => Some("failed"),
            _ => None,
        };
        if let Some(state) = state {
            let done = state == "done";
            sqlx::query(
                "UPDATE mentor SET match_state = ?, match_score = ?, matched_at = ?, match_model = ?,
                   match_error = ?, match_queued_at = ?, match_priority = ? WHERE id = ?",
            )
            .bind((!done).then_some(state))
            .bind(done.then(|| rng.i64(30..100)))
            .bind(done.then_some(now))
            .bind(done.then_some("deepseek-chat"))
            .bind((state == "failed").then_some("模型返回的结果不完整"))
            .bind(matches!(state, "queued" | "running").then_some(now))
            .bind(matches!(state, "queued" | "running").then_some(0))
            .bind(id)
            .execute(&mut *tx)
            .await?;
            if done {
                let [a, b, c] = [0; 3].map(|_| *pick(&mut rng, TOPICS));
                sqlx::query(
                    "UPDATE mentor_detail SET match_reason = ?, match_directions = ?,
                       match_highlights = ?, match_risks = ? WHERE mentor_id = ?",
                )
                .bind(format!(
                    "你的{a}项目经历和导师近年的{b}工作直接相关，本科课题也用到了{c}的方法。"
                ))
                .bind(serde_json::json!([format!("{a}在{b}中的应用"), c]).to_string())
                .bind(
                    serde_json::json!([
                        format!("{a}项目里独立完成的实验和开源代码"),
                        "数学和编程课程的成绩",
                    ])
                    .to_string(),
                )
                .bind(serde_json::json!([format!("没有{b}方向的论文")]).to_string())
                .bind(id)
                .execute(&mut *tx)
                .await?;
            }
        }
        sqlx::query("UPDATE mentor SET source_url = ? WHERE id = ?")
            .bind(format!("https://example.edu.cn/faculty/list{}.htm", i % 9))
            .bind(id)
            .execute(&mut *tx)
            .await?;
        if rng.f32() < 0.5 {
            let papers: Vec<String> = (0..rng.usize(1..4))
                .map(|n| {
                    let topic = pick(&mut rng, TOPICS);
                    format!("面向{topic}的高效方法研究（{}）", 2025 - n)
                })
                .collect();
            sqlx::query("UPDATE mentor_detail SET papers = ? WHERE mentor_id = ?")
                .bind(serde_json::to_string(&papers)?)
                .bind(id)
                .execute(&mut *tx)
                .await?;
        }
        if rng.f32() < 0.05 {
            sqlx::query("UPDATE mentor_detail SET note = ? WHERE mentor_id = ?")
                .bind("学长说组里氛围好，每周组会。")
                .bind(id)
                .execute(&mut *tx)
                .await?;
        }
        if rng.f32() < 0.03 {
            sqlx::query("UPDATE mentor SET trashed_at = ? WHERE id = ?")
                .bind(now)
                .bind(id)
                .execute(&mut *tx)
                .await?;
        }
        if rng.f32() < 0.2 {
            sqlx::query("INSERT OR IGNORE INTO mentor_tag (mentor_id, tag_id) VALUES (?, ?)")
                .bind(id)
                .bind(rng.i64(1..=TAGS.len() as i64))
                .execute(&mut *tx)
                .await?;
        }
    }

    // Mail goes to a subset of mentors, so some have several emails and most have none.
    let contacted: Vec<i64> = (0..(scale.emails * 6 / 10).max(1))
        .map(|_| rng.i64(1..=scale.mentors as i64))
        .collect();
    let states: Vec<&str> = [
        ("sent", 75),
        ("bounced", 3),
        ("maybe_sent", 1),
        ("approved", 8),
        ("review", 5),
        ("failed", 3),
        ("canceled", 5),
    ]
    .into_iter()
    .flat_map(|(state, weight)| std::iter::repeat_n(state, weight))
    .collect();
    let mut sent = Vec::new();
    let mut drafted = HashSet::new();
    for i in 1..=scale.emails {
        let mentor = *pick(&mut rng, &contacted);
        let state = *pick(&mut rng, &states);
        // A mentor has at most one first email being written outside a task.
        let kind = if rng.f32() < 0.85 && (state != "review" || drafted.insert(mentor)) {
            "first"
        } else {
            "follow_up"
        };
        let out = matches!(state, "sent" | "bounced" | "maybe_sent");
        let rendered = !matches!(state, "review" | "canceled");
        let at = now - rng.i64(0..180) * DAY - rng.i64(0..DAY);
        sqlx::query(
            "INSERT INTO email (mentor_id, kind, reply_to, state, planned_at, mailbox_id, from_address,
               to_address, model, message_id, sent_at, error, created_at, updated_at, subject, body_md,
               html, text)
             VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'deepseek-chat', ?, ?, ?, ?, ?, '申请 2027 年博士生', ?, ?, ?)",
        )
        .bind(mentor)
        .bind(kind)
        .bind((kind != "first").then(|| format!("<earlier{i}@aes>")))
        .bind(state)
        // Approved ones are due later, so the development app does not send them to made-up mentors.
        .bind(rendered.then(|| if state == "approved" { now + rng.i64(1..30) * DAY } else { at }))
        .bind(out.then_some(1))
        .bind(out.then_some("linxiao@example.com"))
        .bind(out.then(|| format!("m{mentor}@example.edu.cn")))
        .bind(out.then(|| format!("<e{i}@aes>")))
        .bind(out.then_some(at))
        .bind(matches!(state, "maybe_sent" | "failed" | "bounced" | "canceled").then_some("示例错误"))
        .bind(at)
        .bind(at)
        .bind("老师您好：\n\n我是北京航空航天大学的林晓。")
        .bind(rendered.then_some("<p>老师您好：</p><p>我是北京航空航天大学的林晓。</p>"))
        .bind(rendered.then_some("老师您好：\n\n我是北京航空航天大学的林晓。"))
        .execute(&mut *tx)
        .await?;
        if out {
            sent.push((mentor, at, i));
        }
    }

    for i in 1..=scale.messages {
        let Some(&(mentor, at, email)) = rng.choice(&sent) else {
            break;
        };
        let kind = match rng.u32(0..100) {
            0..85 => "reply",
            85..93 => "auto_reply",
            _ => "sent",
        };
        let at = rng.i64(at..=(at + 10 * DAY).min(now));
        let read = (kind == "reply" && rng.f32() < 0.7)
            .then_some(at + DAY)
            .filter(|t| *t < now);
        let id = sqlx::query(
            "INSERT INTO message (mailbox_id, folder, uidvalidity, uid, kind, message_id, in_reply_to,
               sent_at, read_at, from_address, subject, body_text, truncated)
             VALUES (1, ?, 1, ?, ?, ?, ?, ?, ?, 'mentor@example.edu.cn', '回复：申请 2027 年博士生', ?, 0)",
        )
        .bind(if kind == "sent" { "Sent" } else { "INBOX" })
        .bind(i as i64)
        .bind(kind)
        .bind(format!("<m{i}@example.edu.cn>"))
        .bind(format!("<e{email}@aes>"))
        .bind(at)
        .bind(read)
        .bind("林晓同学你好：\n\n简历已收到，下周可以线上聊一聊。")
        .execute(&mut *tx)
        .await?
        .last_insert_rowid();
        sqlx::query("INSERT INTO message_mentor (mentor_id, message_id) VALUES (?, ?)")
            .bind(mentor)
            .bind(id)
            .execute(&mut *tx)
            .await?;
    }
    tx.commit().await?;
    Ok(())
}
