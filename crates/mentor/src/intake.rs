//! Shared identity matching, non-destructive supplementation, and transactional application.
use crate::export::{Profile, profiles};
use crate::import::{
    AddedFields, Addition, AffiliationAddition, Decisions, Difference, Field, ImportCandidate,
    Imported, MaybeSame, Preview, RowError, Trashed,
};
pub use crate::supplement::comparable;
use crate::supplement::{add_missing, append, same};
use crate::{SaveError, create, trash, update};
use sqlx::{SqliteConnection, types::Json};
use std::collections::{BTreeMap, BTreeSet, HashMap};

pub struct Row {
    pub number: u32,
    /// A prior user-approved association, never supplied by an external extractor.
    pub targets: Vec<i64>,
    pub profile: Profile,
}
pub struct Change {
    before: Option<Profile>,
    after: Profile,
    restore: bool,
    rows: Vec<u32>,
}
impl Change {
    pub fn changed_recipient(&self) -> Option<i64> {
        let before = self.before.as_ref()?;
        let same = match (
            before.fields.emails.first(),
            self.after.fields.emails.first(),
        ) {
            (Some(a), Some(b)) => a.eq_ignore_ascii_case(b),
            (None, None) => true,
            _ => false,
        };
        (!same).then_some(before.id)
    }

    fn changed(&self) -> bool {
        self.restore || self.before.as_ref() != Some(&self.after)
    }
}
struct Candidates {
    profiles: BTreeMap<i64, Profile>,
    by_name: HashMap<String, Vec<i64>>,
}

async fn candidates(conn: &mut SqliteConnection, rows: &[Row]) -> Result<Candidates, sqlx::Error> {
    let emails = Json(
        rows.iter()
            .flat_map(|r| &r.profile.fields.emails)
            .collect::<Vec<_>>(),
    );
    let owners = sqlx::query!(
        r#"SELECT address, mentor_id
        FROM mentor_email
        WHERE address IN (SELECT value FROM json_each(?))"#,
        emails
    )
    .fetch_all(&mut *conn)
    .await?;
    let addresses: BTreeSet<_> = owners.iter().map(|e| e.address.to_lowercase()).collect();
    let mut ids: BTreeSet<_> = owners.into_iter().map(|e| e.mentor_id).collect();
    let names: BTreeSet<_> = rows
        .iter()
        .filter(|r| {
            !r.profile
                .fields
                .emails
                .iter()
                .any(|e| addresses.contains(&e.to_lowercase()))
        })
        .flat_map(|r| std::iter::once(&r.profile.fields.name).chain(&r.profile.fields.aliases))
        .map(|name| comparable(name))
        .collect();
    ids.extend(rows.iter().flat_map(|r| r.targets.iter().copied()));
    let mut by_name = HashMap::<String, Vec<i64>>::new();
    if !names.is_empty() {
        let names = Json(names);
        for record in sqlx::query!(
            r#"SELECT mentor_id AS id, name
            FROM mentor_name
            WHERE name IN (SELECT value FROM json_each(?))"#,
            names
        )
        .fetch_all(&mut *conn)
        .await?
        {
            let name = comparable(&record.name);
            by_name.entry(name).or_default().push(record.id);
            ids.insert(record.id);
        }
    }
    Ok(Candidates {
        profiles: profiles(conn, &ids.into_iter().collect::<Vec<_>>()).await?,
        by_name,
    })
}

fn root(parents: &[usize], mut n: usize) -> usize {
    while parents[n] != n {
        n = parents[n];
    }
    n
}

fn combine_rows(rows: &[Row]) -> Profile {
    let mut incoming = rows[0].profile.clone();
    for row in rows.iter().skip(1) {
        let mut emails = incoming.fields.emails.clone();
        for email in &row.profile.fields.emails {
            if !emails.iter().any(|e| e.eq_ignore_ascii_case(email)) {
                emails.push(email.clone());
            }
        }
        add_missing(
            &mut incoming.fields,
            &mut incoming.papers,
            &row.profile.fields,
            &row.profile.papers,
        );
        incoming.fields.emails = emails;
        if !same(&incoming.fields.name, &row.profile.fields.name) {
            append(
                &mut incoming.fields.aliases,
                std::slice::from_ref(&row.profile.fields.name),
            );
        }
        append(&mut incoming.tags, &row.profile.tags);
        if incoming.fields.research.is_none() {
            incoming
                .fields
                .research
                .clone_from(&row.profile.fields.research);
        }
        if incoming.source_url.is_none() {
            incoming.source_url.clone_from(&row.profile.source_url);
        }
        if incoming.note.is_none() {
            incoming.note.clone_from(&row.profile.note);
        }
    }
    incoming
}

fn supplement(original: &Profile, incoming: &Profile, row: u32) -> (Profile, Vec<Difference>) {
    let mut after = original.clone();
    let titles = add_missing(
        &mut after.fields,
        &mut after.papers,
        &incoming.fields,
        &incoming.papers,
    );
    append(&mut after.tags, &incoming.tags);
    let mut differences = Vec::new();
    for (field, a, b) in [
        (
            Field::Name,
            Some(&original.fields.name),
            Some(&incoming.fields.name),
        ),
        (
            Field::Research,
            original.fields.research.as_ref(),
            incoming.fields.research.as_ref(),
        ),
        (
            Field::SourceUrl,
            original.source_url.as_ref(),
            incoming.source_url.as_ref(),
        ),
        (Field::Note, original.note.as_ref(), incoming.note.as_ref()),
    ] {
        if let (Some(a), Some(b)) = (a, b)
            && !same(a, b)
        {
            differences.push(Difference {
                row,
                id: original.id,
                name: original.fields.name.clone(),
                field,
                affiliation: None,
                local: a.clone(),
                incoming: b.clone(),
            });
        }
    }
    for title in titles {
        let mut affiliation = after.fields.affiliations[title.position].clone();
        affiliation.title = None;
        differences.push(Difference {
            row,
            id: original.id,
            name: original.fields.name.clone(),
            field: Field::Title,
            affiliation: Some(affiliation),
            local: title.local,
            incoming: title.incoming,
        });
    }
    if after.fields.research.is_none() {
        after.fields.research = incoming.fields.research.clone();
    }
    if after.source_url.is_none() {
        after.source_url = incoming.source_url.clone();
    }
    if after.note.is_none() {
        after.note = incoming.note.clone();
    }
    (after, differences)
}

fn additions(original: &Profile, after: &Profile, rows: &[Row]) -> Addition {
    let added = |before: &[String], after: &[String]| {
        after
            .iter()
            .filter(|s| !before.contains(s))
            .cloned()
            .collect()
    };
    let affiliations = after
        .fields
        .affiliations
        .iter()
        .enumerate()
        .filter_map(|(n, a)| {
            let Some(before) = original.fields.affiliations.get(n) else {
                return Some(AffiliationAddition {
                    before: None,
                    added: a.clone(),
                });
            };
            let delta = crate::Affiliation {
                university: before
                    .university
                    .is_none()
                    .then(|| a.university.clone())
                    .flatten(),
                school: before.school.is_none().then(|| a.school.clone()).flatten(),
                department: before
                    .department
                    .is_none()
                    .then(|| a.department.clone())
                    .flatten(),
                title: before.title.is_none().then(|| a.title.clone()).flatten(),
            };
            (delta != crate::Affiliation::default()).then(|| AffiliationAddition {
                before: Some(before.clone()),
                added: delta,
            })
        })
        .collect();
    Addition {
        rows: rows.iter().map(|r| r.number).collect(),
        target: ImportCandidate {
            id: original.id,
            profile: original.into(),
        },
        fields: AddedFields {
            emails: added(&original.fields.emails, &after.fields.emails),
            aliases: added(&original.fields.aliases, &after.fields.aliases),
            homepages: added(&original.fields.homepages, &after.fields.homepages),
            affiliations,
            research: original
                .fields
                .research
                .is_none()
                .then(|| after.fields.research.clone())
                .flatten(),
        },
        papers: added(&original.papers, &after.papers),
        tags: added(&original.tags, &after.tags),
        source_url: original
            .source_url
            .is_none()
            .then(|| after.source_url.clone())
            .flatten(),
        note: original
            .note
            .is_none()
            .then(|| after.note.clone())
            .flatten(),
    }
}

pub async fn calculate(
    conn: &mut SqliteConnection,
    rows: Vec<Row>,
    decisions: &Decisions,
) -> Result<(Preview, Vec<Change>), sqlx::Error> {
    let mut preview = Preview::default();
    if rows.is_empty() {
        return Ok((preview, Vec::new()));
    }
    let Candidates {
        profiles: local,
        by_name,
    } = candidates(conn, &rows).await?;
    let emails: BTreeMap<_, _> = local
        .values()
        .flat_map(|p| {
            p.fields
                .emails
                .iter()
                .map(move |e| (e.to_lowercase(), p.id))
        })
        .collect();
    let schools: BTreeMap<_, Vec<_>> = local
        .values()
        .map(|p| {
            (
                p.id,
                p.fields
                    .affiliations
                    .iter()
                    .filter_map(|a| a.university.as_deref())
                    .map(comparable)
                    .collect(),
            )
        })
        .collect();
    let mut parents: Vec<_> = (0..rows.len()).collect();
    let mut by_email = BTreeMap::new();
    for (n, row) in rows.iter().enumerate() {
        for email in &row.profile.fields.emails {
            if let Some(other) = by_email.insert(email.to_lowercase(), n) {
                let a = root(&parents, n);
                let b = root(&parents, other);
                parents[a.max(b)] = a.min(b);
            }
        }
    }
    let mut email_groups = BTreeMap::<usize, Vec<Row>>::new();
    for (n, row) in rows.into_iter().enumerate() {
        email_groups.entry(root(&parents, n)).or_default().push(row);
    }
    let mut groups = BTreeMap::<(Option<i64>, u32), Vec<Row>>::new();
    for rows in email_groups.into_values() {
        let owners: BTreeSet<_> = rows
            .iter()
            .flat_map(|r| &r.profile.fields.emails)
            .filter_map(|e| emails.get(&e.to_lowercase()).copied())
            .chain(rows.iter().flat_map(|r| r.targets.iter().copied()))
            .collect();
        if owners.len() > 1 {
            let names = owners
                .iter()
                .map(|id| format!("“{}”（编号 {id}）", local[id].fields.name))
                .collect::<Vec<_>>()
                .join("、");
            let count = if owners.len() == 2 {
                "两位".to_owned()
            } else {
                format!("{} 位", owners.len())
            };
            for row in rows {
                preview.errors.push(RowError {
                    row: row.number,
                    reason: format!(
                        "这些邮箱对应{count}已有的导师：{names}。请先合并这些导师或修改资料"
                    ),
                });
            }
            continue;
        }
        let first = &rows[0];
        let mut target = owners.first().copied();
        if target.is_none() {
            let has_email = rows.iter().any(|r| !r.profile.fields.emails.is_empty());
            let schools = &schools;
            let possible: BTreeSet<_> = rows
                .iter()
                .flat_map(|row| {
                    let universities: Vec<_> = row
                        .profile
                        .fields
                        .affiliations
                        .iter()
                        .filter_map(|a| a.university.as_deref())
                        .map(comparable)
                        .collect();
                    std::iter::once(&row.profile.fields.name)
                        .chain(&row.profile.fields.aliases)
                        .filter_map(|name| by_name.get(&comparable(name)))
                        .flatten()
                        .map(|id| &local[id])
                        .filter(move |p| !has_email || p.fields.emails.is_empty())
                        .filter(move |p| {
                            universities.is_empty()
                                || universities.iter().any(|u| schools[&p.id].contains(u))
                        })
                })
                .map(|p| p.id)
                .collect();
            if !possible.is_empty() {
                // Choose each email group before combining groups that target the same mentor.
                let incoming = combine_rows(&rows);
                let exact: Vec<_> = possible
                    .iter()
                    .copied()
                    .filter(|id| {
                        let (after, differences) = supplement(&local[id], &incoming, first.number);
                        after == local[id] && differences.is_empty()
                    })
                    .collect();
                target = match exact.as_slice() {
                    [id] => Some(*id),
                    _ if possible.len() == 1 => possible.first().copied(),
                    _ => None,
                };
                if let Some(choice) = decisions.matches.iter().find(|choice| {
                    choice.row == first.number && choice.id.is_none_or(|id| possible.contains(&id))
                }) {
                    target = choice.id;
                }
                preview.maybe_same.push(MaybeSame {
                    row: first.number,
                    rows: rows.iter().map(|r| r.number).collect(),
                    incoming: (&incoming).into(),
                    candidates: possible
                        .iter()
                        .map(|id| ImportCandidate {
                            id: *id,
                            profile: (&local[id]).into(),
                        })
                        .collect(),
                    selected: target,
                });
            }
        }
        let key = target.map_or((None, first.number), |id| (Some(id), 0));
        groups.entry(key).or_default().extend(rows);
    }
    let mut changes = Vec::new();
    for ((target, _), mut rows) in groups {
        rows.sort_by_key(|r| r.number);
        let first = &rows[0];
        let before = target.and_then(|id| local.get(&id)).cloned();
        let restore = before.as_ref().is_some_and(|p| p.trashed);
        if let Some(p) = before.as_ref().filter(|p| p.trashed) {
            preview.trashed.push(Trashed {
                rows: rows.iter().map(|r| r.number).collect(),
                id: p.id,
                name: p.fields.name.clone(),
            });
            if !decisions.restore.contains(&p.id) {
                continue;
            }
        }
        let incoming = combine_rows(&rows);
        let Some(original) = &before else {
            preview.created += 1;
            changes.push(Change {
                before: None,
                after: incoming,
                restore: false,
                rows: rows.iter().map(|r| r.number).collect(),
            });
            continue;
        };
        let (mut after, differences) = supplement(original, &incoming, first.number);
        let supplemented = &after != original;
        preview.additions.push(additions(original, &after, &rows));
        for d in &differences {
            if !decisions.take.iter().any(|choice| {
                choice.id == d.id
                    && choice.field == d.field
                    && choice.affiliation == d.affiliation
                    && choice.local == d.local
                    && choice.incoming == d.incoming
            }) {
                continue;
            }
            match d.field {
                Field::Name => {
                    after.fields.aliases.retain(|a| !same(a, &d.incoming));
                    append(
                        &mut after.fields.aliases,
                        std::slice::from_ref(&after.fields.name),
                    );
                    after.fields.name.clone_from(&d.incoming);
                }
                Field::Research => after.fields.research = Some(d.incoming.clone()),
                Field::SourceUrl => after.source_url = Some(d.incoming.clone()),
                Field::Note => after.note = Some(d.incoming.clone()),
                Field::Title => {
                    if let Some(a) = after.fields.affiliations.iter_mut().find(|a| {
                        let mut a = (*a).clone();
                        a.title = None;
                        Some(&a) == d.affiliation.as_ref()
                    }) {
                        a.title = Some(d.incoming.clone());
                    }
                }
                _ => unreachable!("only scalar conflicts have decisions"),
            }
        }
        if supplemented {
            preview.supplemented += 1;
        } else if differences.is_empty() {
            preview.unchanged += 1;
        }
        preview.differences.extend(differences);
        changes.push(Change {
            before,
            after,
            restore,
            rows: rows.iter().map(|r| r.number).collect(),
        });
    }
    preview.updated = changes
        .iter()
        .filter(|c| c.changed() && c.before.is_some())
        .count() as u32;
    preview.errors.sort_by_key(|e| e.row);
    Ok((preview, changes))
}

pub async fn apply(
    conn: &mut SqliteConnection,
    changes: Vec<Change>,
    now: i64,
) -> Result<(Imported, BTreeMap<u32, i64>), sqlx::Error> {
    let mut result = Imported::default();
    let mut accepted = BTreeMap::new();
    for change in changes {
        if !change.changed() {
            for row in change.rows {
                accepted.insert(row, change.after.id);
            }
            continue;
        }
        let p = change.after;
        let id = if change.before.is_some() {
            if change.restore {
                trash::restore(&mut *conn, &[p.id], now).await?;
            }
            update(&mut *conn, p.id, &p.fields, now)
                .await
                .map_err(save_error)?;
            result.updated += 1;
            p.id
        } else {
            result.created += 1;
            create(&mut *conn, &p.fields, now)
                .await
                .map_err(save_error)?
        };
        sqlx::query!(
            "UPDATE mentor SET source_url = ? WHERE id = ?",
            p.source_url,
            id
        )
        .execute(&mut *conn)
        .await?;
        sqlx::query!(
            "UPDATE mentor_detail SET papers = ?, note = ? WHERE mentor_id = ?",
            Json(&p.papers),
            p.note,
            id
        )
        .execute(&mut *conn)
        .await?;
        for row in change.rows {
            accepted.insert(row, id);
        }
        for name in &p.tags {
            crate::tags::add_by_name(&mut *conn, id, name).await?;
        }
    }
    Ok((result, accepted))
}
fn save_error(error: SaveError) -> sqlx::Error {
    match error {
        SaveError::Db(e) => e,
        SaveError::EmailTaken { .. } => sqlx::Error::Protocol(error.to_string()),
    }
}

/// Combines duplicate page observations without deleting already collected facts.
pub fn add_candidate(
    fields: &mut crate::MentorFields,
    papers: &mut Vec<String>,
    incoming: &crate::MentorFields,
    incoming_papers: &[String],
) {
    add_missing(fields, papers, incoming, incoming_papers);
    if fields.research.is_none() {
        fields.research.clone_from(&incoming.research);
    }
    if !same(&fields.name, &incoming.name) {
        append(&mut fields.aliases, std::slice::from_ref(&incoming.name));
    }
}
