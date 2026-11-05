//! Agent input is checked before entering the shared candidate merge transaction.
use crate::{PageUrl, extract::Person, queue::PERSON_LIMIT, review};
use mentor::{Affiliation, MentorForm};
use serde::{Deserialize, Serialize};
use sqlx::SqlitePool;
use ts_rs::TS;

#[derive(Debug, Deserialize, TS)]
#[serde(rename_all = "camelCase")]
pub struct SubmitCandidates {
    #[ts(optional)]
    pub crawl_id: Option<i64>,
    #[serde(default)]
    #[ts(as = "Option<_>", optional)]
    pub candidates: Vec<SubmittedCandidate>,
}

#[derive(Debug, Deserialize, TS)]
#[serde(rename_all = "camelCase")]
pub struct SubmittedCandidate {
    #[serde(default)]
    pub name: String,
    #[serde(default)]
    #[ts(as = "Option<_>", optional)]
    pub aliases: Vec<String>,
    #[serde(default)]
    #[ts(as = "Option<_>", optional)]
    pub emails: Vec<String>,
    #[serde(default)]
    #[ts(as = "Option<_>", optional)]
    pub affiliations: Vec<Affiliation>,
    #[serde(default)]
    #[ts(as = "Option<_>", optional)]
    pub homepages: Vec<String>,
    #[ts(optional)]
    pub profile_url: Option<String>,
    #[serde(default)]
    pub source_url: String,
    #[serde(default)]
    #[ts(as = "Option<_>", optional)]
    pub research: String,
    #[serde(default)]
    #[ts(as = "Option<_>", optional)]
    pub papers: Vec<String>,
    #[ts(optional)]
    pub note: Option<String>,
}

#[derive(Debug, Serialize, TS)]
#[serde(rename_all = "camelCase")]
pub struct Submitted {
    pub crawl_id: i64,
    pub added: u32,
    pub merged: u32,
    pub total: i64,
}

#[derive(Debug)]
pub enum SubmitError {
    Invalid(String),
    Deleted,
    Db(sqlx::Error),
}
impl From<sqlx::Error> for SubmitError {
    fn from(error: sqlx::Error) -> Self {
        Self::Db(error)
    }
}

struct Checked {
    person: Person,
    source: PageUrl,
    note: Option<String>,
}

fn check(candidates: Vec<SubmittedCandidate>) -> Result<Vec<Checked>, SubmitError> {
    if candidates.is_empty() || candidates.len() > 100 {
        return Err(SubmitError::Invalid(
            "每次请提交 1～100 位候选导师。".into(),
        ));
    }
    let mut errors = Vec::new();
    let mut checked = Vec::new();
    for (index, c) in candidates.into_iter().enumerate() {
        let mut error = |message: String| {
            errors.push(format!(
                "第 {} 位（{}）：{message}",
                index + 1,
                c.name.trim()
            ));
        };
        let fields = MentorForm {
            name: c.name.clone(),
            aliases: c.aliases,
            emails: c.emails,
            affiliations: c.affiliations,
            homepages: c.homepages,
            research: c.research,
        }
        .check();
        if let Err(message) = &fields {
            error(message.clone());
        }
        let source = PageUrl::parse(&c.source_url);
        if let Err(e) = &source {
            error(format!("来源页（sourceUrl）：{}", e.message()));
        }
        let profile = c.profile_url.as_deref().map(PageUrl::parse).transpose();
        if let Err(e) = &profile {
            error(format!("个人页（profileUrl）：{}", e.message()));
        }
        if c.papers.len() > 20 {
            error("代表论文（papers）最多 20 篇。".into());
        }
        if c.note.as_ref().is_some_and(|s| s.chars().count() > 500) {
            error("说明（note）最多 500 字。".into());
        }
        if let (Ok(mut fields), Ok(source), Ok(profile)) = (fields, source, profile) {
            // Written as profile links are, so a link found not to be theirs leaves the homepages.
            let mut homepages = Vec::new();
            for url in fields.homepages {
                let url = PageUrl::parse(&url).map_or(url, |u| u.as_str().to_owned());
                if !homepages.contains(&url) {
                    homepages.push(url);
                }
            }
            fields.homepages = homepages;
            checked.push(Checked {
                person: Person {
                    fields,
                    papers: c.papers,
                    profile,
                },
                source,
                note: c.note.filter(|s| !s.trim().is_empty()),
            });
        }
    }
    if errors.is_empty() {
        Ok(checked)
    } else {
        let total = errors.len();
        errors.truncate(10);
        if total > 10 {
            errors.push(format!("另有 {} 条问题，共 {total} 条。", total - 10));
        }
        Err(SubmitError::Invalid(errors.join("\n")))
    }
}

pub async fn submit(
    db: &SqlitePool,
    input: SubmitCandidates,
    at: i64,
) -> Result<Submitted, SubmitError> {
    let candidates = check(input.candidates)?;
    let mut tx = db.begin_with("BEGIN IMMEDIATE").await?;
    let crawl = if let Some(id) = input.crawl_id {
        let kind = sqlx::query_scalar!("SELECT kind FROM crawl WHERE id = ?", id)
            .fetch_optional(&mut *tx)
            .await?
            .ok_or(SubmitError::Deleted)?;
        if kind != "agent" {
            return Err(SubmitError::Invalid(
                "只能追加到 Agent 提交的整理记录。".into(),
            ));
        }
        sqlx::query!(
            "UPDATE crawl SET finished_at = ? WHERE id = ? AND state = 'done'",
            at,
            id
        )
        .execute(&mut *tx)
        .await?;
        id
    } else {
        let affiliation = candidates[0].person.fields.affiliations.first();
        let university = affiliation.and_then(|a| a.university.as_deref());
        let school = affiliation.and_then(|a| a.school.as_deref());
        sqlx::query_scalar!(
            r#"INSERT INTO crawl (kind, university, school, state, created_at, finished_at)
            VALUES ('agent', ?, ?, 'done', ?, ?)
            RETURNING id"#,
            university,
            school,
            at,
            at
        )
        .fetch_one(&mut *tx)
        .await?
    };
    let mut result = Submitted {
        crawl_id: crawl,
        added: 0,
        merged: 0,
        total: 0,
    };
    for c in candidates {
        if review::save(
            &mut tx,
            crawl,
            review::Source::Agent { note: c.note },
            c.source.as_str(),
            c.person,
        )
        .await?
        {
            result.added += 1;
        } else {
            result.merged += 1;
        }
    }
    result.total = sqlx::query_scalar!("SELECT count(*) FROM candidate WHERE crawl_id = ?", crawl)
        .fetch_one(&mut *tx)
        .await?;
    if result.total > PERSON_LIMIT {
        return Err(SubmitError::Invalid(
            "这条整理记录最多容纳 1000 位候选导师，请不带 crawlId 重新提交，新开一条记录。".into(),
        ));
    }
    tx.commit().await?;
    crate::span(crawl).in_scope(|| {
        tracing::info!(
            added = result.added,
            merged = result.merged,
            "agent candidates submitted"
        );
    });
    Ok(result)
}
