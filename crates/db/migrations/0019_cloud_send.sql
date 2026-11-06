-- no-transaction
-- Cloud sending (docs/data-model.md#联系). An email handed to the official server keeps its latest
-- time and may carry a cancel reason, which the email table's checks did not allow, so the table
-- is rebuilt (https://sqlite.org/lang_altertable.html#otheralter): with foreign keys off, so that
-- dropping the old table neither deletes nor clears what refers to it, and with the legacy rename,
-- so that the views and triggers naming the table are kept as they are and find the new one.
PRAGMA foreign_keys = OFF;
BEGIN;

CREATE TABLE email_new (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  mentor_id INTEGER NOT NULL REFERENCES mentor(id) ON DELETE CASCADE,
  task_id INTEGER REFERENCES task(id) ON DELETE SET NULL,
  kind TEXT NOT NULL CHECK (kind IN ('first', 'follow_up', 'reply')),
  reply_to TEXT,               -- Message-ID this email replies to
  refs TEXT,
  resend_of INTEGER UNIQUE REFERENCES email(id) ON DELETE SET NULL, -- the bounced email it sends again
  state TEXT NOT NULL CHECK (state IN ('generating', 'generation_failed', 'review', 'approved',
    'cloud', 'sending', 'maybe_sent', 'sent', 'bounced', 'failed', 'canceled')),
  generation_claim INTEGER NOT NULL DEFAULT 0,
  generation_queued_at INTEGER,
  generated_at INTEGER,
  planned_at INTEGER,
  latest_at INTEGER,            -- only an approved or cloud scheduled email has a deadline
  approval_order INTEGER,
  manual_schedule INTEGER NOT NULL DEFAULT 0 CHECK (manual_schedule IN (0,1)),
  mailbox_id INTEGER REFERENCES mailbox(id) ON DELETE SET NULL,
  from_address TEXT,
  to_address TEXT COLLATE NOCASE, -- the mentor's address it was last handed over for
  model TEXT,
  message_id TEXT UNIQUE,
  copy_message_id TEXT UNIQUE, -- of its copy found in the sent folder, which the provider may change
  sent_at INTEGER,             -- handed to SMTP (sending/maybe_sent, kept while waiting to be sent
                               -- again) or accepted (sent/bounced)
  error TEXT,
  cancel_reason TEXT,
  -- Handed to the official server: the id it knows the email by, the account it belongs to, and
  -- when it is to go, from mailbox_id. Uploading until the server has it.
  cloud_id TEXT UNIQUE,
  cloud_account TEXT,
  cloud_at INTEGER,
  cloud_uploading INTEGER NOT NULL DEFAULT 0 CHECK (cloud_uploading IN (0, 1)),
  -- The official server handed it to the mail server, or tried to.
  by_cloud INTEGER NOT NULL DEFAULT 0 CHECK (by_cloud IN (0, 1)),
  tracking_id TEXT UNIQUE,
  tracking_state TEXT
    CHECK (tracking_state IN ('pending', 'tracked', 'no_quota', 'not_sponsored', 'expired')),
  tracking_account TEXT,
  tracking_chosen_at INTEGER,
  tracked_at INTEGER,
  open_count INTEGER,
  first_opened_at INTEGER,
  last_opened_at INTEGER,
  fact_check TEXT CHECK (fact_check IS NULL OR json_valid(fact_check)),
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL,
  subject TEXT,
  body_md TEXT,
  html TEXT,
  text TEXT,
  CHECK ((kind = 'first') = (reply_to IS NULL)),
  CHECK (state NOT IN ('approved', 'cloud', 'sending', 'maybe_sent', 'sent', 'bounced', 'failed')
         OR (subject IS NOT NULL AND body_md IS NOT NULL AND html IS NOT NULL AND text IS NOT NULL)),
  CHECK (state NOT IN ('cloud', 'sending', 'maybe_sent', 'sent', 'bounced', 'failed') OR planned_at IS NOT NULL),
  CHECK (state <> 'approved' OR planned_at IS NOT NULL OR task_id IS NOT NULL),
  CHECK (latest_at IS NULL
         OR (state IN ('approved', 'cloud') AND planned_at IS NOT NULL AND latest_at > planned_at)),
  CHECK (state NOT IN ('sending', 'maybe_sent', 'sent', 'bounced')
         OR (message_id IS NOT NULL AND sent_at IS NOT NULL AND from_address IS NOT NULL)),
  -- An email to a mentor with no address fails before going out.
  CHECK (state NOT IN ('maybe_sent', 'sent', 'bounced') OR to_address IS NOT NULL),
  CHECK (state NOT IN ('generation_failed', 'maybe_sent', 'failed', 'bounced') OR error IS NOT NULL),
  CHECK (cancel_reason IS NULL OR state IN ('cloud', 'sending', 'maybe_sent', 'canceled')),
  CHECK (state <> 'canceled' OR cancel_reason IS NOT NULL),
  -- A first letter that never went before, with what the server sends it as.
  CHECK ((state = 'cloud') = (cloud_id IS NOT NULL)),
  CHECK (state <> 'cloud'
         OR (kind = 'first' AND cloud_account IS NOT NULL AND cloud_at IS NOT NULL
           AND latest_at IS NOT NULL AND mailbox_id IS NOT NULL AND message_id IS NOT NULL
           AND from_address IS NOT NULL AND to_address IS NOT NULL AND sent_at IS NULL)),
  CHECK (state = 'cloud' OR (cloud_account IS NULL AND cloud_at IS NULL AND cloud_uploading = 0)),
  CHECK ((tracking_state IS NULL) = (tracking_id IS NULL)),
  CHECK ((tracking_account IS NULL) = (tracking_id IS NULL)),
  CHECK ((tracking_chosen_at IS NULL) = (tracking_id IS NULL)),
  CHECK ((tracked_at IS NOT NULL) = (tracking_state IS 'tracked'))
) STRICT;

INSERT INTO email_new (id, mentor_id, task_id, kind, reply_to, refs, resend_of, state,
    generation_claim, generation_queued_at, generated_at, planned_at, latest_at, approval_order,
    manual_schedule, mailbox_id, from_address, to_address, model, message_id, copy_message_id,
    sent_at, error, cancel_reason, tracking_id, tracking_state, tracking_account,
    tracking_chosen_at, tracked_at, open_count, first_opened_at, last_opened_at, fact_check,
    created_at, updated_at, subject, body_md, html, text)
  SELECT id, mentor_id, task_id, kind, reply_to, refs, resend_of, state,
    generation_claim, generation_queued_at, generated_at, planned_at, latest_at, approval_order,
    manual_schedule, mailbox_id, from_address, to_address, model, message_id, copy_message_id,
    sent_at, error, cancel_reason, tracking_id, tracking_state, tracking_account,
    tracking_chosen_at, tracked_at, open_count, first_opened_at, last_opened_at, fact_check,
    created_at, updated_at, subject, body_md, html, text
  FROM email;
DROP TABLE email;
PRAGMA legacy_alter_table = ON;
ALTER TABLE email_new RENAME TO email;
PRAGMA legacy_alter_table = OFF;

CREATE INDEX email_out ON email (mentor_id, sent_at, kind)
  WHERE state IN ('maybe_sent', 'sent', 'bounced');
CREATE INDEX email_queue ON email (planned_at, id, latest_at) WHERE state = 'approved';
CREATE INDEX email_deadline ON email (latest_at, task_id) WHERE state = 'approved';
CREATE INDEX email_history ON email (sent_at DESC, id DESC) WHERE state IN ('sent', 'bounced');
CREATE INDEX email_mentor ON email (mentor_id, state);
-- What each mailbox has sent lately, and is to send from the official server, for its daily limit
-- and minimum interval.
CREATE INDEX email_mailbox_out ON email (mailbox_id, sent_at)
  WHERE state IN ('sending', 'maybe_sent', 'sent', 'bounced');
CREATE INDEX email_mailbox_cloud ON email (mailbox_id, cloud_at) WHERE state = 'cloud';
CREATE INDEX email_task ON email (task_id, state, approval_order, planned_at);
CREATE UNIQUE INDEX email_task_mentor ON email (mentor_id, task_id) WHERE task_id IS NOT NULL;
CREATE INDEX email_generation_queue ON email (generation_queued_at, id) WHERE state = 'generating';
CREATE INDEX email_attention ON email (state) WHERE state IN ('generation_failed', 'failed', 'maybe_sent');
CREATE UNIQUE INDEX email_reply_draft ON email (mentor_id) WHERE kind = 'reply' AND state = 'review';
CREATE UNIQUE INDEX email_contact_draft ON email (mentor_id)
  WHERE kind IN ('first', 'follow_up') AND task_id IS NULL AND state = 'review';
CREATE INDEX email_stats_time ON email(sent_at, mentor_id, state)
  WHERE state IN ('maybe_sent', 'sent', 'bounced');
CREATE INDEX email_generated_at ON email(generated_at, task_id)
  WHERE generated_at IS NOT NULL;

-- Contact summary: the one place derived data is stored. Only these triggers write it, recomputing
-- the affected mentors from mentor_contact_live; a test checks the two stay equal.
CREATE TRIGGER email_ai AFTER INSERT ON email BEGIN
  DELETE FROM mentor_contact WHERE mentor_id = NEW.mentor_id;
  INSERT INTO mentor_contact SELECT * FROM mentor_contact_live WHERE mentor_id = NEW.mentor_id;
END;

CREATE TRIGGER email_ad AFTER DELETE ON email BEGIN
  DELETE FROM mentor_contact WHERE mentor_id = OLD.mentor_id;
  INSERT INTO mentor_contact SELECT * FROM mentor_contact_live WHERE mentor_id = OLD.mentor_id;
END;

CREATE TRIGGER email_au AFTER UPDATE OF mentor_id, kind, state, sent_at ON email BEGIN
  DELETE FROM mentor_contact WHERE mentor_id IN (OLD.mentor_id, NEW.mentor_id);
  INSERT INTO mentor_contact SELECT * FROM mentor_contact_live WHERE mentor_id IN (OLD.mentor_id, NEW.mentor_id);
END;

-- An id that never left the computer goes with the attempt, so that a retry decides afresh whether
-- the quota covers it: a pending id is one that may be out. Writes therefore set sent_at to its
-- final value at once, never to NULL on the way to an earlier attempt's time.
CREATE TRIGGER email_au_tracking_unsent AFTER UPDATE OF sent_at ON email
WHEN NEW.sent_at IS NULL AND NEW.tracking_state = 'pending' BEGIN
  UPDATE email SET tracking_id = NULL, tracking_state = NULL, tracking_account = NULL,
    tracking_chosen_at = NULL
  WHERE id = NEW.id;
END;

CREATE TRIGGER email_au_tracking AFTER UPDATE OF tracking_state, open_count ON email BEGIN
  DELETE FROM mentor_contact WHERE mentor_id = NEW.mentor_id;
  INSERT INTO mentor_contact SELECT * FROM mentor_contact_live WHERE mentor_id = NEW.mentor_id;
END;

-- The official server's pause of a mailbox's sending, as last synced; and whether what the server
-- was given of it (its secret, sender name, servers or limits) changed since. Both are kept only
-- while it has cloud emails: the server forgets a mailbox with none.
ALTER TABLE mailbox ADD COLUMN cloud_paused_until INTEGER;
ALTER TABLE mailbox ADD COLUMN cloud_pause_reason TEXT
  CHECK ((cloud_pause_reason IS NULL) = (cloud_paused_until IS NULL));
ALTER TABLE mailbox ADD COLUMN cloud_stale INTEGER NOT NULL DEFAULT 0 CHECK (cloud_stale IN (0, 1));

CREATE TRIGGER email_au_cloud AFTER UPDATE OF state ON email
WHEN OLD.state = 'cloud' AND NEW.state <> 'cloud' BEGIN
  UPDATE mailbox SET cloud_paused_until = NULL, cloud_pause_reason = NULL, cloud_stale = 0
  WHERE id = OLD.mailbox_id
    AND NOT EXISTS (SELECT 1 FROM email WHERE mailbox_id = OLD.mailbox_id AND state = 'cloud');
END;

-- About how much an email comes to as it goes to the official server: its letter and files.
CREATE VIEW email_size AS
SELECT e.id AS email_id, length(CAST(e.html AS BLOB)) + length(CAST(e.text AS BLOB))
    + coalesce((SELECT sum(a.size) FROM email_attachment a WHERE a.email_id = e.id), 0) AS size
FROM email e;

-- Outcomes written down here that the server is still to be told of, so that it deletes them.
CREATE TABLE cloud_ack (
  cloud_id TEXT PRIMARY KEY,
  account TEXT NOT NULL
) STRICT, WITHOUT ROWID;

PRAGMA foreign_key_check;
COMMIT;
PRAGMA foreign_keys = ON;
