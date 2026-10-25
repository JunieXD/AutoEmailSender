WITH trashed AS MATERIALIZED (SELECT id FROM mentor WHERE trashed_at IS NOT NULL), all_schools AS MATERIALIZED (
 SELECT university, count(DISTINCT mentor_id) AS n FROM mentor_affiliation WHERE university IS NOT NULL GROUP BY university
), trash_schools AS MATERIALIZED (
 SELECT university, count(DISTINCT mentor_id) AS n FROM mentor_affiliation WHERE university IS NOT NULL AND mentor_id IN (SELECT id FROM trashed) GROUP BY university
), schools AS MATERIALIZED (
 SELECT a.university, a.n-coalesce(t.n,0) AS n FROM all_schools a LEFT JOIN trash_schools t ON t.university=a.university WHERE a.n>coalesce(t.n,0)
), top_schools AS MATERIALIZED (SELECT * FROM schools ORDER BY n DESC, university LIMIT 10)
SELECT (SELECT json_group_array(json_object('university',university,'count',n)) FROM top_schools) AS "schools!: String",
 ((SELECT coalesce(sum(n),0) FROM schools)-(SELECT coalesce(sum(n),0) FROM top_schools)) AS "other_schools!: i64"
