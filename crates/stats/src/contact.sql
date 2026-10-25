WITH cohort AS MATERIALIZED (
 SELECT o.mentor_id, o.first_at, o.reply_at FROM mentor_contact_first o JOIN mentor m ON m.id=o.mentor_id
 WHERE m.trashed_at IS NULL AND (?1 IS NULL OR o.first_at>=?1) AND o.first_at<?2
), waits AS MATERIALIZED (
  SELECT (reply_at-first_at)/86400000.0 AS days FROM cohort WHERE reply_at >= first_at
), ranked AS (
  SELECT days, row_number() OVER (ORDER BY days) AS n, count(*) OVER () AS total FROM waits
)
SELECT (SELECT count(*) FROM cohort) AS "contacted!: i64",
  (SELECT count(reply_at) FROM cohort) AS "replied!: i64",
  (SELECT avg(days) FROM ranked WHERE n IN ((total+1)/2, (total+2)/2)) AS "median_days?: f64",
  (SELECT count(*) FROM (
    SELECT e.mentor_id FROM contact_email e JOIN mentor m ON m.id=e.mentor_id
    WHERE e.state='bounced' AND m.trashed_at IS NULL AND (?1 IS NULL OR e.sent_at>=?1) AND e.sent_at<?2
    UNION ALL
    SELECT DISTINCT e.id FROM contact_message e JOIN message_mentor mm ON mm.message_id=e.id JOIN mentor m ON m.id=mm.mentor_id
    WHERE e.kind='sent' AND mm.bounce_reason IS NOT NULL AND m.trashed_at IS NULL AND (?1 IS NULL OR e.sent_at>=?1) AND e.sent_at<?2
  )) AS "bounced!: i64",
  (SELECT json_array(count(CASE WHEN days<=1 THEN 1 END), count(CASE WHEN days>1 AND days<=3 THEN 1 END),
    count(CASE WHEN days>3 AND days<=7 THEN 1 END), count(CASE WHEN days>7 AND days<=14 THEN 1 END),
    count(CASE WHEN days>14 THEN 1 END)) FROM waits) AS "waiting!: String"
