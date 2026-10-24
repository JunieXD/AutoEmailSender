//! Telling what a reply says from the earlier mail it quotes (docs/design.md#桌面端), by the marks
//! mail apps put before a quote.

/// The new part of a message's text and the quote that follows it, which may be empty. A message
/// that is all quote is left whole.
pub(crate) fn split(text: &str) -> (&str, &str) {
    let lines: Vec<(usize, &str)> = text
        .split_inclusive('\n')
        .scan(0, |at, line| {
            let start = *at;
            *at += line.len();
            Some((start, line.trim_end()))
        })
        .collect();
    let start = (0..lines.len())
        .find_map(|i| quote_start(&lines, i))
        .map(|i| lines[i].0);
    match start {
        Some(at) if !text[..at].trim().is_empty() => (text[..at].trim_end(), text[at..].trim_end()),
        _ => (text.trim_end(), ""),
    }
}

/// Where a quote starts if line `i` begins one.
fn quote_start(lines: &[(usize, &str)], i: usize) -> Option<usize> {
    let line = lines[i].1.trim();
    let following = || {
        lines[i + 1..]
            .iter()
            .map(|(_, l)| l.trim())
            .filter(|l| !l.is_empty())
    };
    // Lines that all quote, with no answers written between them.
    let quoted_to_end = || following().next().is_some() && following().all(|l| l.starts_with('>'));
    // Answers written below quoted lines, which are then no quote to fold.
    let answered = || {
        let mut quoting = false;
        following().any(|l| {
            quoting |= l.starts_with('>');
            quoting && !l.starts_with('>')
        })
    };
    // ------------------ 原始邮件 ------------------, -----Original Message-----
    if line.starts_with('-')
        && SEPARATORS.iter().any(|s| line.to_lowercase().contains(s))
        && !answered()
    {
        return Some(i);
    }
    // 发件人: …  发送时间: …, or From: …  Sent: …, often after a line of underscores.
    if starts_with_any(line, &FROM)
        && following().take(4).any(|l| starts_with_any(l, &DETAILS))
        && !answered()
    {
        let rule = i > 0 && lines[i - 1].1.trim().starts_with("____");
        return Some(if rule { i - 1 } else { i });
    }
    // 在 2026年10月20日 10:00，张三 写道：  On Mon, 20 Oct 2026 at 10:00, Zhang wrote:
    // followed by the quoted lines; the English one may be wrapped over two lines.
    if ATTRIBUTIONS.iter().any(|a| line.ends_with(a)) && quoted_to_end() {
        let wrapped = i > 0 && lines[i - 1].1.trim().starts_with("On ") && !line.starts_with("On ");
        return Some(if wrapped { i - 1 } else { i });
    }
    // Quoted lines to the end.
    if line.starts_with('>') && following().all(|l| l.starts_with('>')) {
        return Some(i);
    }
    None
}

fn starts_with_any(line: &str, starts: &[&str]) -> bool {
    starts.iter().any(|s| line.starts_with(s))
}

/// In lowercase.
const SEPARATORS: [&str; 3] = ["原始邮件", "回复的原邮件", "original message"];
const FROM: [&str; 4] = ["发件人:", "发件人：", "From:", "*From:*"];
const DETAILS: [&str; 16] = [
    "发送时间:",
    "发送时间：",
    "发件时间:",
    "发件时间：",
    "时间:",
    "时间：",
    "日期:",
    "日期：",
    "收件人:",
    "收件人：",
    "主题:",
    "主题：",
    "Sent:",
    "Date:",
    "To:",
    "Subject:",
];
const ATTRIBUTIONS: [&str; 3] = ["写道：", "写道:", "wrote:"];

#[cfg(test)]
mod tests {
    use super::split;

    /// Replies as mail apps write them: what the mentor wrote, then the quote.
    #[test]
    fn a_reply_is_told_from_what_it_quotes() {
        let cases = [
            // QQ Mail
            (
                "同学你好，欢迎报考。\n\n李明\n\n------------------ 原始邮件 ------------------\n发件人: \"张三\"<me@qq.com>;\n发送时间: 2026年10月20日(星期一) 上午10:00\n收件人: \"李明\"<li@pku.edu.cn>;\n主题: 申请读博\n\n李老师：\n您好！\n",
                "同学你好，欢迎报考。\n\n李明",
            ),
            // Coremail, at many universities
            (
                "收到，周五面谈。\n-----原始邮件-----\n发件人:\"张三\" <me@qq.com>\n发送时间:2026-10-20 10:00:00 (星期一)\n收件人:li@pku.edu.cn\n主题:申请读博\n\n李老师：您好！",
                "收到，周五面谈。",
            ),
            // 163
            (
                "可以，请发成绩单。\n\n在 2026-10-20 10:00:00，\"张三\" <me@163.com> 写道：\n> 李老师：\n> 您好！\n",
                "可以，请发成绩单。",
            ),
            // Gmail, the attribution wrapped
            (
                "Thanks, let's talk next week.\n\nOn Mon, Oct 20, 2026 at 10:00 AM Zhang San <\nme@gmail.com> wrote:\n\n> Dear Prof. Li,\n> I am writing to\n",
                "Thanks, let's talk next week.",
            ),
            // Outlook in Chinese
            (
                "好的。\n\n________________________________\n发件人: 张三 <me@outlook.com>\n发送时间: 2026年10月20日 10:00\n收件人: 李明 <li@pku.edu.cn>\n主题: 申请读博\n\n李老师：",
                "好的。",
            ),
            // Outlook in English
            (
                "Sure.\r\n\r\nFrom: Zhang San <me@outlook.com>\r\nSent: Monday, October 20, 2026 10:00 AM\r\nTo: Li Ming <li@pku.edu.cn>\r\nSubject: PhD application\r\n\r\nDear Prof. Li,",
                "Sure.",
            ),
            // Quoted lines at the end, with no attribution
            ("同意。\n> 李老师：\n> 您好！", "同意。"),
        ];
        for (text, fresh) in cases {
            let (new, quote) = split(text);
            assert_eq!(new, fresh, "{text}");
            assert!(!quote.is_empty(), "{text}");
        }
    }

    #[test]
    fn what_only_looks_like_a_quote_stays() {
        let cases = [
            // Answers between quoted lines.
            "> 能否参加夏令营？\n可以。\n> 需要推荐信吗？\n需要两封。",
            "我的答复如下：\nOn Mon, Oct 20, 2026, Zhang wrote:\n> 可以报考吗？\n欢迎报考。\n> 需要提交成绩单吗？\n请寄给秘书。",
            "答复如下。\n-----Original Message-----\nFrom: Zhang <me@example.com>\nSent: Monday, October 20, 2026\n> 可以报考吗？\n欢迎报考。",
            // Saying what someone wrote, with nothing quoted after it.
            "你在信中写道：\n希望读博。我们组今年有名额。",
            // A header-like line on its own.
            "From: 下周起我在国外开会。",
            // All quote, as a bare forward.
            "> 李老师：\n> 您好！",
        ];
        for text in cases {
            assert_eq!(split(text), (text, ""), "{text}");
        }
    }

    #[test]
    fn a_reply_to_our_direct_reply_folds_its_attribution_and_quote() {
        let text = "好的，下周见。\n\n在 2026年10月24日 14:00，林晓 <lin@example.com> 写道：\n> 王老师：谢谢。\n> \n> 在 2026年10月23日 09:00，王伟 <wang@pku.edu.cn> 写道：\n> > 欢迎报考。";
        let (fresh, quote) = split(text);
        assert_eq!(fresh, "好的，下周见。");
        assert!(quote.starts_with("在 2026年10月24日 14:00，林晓"));
        assert!(quote.ends_with("> > 欢迎报考。"));
    }
}
