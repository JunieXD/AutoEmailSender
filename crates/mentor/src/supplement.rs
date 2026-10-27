//! Non-destructive profile additions shared by merging and table imports.

use crate::{Affiliation, MentorFields, place};

fn comparable_chars(value: &str) -> impl Iterator<Item = char> + '_ {
    let mut space = false;
    value.trim().chars().filter_map(move |c| {
        if c.is_whitespace() {
            if space {
                return None;
            }
            space = true;
            return Some(' ');
        }
        space = false;
        Some(match c {
            'Ａ'..='Ｚ' | 'ａ'..='ｚ' | '０'..='９' => (c as u32 - 0xfee0) as u8 as char,
            _ => c,
        })
    })
}

pub fn comparable(value: &str) -> String {
    comparable_chars(value).collect()
}

pub fn same(a: &str, b: &str) -> bool {
    a == b || comparable_chars(a).eq(comparable_chars(b))
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

pub fn affiliation_position(affiliations: &[Affiliation], incoming: &Affiliation) -> Option<usize> {
    let exact = affiliations.iter().position(|a| {
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
    exact.or_else(|| {
        affiliations
            .iter()
            .position(|a| same_affiliation(a, incoming))
    })
}

pub struct TitleDifference {
    pub position: usize,
    pub local: String,
    pub incoming: String,
}

pub fn add_missing(
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
        let position = affiliation_position(&fields.affiliations, incoming);
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

pub fn append(local: &mut Vec<String>, incoming: &[String]) {
    for value in incoming {
        if !local.iter().any(|v| same(v, value)) {
            local.push(value.clone());
        }
    }
}
