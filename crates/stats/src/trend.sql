WITH trashed AS MATERIALIZED (SELECT id FROM mentor WHERE trashed_at IS NOT NULL), buckets AS (
 SELECT value->>'date' AS date, value->>'start' AS start, value->>'end' AS end FROM json_each(?1)
)
SELECT b.date AS "date!: String",
  ((SELECT count(*) FROM contact_email e WHERE e.mentor_id NOT IN (SELECT id FROM trashed) AND e.sent_at>=b.start AND e.sent_at<b.end)
  + (SELECT count(DISTINCT e.id) FROM contact_message e JOIN message_mentor mm ON mm.message_id=e.id WHERE e.kind='sent' AND mm.mentor_id NOT IN (SELECT id FROM trashed) AND e.sent_at>=b.start AND e.sent_at<b.end)) AS "sent!: i64",
  (SELECT count(DISTINCT e.id) FROM contact_message e JOIN message_mentor mm ON mm.message_id=e.id WHERE e.kind='reply' AND mm.mentor_id NOT IN (SELECT id FROM trashed) AND e.sent_at>=b.start AND e.sent_at<b.end) AS "replies!: i64"
FROM buckets b ORDER BY b.start
