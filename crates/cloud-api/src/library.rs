//! Mentor library protocol. A removed change cannot contain a profile.

use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Status {
    Active,
    Retired,
    Departed,
    Deceased,
    Removed,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct Org {
    pub id: String,
    pub parent_id: Option<String>,
    pub name: String,
    pub count: u32,
}

#[derive(Debug, Serialize, Deserialize)]
pub struct Orgs {
    pub orgs: Vec<Org>,
}

#[derive(Debug, Default, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct Unit {
    pub university: Option<String>,
    pub school: Option<String>,
    pub department: Option<String>,
    pub title: Option<String>,
}

#[derive(Debug, Default, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ListRequest {
    pub org: Option<String>,
    pub q: Option<String>,
    pub page: u32,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct Summary {
    pub id: String,
    pub name: String,
    pub aliases: Vec<String>,
    #[serde(flatten)]
    pub unit: Unit,
    pub research: Option<String>,
    pub email_count: u32,
    pub fetched: bool,
}

#[derive(Debug, Serialize, Deserialize)]
pub struct List {
    pub total: u32,
    pub mentors: Vec<Summary>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct Preview {
    #[serde(flatten)]
    pub summary: Summary,
    pub units: Vec<Unit>,
    pub papers: Vec<String>,
    pub homepages: Vec<String>,
    pub source_url: Option<String>,
    pub verified_at: Option<i64>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct Record {
    #[serde(flatten)]
    pub preview: Preview,
    pub emails: Vec<String>,
    pub revision: i64,
}

#[derive(Debug, Serialize, Deserialize)]
pub struct Quota {
    pub remaining: Option<u32>,
}

#[derive(Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct FetchRequest {
    pub ids: Vec<String>,
}

#[derive(Debug, Serialize, Deserialize)]
pub struct Fetched {
    pub records: Vec<Record>,
    pub missing: Vec<String>,
    pub remaining: Option<u32>,
}

#[derive(Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ChangesRequest {
    pub after: i64,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(tag = "status", content = "record", rename_all = "snake_case")]
pub enum ChangeData {
    Active(Record),
    Retired(Record),
    Departed(Record),
    Deceased(Record),
    Removed(()),
}

impl ChangeData {
    pub fn status(&self) -> Status {
        match self {
            Self::Active(_) => Status::Active,
            Self::Retired(_) => Status::Retired,
            Self::Departed(_) => Status::Departed,
            Self::Deceased(_) => Status::Deceased,
            Self::Removed(()) => Status::Removed,
        }
    }

    pub fn record(&self) -> Option<&Record> {
        match self {
            Self::Active(record)
            | Self::Retired(record)
            | Self::Departed(record)
            | Self::Deceased(record) => Some(record),
            Self::Removed(()) => None,
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct Change {
    pub id: String,
    pub revision: i64,
    #[serde(flatten)]
    pub data: ChangeData,
}

#[derive(Debug, Serialize, Deserialize)]
pub struct Changes {
    pub changes: Vec<Change>,
    pub revision: i64,
    pub more: bool,
}
