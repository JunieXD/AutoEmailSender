WITH trashed AS MATERIALIZED (SELECT id FROM mentor WHERE trashed_at IS NOT NULL), completeness AS MATERIALIZED (
 SELECT count(CASE WHEN research IS NOT NULL THEN 1 END) AS research,
 count(CASE WHEN json_array_length(papers)>0 THEN 1 END) AS papers FROM mentor_detail
), trash_completeness AS MATERIALIZED (
 SELECT count(CASE WHEN research IS NOT NULL THEN 1 END) AS research,
 count(CASE WHEN json_array_length(papers)>0 THEN 1 END) AS papers FROM mentor_detail WHERE mentor_id IN (SELECT id FROM trashed)
)
SELECT ((SELECT count(DISTINCT mentor_id) FROM mentor_email)-(SELECT count(DISTINCT mentor_id) FROM mentor_email WHERE mentor_id IN (SELECT id FROM trashed))) AS "email!: i64",
 ((SELECT research FROM completeness)-(SELECT research FROM trash_completeness)) AS "research!: i64",
 ((SELECT papers FROM completeness)-(SELECT papers FROM trash_completeness)) AS "papers!: i64",
 ((SELECT count(DISTINCT mentor_id) FROM mentor_homepage)-(SELECT count(DISTINCT mentor_id) FROM mentor_homepage WHERE mentor_id IN (SELECT id FROM trashed))) AS "homepage!: i64"
