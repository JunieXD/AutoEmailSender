-- Contact events are shared by the live summary and statistics. Keep the two sources separate:
-- SQLite can push a mentor predicate into each simple view in the write triggers.
CREATE VIEW contact_email AS
SELECT mentor_id, kind, state, sent_at FROM email
WHERE state IN ('maybe_sent', 'sent', 'bounced');

CREATE VIEW contact_message AS
SELECT id, kind, sent_at, in_reply_to, read_at, outcome FROM message WHERE kind IN ('sent', 'reply');

-- Start each per-mentor lookup with its links; a global time index must not make writes scan mail for other mentors.
DROP VIEW mentor_contact_live;
CREATE VIEW mentor_contact_live AS
SELECT mentor_id,
       CASE WHEN last_reply_at IS NOT NULL THEN 'replied'
            WHEN last_bounced_at = last_out_at THEN 'bounced'
            ELSE 'contacted' END AS status,
       last_out_at, last_reply_at, contact_count,
       coalesce(last_unread_at > coalesce(last_out_at, 0), 0) AS unread, outcome
FROM (SELECT m.id AS mentor_id,
             (SELECT max(at) FROM (
                SELECT max(sent_at) AS at FROM contact_email
                WHERE mentor_id = m.id
                UNION ALL
                SELECT max(msg.sent_at) FROM message_mentor mm CROSS JOIN contact_message msg ON msg.id=mm.message_id
                WHERE mm.mentor_id = m.id AND msg.kind = 'sent')) AS last_out_at,
             (SELECT max(at) FROM (
                SELECT max(CASE WHEN state = 'bounced' THEN sent_at END) AS at FROM contact_email
                WHERE mentor_id = m.id
                UNION ALL
                SELECT max(msg.sent_at) FROM message_mentor mm CROSS JOIN contact_message msg ON msg.id=mm.message_id
                WHERE mm.mentor_id = m.id AND msg.kind = 'sent' AND mm.bounce_reason IS NOT NULL)) AS last_bounced_at,
             (SELECT count(*) FROM contact_email
              WHERE mentor_id = m.id AND kind = 'first')
             + (SELECT count(*) FROM message_mentor mm CROSS JOIN contact_message msg ON msg.id=mm.message_id
                WHERE mm.mentor_id = m.id AND msg.kind = 'sent' AND msg.in_reply_to IS NULL) AS contact_count,
             (SELECT max(msg.sent_at) FROM message_mentor mm CROSS JOIN contact_message msg ON msg.id=mm.message_id
              WHERE mm.mentor_id = m.id AND msg.kind = 'reply') AS last_reply_at,
             (SELECT max(msg.sent_at) FROM message_mentor mm CROSS JOIN contact_message msg ON msg.id=mm.message_id
              WHERE mm.mentor_id = m.id AND msg.kind = 'reply' AND msg.read_at IS NULL) AS last_unread_at,
             (SELECT msg.outcome FROM message_mentor mm CROSS JOIN contact_message msg ON msg.id=mm.message_id
              WHERE mm.mentor_id = m.id AND msg.kind = 'reply' AND msg.outcome IS NOT NULL
              ORDER BY msg.sent_at DESC, msg.id DESC LIMIT 1) AS outcome
      FROM mentor m)
WHERE last_out_at IS NOT NULL OR last_reply_at IS NOT NULL;


-- Trends select time intervals; the existing outgoing index starts with mentor_id.
CREATE INDEX email_stats_time ON email(sent_at, mentor_id, state)
 WHERE state IN ('maybe_sent', 'sent', 'bounced');
CREATE INDEX message_stats_time ON message(kind, sent_at);
-- Group schools and deduplicate mentors without visiting the wide affiliation rows.
CREATE INDEX mentor_stats_university ON mentor_affiliation(university, mentor_id)
 WHERE university IS NOT NULL;

CREATE VIEW mentor_contact_first AS
SELECT mentor_id, min(first_at) AS first_at, min(reply_at) AS reply_at FROM (
 SELECT mentor_id, min(sent_at) AS first_at, NULL AS reply_at FROM contact_email GROUP BY mentor_id
 UNION ALL
 SELECT mm.mentor_id, min(CASE WHEN msg.kind='sent' THEN msg.sent_at END), min(CASE WHEN msg.kind='reply' THEN msg.sent_at END)
 FROM message_mentor mm JOIN contact_message msg ON msg.id=mm.message_id GROUP BY mm.mentor_id
) GROUP BY mentor_id;
