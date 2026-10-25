WITH cohort AS MATERIALIZED (
 SELECT o.mentor_id, o.first_at, o.reply_at FROM mentor_contact_first o JOIN mentor m ON m.id=o.mentor_id
 WHERE m.trashed_at IS NULL AND (?1 IS NULL OR o.first_at>=?1) AND o.first_at<?2
) , schools AS MATERIALIZED (
 SELECT university, count(*) AS contacted, count(reply_at) AS replied
 FROM (SELECT DISTINCT c.mentor_id,a.university,c.reply_at FROM cohort c
 CROSS JOIN mentor_affiliation a ON a.mentor_id=c.mentor_id WHERE a.university IS NOT NULL) GROUP BY university
)
SELECT (SELECT json_group_array(json_object('university',university,'contacted',contacted,'replied',replied))
    FROM (SELECT * FROM schools ORDER BY contacted DESC, university LIMIT ?3 OFFSET ?4)) AS "schools!: String",
  (SELECT count(*) FROM schools) AS "school_count!: i64"