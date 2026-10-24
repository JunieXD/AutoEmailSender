// Marks the whole file as test code, so clippy's test allowances cover the helpers too.
#![cfg(test)]

//! Logging in and sending against the fake mail servers.

use std::borrow::Cow;

use fakemail::{ADDRESS, Inbox, Pki, SECRET};
use mailbox::{Account, MailError, Security, Server};
use net::Net;

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
        address: ADDRESS.parse().unwrap(),
        sender_name: "林晓".into(),
        smtp: server(smtp),
        imap: server(imap),
        secret: secret.into(),
    };
    (me, inbox)
}

#[tokio::test]
async fn checks_pass_over_tls_and_starttls() {
    let pki = fakemail::pki();
    let net = Net::new(vec![pki.root.clone()]).unwrap();
    for security in [Security::Tls, Security::Starttls] {
        let (me, _) = mailbox(&pki, security, SECRET).await;
        mailbox::check_sending(&net, &me).await.unwrap();
        mailbox::check_receiving(&net, &me).await.unwrap();
    }
}

#[tokio::test]
async fn a_wrong_secret_is_a_refused_login() {
    let pki = fakemail::pki();
    let net = Net::new(vec![pki.root.clone()]).unwrap();
    let (me, _) = mailbox(&pki, Security::Tls, "wrong").await;
    let sending = mailbox::check_sending(&net, &me).await;
    assert!(
        matches!(sending, Err(MailError::Login(ref r)) if r.contains("535")),
        "{sending:?}"
    );
    let receiving = mailbox::check_receiving(&net, &me).await;
    assert!(
        matches!(receiving, Err(MailError::Login(ref r)) if r == "[AUTHENTICATIONFAILED] invalid credentials"),
        "{receiving:?}"
    );
}

#[tokio::test]
async fn an_untrusted_certificate_is_refused() {
    let pki = fakemail::pki();
    let net = Net::new(Vec::new()).unwrap();
    for security in [Security::Tls, Security::Starttls] {
        let (me, _) = mailbox(&pki, security, SECRET).await;
        for result in [
            mailbox::check_sending(&net, &me).await,
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
    mailbox::send_test(&net, &me).await.unwrap();
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
