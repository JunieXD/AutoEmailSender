use crate::{EmailLocation, Material};

fn compact(text: &str) -> String {
    text.chars().filter(|c| !c.is_whitespace()).collect()
}

struct Occurrence<'a> {
    name: &'a str,
    unit: usize,
    offset: usize,
}

impl Occurrence<'_> {
    fn position(&self) -> (usize, usize) {
        (self.unit, self.offset)
    }
}

/// Checks one proposed address against all mentor names extracted from this page.
/// Names are compared ignoring whitespace; addresses must match parsed evidence.
pub fn owns_email(material: &Material, names: &[&str], name: &str, address: &str) -> bool {
    let evidence: Vec<_> = material
        .units
        .iter()
        .enumerate()
        .flat_map(|(unit, block)| {
            block
                .emails
                .iter()
                .filter(|email| email.address.eq_ignore_ascii_case(address))
                .map(move |email| {
                    (
                        unit,
                        match email.location {
                            EmailLocation::Text { start, .. } => start,
                            EmailLocation::Attribute { offset, .. } => offset,
                        },
                    )
                })
        })
        .collect();
    if evidence.is_empty() {
        return false;
    }
    let name = compact(name);
    let mut names: Vec<_> = names
        .iter()
        .map(|name| compact(name))
        .filter(|name| !name.is_empty())
        .collect();
    names.sort();
    names.dedup();
    let mut occurrences = Vec::new();
    for (unit, block) in material.units.iter().enumerate() {
        let text = compact(&block.text);
        let positions: Vec<_> = block
            .text
            .char_indices()
            .filter(|(_, c)| !c.is_whitespace())
            .flat_map(|(index, c)| std::iter::repeat_n(index, c.len_utf8()))
            .collect();
        for name in &names {
            for (start, _) in text.match_indices(name.as_str()) {
                occurrences.push(Occurrence {
                    name,
                    unit,
                    offset: positions[start],
                });
            }
        }
    }
    occurrences.sort_by_key(Occurrence::position);
    occurrences
        .iter()
        .enumerate()
        .filter(|(_, found)| found.name == name)
        .any(|(index, found)| {
            evidence.iter().any(|&(unit, offset)| {
                let name_path = &material.units[found.unit].path;
                let email_path = &material.units[unit].path;
                let common = name_path
                    .iter()
                    .zip(email_path)
                    .take_while(|(a, b)| a == b)
                    .count();
                let ancestor = &name_path[..common];
                let contains_other = occurrences.iter().any(|other| {
                    other.name != name && material.units[other.unit].path.starts_with(ancestor)
                });
                !contains_other
                    || ((unit, offset) >= found.position()
                        && occurrences
                            .get(index + 1)
                            .is_none_or(|next| (unit, offset) < next.position()))
            })
        })
}
