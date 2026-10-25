use crate::{EmailLocation, Material};

fn compact(text: &str) -> String {
    text.chars().filter(|c| !c.is_whitespace()).collect()
}

struct Occurrence<'a> {
    name: &'a str,
    unit: usize,
    start: usize,
    end: usize,
    leading: bool,
}

fn item_start(material: &Material, unit: usize, depth: usize) -> usize {
    let path = &material.units[unit].path;
    if path.len() == depth {
        return unit;
    }
    let child = &path[..depth + 1];
    let mut first = unit;
    while first > 0 && material.units[first - 1].path.starts_with(child) {
        first -= 1;
    }
    first
}

/// Checks one proposed address against all mentor names extracted from this page.
/// Names are compared ignoring whitespace; addresses must match parsed evidence.
pub fn owns_email(
    material: &Material,
    names: &[&str],
    target_names: &[&str],
    address: &str,
) -> bool {
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
                    let offset = match email.location {
                        EmailLocation::Text { start, .. } => start,
                        EmailLocation::Attribute { offset, .. } => offset,
                    };
                    (unit, compact(&block.text[..offset]).len())
                })
        })
        .collect();
    if evidence.is_empty() {
        return false;
    }
    let target_names: Vec<_> = target_names.iter().map(|name| compact(name)).collect();
    let is_target = |name: &str| target_names.iter().any(|target| target == name);
    let mut names: Vec<_> = names
        .iter()
        .map(|name| compact(name))
        .filter(|name| !name.is_empty())
        .collect();
    names.sort();
    names.dedup();
    let mut matches = Vec::new();
    for (unit, block) in material.units.iter().enumerate() {
        let text = compact(&block.text);
        let line_starts: Vec<_> = block
            .line_starts
            .iter()
            .map(|&start| compact(&block.text[..start]).len())
            .collect();
        for name in &names {
            for (start, matched) in text.match_indices(name.as_str()) {
                let end = start + matched.len();
                matches.push(Occurrence {
                    name,
                    unit,
                    start,
                    end,
                    leading: start == 0 || line_starts.contains(&start),
                });
            }
        }
    }
    let occurrences: Vec<_> = matches
        .iter()
        .filter(|found| {
            !matches.iter().any(|other| {
                found.unit == other.unit
                    && other.name.len() > found.name.len()
                    && other.start <= found.start
                    && found.end <= other.end
            })
        })
        .collect();
    occurrences
        .iter()
        .filter(|found| is_target(found.name) && found.leading)
        .any(|found| {
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
                    !is_target(other.name) && material.units[other.unit].path.starts_with(ancestor)
                });
                if !contains_other {
                    return true;
                }
                if found.unit == unit {
                    return offset >= found.start
                        && !occurrences.iter().any(|other| {
                            other.unit == unit
                                && !is_target(other.name)
                                && other.start >= found.start
                                && other.start <= offset
                        });
                }
                if material.units[unit].in_footer_or_nav {
                    return false;
                }
                let name_item = item_start(material, found.unit, common);
                let email_item = item_start(material, unit, common);
                let first_text =
                    (name_item..=found.unit).find(|&index| !material.units[index].text.is_empty());
                name_item < email_item
                    && found.start == 0
                    && first_text == Some(found.unit)
                    && !occurrences.iter().any(|other| {
                        let path = &material.units[other.unit].path;
                        if is_target(other.name) || !path.starts_with(ancestor) {
                            return false;
                        }
                        let item = item_start(material, other.unit, common);
                        name_item <= item && item <= email_item
                    })
            })
        })
}
