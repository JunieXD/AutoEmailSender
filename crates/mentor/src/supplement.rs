//! Non-destructive profile additions shared by merging and table imports.

use crate::{Affiliation, MentorFields, place};

pub(crate) fn comparable(value: &str) -> String {
    value.split_whitespace().collect::<Vec<_>>().join(" ")
}

pub(crate) fn same(a: &str, b: &str) -> bool {
    comparable(a) == comparable(b)
}

pub(crate) fn same_affiliation(a: &Affiliation, b: &Affiliation) -> bool {
    [
        (&a.university, &b.university),
        (&a.school, &b.school),
        (&a.department, &b.department),
    ]
    .into_iter()
    .all(|(a, b)| match (a, b) {
        (Some(a), Some(b)) => same(a, b),
        _ => true,
    })
}

pub(crate) struct TitleDifference {
    pub position: usize,
    pub local: String,
    pub incoming: String,
}

pub(crate) fn add_missing(
    fields: &mut MentorFields,
    papers: &mut Vec<String>,
    incoming: &MentorFields,
    incoming_papers: &[String],
) -> Vec<TitleDifference> {
    append(&mut fields.aliases, &incoming.aliases);
    fields.aliases.retain(|a| !same(a, &fields.name));
    for address in &incoming.emails {
        if !fields
            .emails
            .iter()
            .any(|a| a.eq_ignore_ascii_case(address))
        {
            fields
                .emails
                .insert(place(&fields.emails, address), address.clone());
        }
    }
    append(&mut fields.homepages, &incoming.homepages);
    append(papers, incoming_papers);
    let mut differences = Vec::new();
    for incoming in &incoming.affiliations {
        let exact = fields.affiliations.iter().position(|a| {
            [
                (&a.university, &incoming.university),
                (&a.school, &incoming.school),
                (&a.department, &incoming.department),
            ]
            .into_iter()
            .all(|(a, b)| match (a, b) {
                (Some(a), Some(b)) => same(a, b),
                (None, None) => true,
                _ => false,
            })
        });
        let position = exact.or_else(|| {
            fields
                .affiliations
                .iter()
                .position(|a| same_affiliation(a, incoming))
        });
        if let Some(position) = position {
            let local = &mut fields.affiliations[position];
            if let (Some(a), Some(b)) = (&local.title, &incoming.title)
                && !same(a, b)
            {
                differences.push(TitleDifference {
                    position,
                    local: a.clone(),
                    incoming: b.clone(),
                });
            }
            for (a, b) in [
                (&mut local.university, &incoming.university),
                (&mut local.school, &incoming.school),
                (&mut local.department, &incoming.department),
                (&mut local.title, &incoming.title),
            ] {
                if a.is_none() {
                    *a = b.clone();
                }
            }
        } else {
            fields.affiliations.push(incoming.clone());
        }
    }
    differences
}

pub(crate) fn append(local: &mut Vec<String>, incoming: &[String]) {
    for value in incoming {
        if !local.iter().any(|v| same(v, value)) {
            local.push(value.clone());
        }
    }
}
