use crate::Material;

fn compact(text: &str) -> String {
    text.chars().filter(|c| !c.is_whitespace()).collect()
}

struct Occurrence<'a> {
    name: &'a str,
    unit: usize,
}

/// Checks one proposed address against all mentor names extracted from this page.
/// Names are compared ignoring whitespace; addresses must match parsed evidence.
pub fn owns_email(material: &Material, names: &[&str], name: &str, address: &str) -> bool {
    let evidence: Vec<_> = material
        .units
        .iter()
        .enumerate()
        .filter_map(|(unit, block)| {
            block
                .emails
                .iter()
                .any(|email| email.address.eq_ignore_ascii_case(address))
                .then_some(unit)
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
        for name in &names {
            if text.contains(name.as_str()) {
                occurrences.push(Occurrence { name, unit });
            }
        }
    }
    occurrences
        .iter()
        .filter(|found| found.name == name)
        .any(|found| {
            evidence.iter().any(|&unit| {
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
                if !contains_other {
                    return true;
                }
                let (Some(&name_child), Some(&email_child)) =
                    (name_path.get(common), email_path.get(common))
                else {
                    return false;
                };
                name_child < email_child
                    && !occurrences.iter().any(|other| {
                        let path = &material.units[other.unit].path;
                        if !path.starts_with(ancestor) {
                            return false;
                        }
                        path.get(common).is_some_and(|&child| {
                            (child == name_child && other.name != name)
                                || (name_child < child && child <= email_child)
                        })
                    })
            })
        })
}
