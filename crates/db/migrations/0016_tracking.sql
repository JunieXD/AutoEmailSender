ALTER TABLE profile RENAME COLUMN open_tracking TO email_tracking;
-- The quota left and when the sponsorship ends, as last read from the server; and how far the
-- opens are fetched.
ALTER TABLE account ADD COLUMN tracking_left INTEGER NOT NULL DEFAULT 0
  CHECK (tracking_left >= 0);
ALTER TABLE account ADD COLUMN tracking_until INTEGER;
ALTER TABLE account ADD COLUMN tracking_revision INTEGER NOT NULL DEFAULT 0;
-- Since when synchronizing tracking has been failing, and the latest reason; cleared by the next
-- success.
ALTER TABLE account ADD COLUMN tracking_failed_at INTEGER;
ALTER TABLE account ADD COLUMN tracking_error TEXT
  CHECK ((tracking_error IS NULL) = (tracking_failed_at IS NULL));

-- Of an email handed over with a tracking image; tracking_id, open_count and the open times are in
-- 0001. The id is chosen in the handover's transaction and kept for every later attempt; the
-- account whose quota it uses alone registers it.
ALTER TABLE email ADD COLUMN tracking_state TEXT
  CHECK (tracking_state IN ('pending', 'tracked', 'no_quota', 'not_sponsored', 'expired'))
  CHECK ((tracking_state IS NULL) = (tracking_id IS NULL));
ALTER TABLE email ADD COLUMN tracking_account TEXT
  CHECK ((tracking_account IS NULL) = (tracking_id IS NULL));
-- When the id first went out; registering it is given up REGISTER_WITHIN later, resends or not.
ALTER TABLE email ADD COLUMN tracking_chosen_at INTEGER
  CHECK ((tracking_chosen_at IS NULL) = (tracking_id IS NULL));
-- When the server took it; it counts opens for 180 days from then.
ALTER TABLE email ADD COLUMN tracked_at INTEGER
  CHECK ((tracked_at IS NOT NULL) = (tracking_state IS 'tracked'));

-- An id that never left the computer goes with the attempt, so that a retry decides afresh whether
-- the quota covers it: a pending id is one that may be out. Writes therefore set sent_at to its
-- final value at once, never to NULL on the way to an earlier attempt's time.
CREATE TRIGGER email_au_tracking_unsent AFTER UPDATE OF sent_at ON email
WHEN NEW.sent_at IS NULL AND NEW.tracking_state = 'pending' BEGIN
  UPDATE email SET tracking_id = NULL, tracking_state = NULL, tracking_account = NULL,
    tracking_chosen_at = NULL
  WHERE id = NEW.id;
END;

-- Tracking belongs to the account, like cloud backup.
CREATE TRIGGER account_ad_tracking AFTER DELETE ON account BEGIN
  UPDATE profile SET email_tracking = 0;
END;

-- The contact summary says whether the latest letter out was opened.
ALTER TABLE mentor_contact ADD COLUMN opened INTEGER CHECK (opened IN (0, 1));
DROP VIEW mentor_contact_live;
CREATE VIEW mentor_contact_live AS
SELECT mentor_id,
       CASE WHEN last_reply_at IS NOT NULL THEN 'replied'
            WHEN last_bounced_at = last_out_at THEN 'bounced'
            ELSE 'contacted' END AS status,
       last_out_at, last_reply_at, contact_count,
       coalesce(last_unread_at > coalesce(last_out_at, 0), 0) AS unread, outcome,
       -- Of the latest letter out, when it went with a tracking image the server took; ties go
       -- to the app's letters, as for follow-ups.
       (SELECT CASE WHEN tracking_state = 'tracked' THEN coalesce(open_count, 0) > 0 END
        FROM email
        WHERE mentor_id = s.mentor_id AND state IN ('maybe_sent', 'sent', 'bounced')
          AND sent_at = s.last_out_at
        ORDER BY id DESC LIMIT 1) AS opened
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
      FROM mentor m) s
WHERE last_out_at IS NOT NULL OR last_reply_at IS NOT NULL;

CREATE TRIGGER email_au_tracking AFTER UPDATE OF tracking_state, open_count ON email BEGIN
  DELETE FROM mentor_contact WHERE mentor_id = NEW.mentor_id;
  INSERT INTO mentor_contact SELECT * FROM mentor_contact_live WHERE mentor_id = NEW.mentor_id;
END;
