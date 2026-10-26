//! Filters shared by the home list and table export, and the choices shown in the filter menus.

use std::num::NonZeroU32;

use serde::{Deserialize, Serialize};
use sqlx::SqlitePool;
use ts_rs::TS;

use crate::{ContactStatus, ReplyOutcomeFilter};

#[derive(Debug, Clone, Copy, Deserialize, TS)]
#[serde(tag = "kind", rename_all = "camelCase")]
pub enum MatchFilter {
    AtLeast { score: u8 },
    Unanalyzed,
}

#[derive(Debug, Clone, Default, Deserialize, TS)]
#[serde(rename_all = "camelCase")]
pub struct MentorFilter {
    #[ts(optional)]
    pub status: Option<ContactStatus>,
    /// Applied only with status = replied.
    #[ts(optional)]
    pub reply_outcome: Option<ReplyOutcomeFilter>,
    #[ts(optional)]
    pub keyword: Option<String>,
    #[ts(optional)]
    pub unanswered_days: Option<NonZeroU32>,
    /// All scores when absent; an unfinished analysis can still have a previous result.
    #[ts(optional)]
    pub matching: Option<MatchFilter>,
    /// Empty lists mean no restriction; None selects mentors with no value in any affiliation.
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub universities: Vec<Option<String>>,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub schools: Vec<Option<String>>,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub departments: Vec<Option<String>>,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub titles: Vec<Option<String>>,
    /// None selects mentors with no tags.
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub tags: Vec<Option<i64>>,
}

impl MentorFilter {
    /// Validates and trims user input at the API boundary.
    pub fn check(&mut self) -> Result<(), String> {
        if let Some(MatchFilter::AtLeast { score }) = self.matching
            && !(1..=100).contains(&score)
        {
            return Err("匹配度分数须为 1–100".into());
        }
        for values in [
            &mut self.universities,
            &mut self.schools,
            &mut self.departments,
            &mut self.titles,
        ] {
            for value in values.iter_mut().flatten() {
                *value = value.trim().to_owned();
                if value.is_empty() {
                    return Err("筛选值不能为空，请选择“未填写”".into());
                }
            }
        }
        Ok(())
    }

    pub(crate) fn unanswered_before(&self, now: i64) -> Option<i64> {
        self.unanswered_days
            .map(|days| now - i64::from(days.get()) * 86_400_000)
    }

    pub(crate) fn match_score(&self) -> (Option<u8>, bool) {
        match self.matching {
            Some(MatchFilter::AtLeast { score }) => (Some(score), false),
            Some(MatchFilter::Unanalyzed) => (None, true),
            None => (None, false),
        }
    }
}

#[derive(Debug, Serialize, TS)]
pub struct FilterValue {
    pub value: String,
    pub count: i64,
}

#[derive(Debug, Default, Serialize, TS)]
pub struct FilterValues {
    pub values: Vec<FilterValue>,
    pub missing: i64,
}

#[derive(Debug, Serialize, TS)]
pub struct FilterTag {
    pub id: i64,
    pub name: String,
    pub count: i64,
}

#[derive(Debug, Default, Serialize, TS)]
pub struct MentorFilterOptions {
    pub universities: FilterValues,
    pub schools: FilterValues,
    pub departments: FilterValues,
    pub titles: FilterValues,
    pub tags: Vec<FilterTag>,
    pub untagged: i64,
}

#[derive(Debug, sqlx::Type)]
#[sqlx(rename_all = "snake_case")]
enum AffiliationField {
    University,
    School,
    Department,
    Title,
}

/// Counts mentors rather than affiliations, using one snapshot for all menus.
pub async fn options(db: &SqlitePool) -> Result<MentorFilterOptions, sqlx::Error> {
    let mut tx = db.begin().await?;
    let mut options = MentorFilterOptions::default();
    // Grouping expanded fields needs a temporary B-tree; separate aggregates retain index order.
    let values = sqlx::query!(
        r#"WITH trash AS MATERIALIZED (
            SELECT id FROM mentor WHERE trashed_at IS NOT NULL
        ), active_count AS MATERIALIZED (
            SELECT count(*) AS total FROM mentor WHERE trashed_at IS NULL
        )
        SELECT 'university' AS "field!: AffiliationField", university AS "value?",
            count(DISTINCT mentor_id) AS "count!: i64"
        FROM mentor_affiliation
        WHERE university IS NOT NULL AND mentor_id NOT IN (SELECT id FROM trash)
        GROUP BY university
        UNION ALL
        SELECT 'university', NULL,
            (SELECT total FROM active_count) - count(DISTINCT mentor_id)
        FROM mentor_affiliation
        WHERE university IS NOT NULL AND mentor_id NOT IN (SELECT id FROM trash)
        UNION ALL
        SELECT 'school', school,
            count(DISTINCT mentor_id)
        FROM mentor_affiliation
        WHERE school IS NOT NULL AND mentor_id NOT IN (SELECT id FROM trash)
        GROUP BY school
        UNION ALL
        SELECT 'school', NULL,
            (SELECT total FROM active_count) - count(DISTINCT mentor_id)
        FROM mentor_affiliation
        WHERE school IS NOT NULL AND mentor_id NOT IN (SELECT id FROM trash)
        UNION ALL
        SELECT 'department', department,
            count(DISTINCT mentor_id)
        FROM mentor_affiliation
        WHERE department IS NOT NULL AND mentor_id NOT IN (SELECT id FROM trash)
        GROUP BY department
        UNION ALL
        SELECT 'department', NULL,
            (SELECT total FROM active_count) - count(DISTINCT mentor_id)
        FROM mentor_affiliation
        WHERE department IS NOT NULL AND mentor_id NOT IN (SELECT id FROM trash)
        UNION ALL
        SELECT 'title', title,
            count(DISTINCT mentor_id)
        FROM mentor_affiliation
        WHERE title IS NOT NULL AND mentor_id NOT IN (SELECT id FROM trash)
        GROUP BY title
        UNION ALL
        SELECT 'title', NULL,
            (SELECT total FROM active_count) - count(DISTINCT mentor_id)
        FROM mentor_affiliation
        WHERE title IS NOT NULL AND mentor_id NOT IN (SELECT id FROM trash)
        ORDER BY 3 DESC, 2"#,
    )
    .fetch_all(&mut *tx)
    .await?;
    for row in values {
        let field = match row.field {
            AffiliationField::University => &mut options.universities,
            AffiliationField::School => &mut options.schools,
            AffiliationField::Department => &mut options.departments,
            AffiliationField::Title => &mut options.titles,
        };
        match row.value {
            Some(value) => field.values.push(FilterValue {
                value,
                count: row.count,
            }),
            None => field.missing = row.count,
        }
    }
    options.untagged = sqlx::query_scalar!(
        r#"SELECT (SELECT count(*) FROM mentor WHERE trashed_at IS NULL)
            - count(DISTINCT mentor_id) AS "count!: i64"
        FROM mentor_tag
        WHERE mentor_id NOT IN (SELECT id FROM mentor WHERE trashed_at IS NOT NULL)"#,
    )
    .fetch_one(&mut *tx)
    .await?;
    options.tags = sqlx::query_as!(
        FilterTag,
        r#"SELECT t.id, t.name, count(mt.mentor_id) AS "count!: i64"
        FROM tag t
        LEFT JOIN mentor_tag mt ON mt.tag_id = t.id
            AND mt.mentor_id NOT IN (SELECT id FROM mentor WHERE trashed_at IS NOT NULL)
        GROUP BY t.id
        ORDER BY count(mt.mentor_id) DESC, t.name"#,
    )
    .fetch_all(&mut *tx)
    .await?;
    Ok(options)
}
