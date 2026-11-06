// Marks the whole file as test code, so clippy's test allowances cover the helpers too.
#![cfg(test)]

//! Logging in and sending against the fake mail servers.

use std::borrow::Cow;

use fakemail::{ADDRESS, Inbox, Pki, SECRET};
use mailbox::Account;
use net::Net;
use transport::{MailError, Security, SendError, Sender, Server};

/// A mailbox on fake servers of its own.
async fn mailbox(pki: &Pki, security: Security, secret: &str) -> (Account, Inbox) {
    let starttls = matches!(security, Security::Starttls);
    let (smtp, inbox) = fakemail::smtp(pki, starttls).await;
    let (imap, _) = fakemail::imap(pki, starttls).await;
    let server = |port| Server {
        host: Cow::Borrowed("localhost"),
        port,
        security,
    };
    let me = Account {
        sender: Sender {
            address: ADDRESS.parse().unwrap(),
            sender_name: "林晓".into(),
            smtp: server(smtp),
            secret: secret.into(),
        },
        imap: server(imap),
    };
    (me, inbox)
}

#[tokio::test]
async fn checks_pass_over_tls_and_starttls() {
    let pki = fakemail::pki();
    let net = Net::new(vec![pki.root.clone()]).unwrap();
    for security in [Security::Tls, Security::Starttls] {
        let (me, _) = mailbox(&pki, security, SECRET).await;
        transport::check_sending(&net, &me.sender).await.unwrap();
        mailbox::check_receiving(&net, &me).await.unwrap();
    }
}

#[tokio::test]
async fn the_official_server_does_not_connect_into_its_own_network() {
    let pki = fakemail::pki();
    let net = Net::new(vec![pki.root.clone()]).unwrap().public_only();
    let (me, _) = mailbox(&pki, Security::Tls, SECRET).await;
    let sending = transport::check_sending(&net, &me.sender).await;
    assert!(
        matches!(sending, Err(MailError::Connect { .. })),
        "{sending:?}"
    );
}

#[tokio::test]
async fn a_wrong_secret_is_a_refused_login() {
    let pki = fakemail::pki();
    let net = Net::new(vec![pki.root.clone()]).unwrap();
    let (me, _) = mailbox(&pki, Security::Tls, "wrong").await;
    let sending = transport::check_sending(&net, &me.sender).await;
    assert!(
        matches!(
            sending,
            Err(MailError::Login {
                ref reply,
                smtp_code: Some(535),
            }) if reply.contains("535")
        ),
        "{sending:?}"
    );
    // The log has the codes, not the server's words.
    assert_eq!(
        sending.unwrap_err().to_string(),
        "login refused (535 5.7.8)"
    );
    let receiving = mailbox::check_receiving(&net, &me).await;
    assert!(
        matches!(
            receiving,
            Err(MailError::Login {
                ref reply,
                smtp_code: None,
            }) if reply == "[AUTHENTICATIONFAILED] invalid credentials"
        ),
        "{receiving:?}"
    );
}

#[tokio::test]
async fn replies_that_cannot_be_read_are_not_quoted() {
    let pki = fakemail::pki();
    let net = Net::new(vec![pki.root.clone()]).unwrap();
    let (mut me, _) = mailbox(&pki, Security::Tls, SECRET).await;
    // The fake's reply quotes the recipient; the mail libraries' wording of it, the reply.
    let message = lettre::Message::builder()
        .from(ADDRESS.parse().unwrap())
        .to("garbled@example.edu.cn".parse().unwrap())
        .subject("申请硕士")
        .body(String::from("王老师：您好！"))
        .unwrap();
    let sending = transport::login(&net, &me.sender)
        .await
        .unwrap()
        .send(&message.into(), || false)
        .await;
    assert!(
        matches!(sending, Err(SendError::NotSent(MailError::Unreadable))),
        "{:?}",
        sending.err()
    );
    // This one quotes the secret.
    let (imap, store) = fakemail::imap(&pki, false).await;
    me.imap.port = imap;
    store.garble("EXAMINE");
    let receiving = mailbox::check_receiving(&net, &me).await;
    assert!(
        matches!(receiving, Err(MailError::Unreadable)),
        "{receiving:?}"
    );
}

#[tokio::test]
async fn a_server_ending_the_session_closed_the_connection() {
    let pki = fakemail::pki();
    let net = Net::new(vec![pki.root.clone()]).unwrap();
    let (mut me, _) = mailbox(&pki, Security::Tls, SECRET).await;
    let (imap, store) = fakemail::imap(&pki, false).await;
    me.imap.port = imap;
    store.bye("EXAMINE");
    let receiving = mailbox::check_receiving(&net, &me).await;
    // Not taken for a reply that cannot be read, nor quoting the BYE.
    assert!(
        matches!(receiving, Err(MailError::Broken(ref e)) if !e.contains(SECRET)),
        "{receiving:?}"
    );
}

#[tokio::test]
async fn a_sent_folder_named_with_a_line_break_is_left_alone() {
    let pki = fakemail::pki();
    let net = Net::new(vec![pki.root.clone()]).unwrap();
    let (mut me, _) = mailbox(&pki, Security::Tls, SECRET).await;
    let (imap, store) = fakemail::imap(&pki, false).await;
    me.imap.port = imap;
    // Opening it would send the rest of its name as another command.
    store.create("Sent\r\nA1 NOOP", true);
    store.create("Sent Messages", false);
    let mut session = mailbox::Imap::open(&net, &me).await.unwrap();
    assert_eq!(
        session.sent_folder().await.unwrap().as_deref(),
        Some("Sent Messages")
    );
}

#[tokio::test]
async fn an_untrusted_certificate_is_refused() {
    let pki = fakemail::pki();
    let net = Net::new(Vec::new()).unwrap();
    for security in [Security::Tls, Security::Starttls] {
        let (me, _) = mailbox(&pki, security, SECRET).await;
        for result in [
            transport::check_sending(&net, &me.sender).await,
            mailbox::check_receiving(&net, &me).await,
        ] {
            let message = result.unwrap_err().message();
            assert!(message.contains("证书不受信任"), "{security:?}: {message}");
        }
    }
}

#[tokio::test]
async fn the_test_email_goes_to_the_mailbox_itself() {
    let pki = fakemail::pki();
    let net = Net::new(vec![pki.root.clone()]).unwrap();
    let (me, inbox) = mailbox(&pki, Security::Starttls, SECRET).await;
    transport::send_test(&net, &me.sender).await.unwrap();
    let inbox = inbox.lock().unwrap();
    assert_eq!(inbox.len(), 1);
    let headers = inbox[0].split("\r\n\r\n").next().unwrap();
    let header = |name: &str| {
        headers
            .lines()
            .find_map(|l| l.strip_prefix(name))
            .unwrap_or_default()
    };
    assert!(
        header("From: ").ends_with(&format!("<{ADDRESS}>")),
        "{headers}"
    );
    assert!(
        header("To: ").ends_with(&format!("<{ADDRESS}>")),
        "{headers}"
    );
    // Named after the mailbox's domain, not the computer.
    assert!(
        header("Message-ID: ").ends_with("@example.com>"),
        "{headers}"
    );
}

#[tokio::test]
async fn a_throttled_login_says_to_wait_not_to_change_the_code() {
    let pki = fakemail::pki();
    let net = Net::new(vec![pki.root.clone()]).unwrap();
    let (me, _) = mailbox(&pki, Security::Tls, "throttled").await;
    let message = transport::check_sending(&net, &me.sender)
        .await
        .unwrap_err()
        .message();
    assert!(
        message.starts_with("服务商限制了登录或发信的频率"),
        "{message}"
    );
}
