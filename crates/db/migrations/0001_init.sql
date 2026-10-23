-- Times are UTC milliseconds (INTEGER). Missing values are NULL, never ''.
-- Local civil dates/times (schedule windows, todo due) are TEXT 'YYYY-MM-DD' / 'HH:MM'.

CREATE TABLE model_config (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT,                    -- NULL: known by the model's name
  provider TEXT NOT NULL,
  base_url TEXT,
  model TEXT NOT NULL,
  api_key TEXT NOT NULL,        -- never sent to the UI
  input_price REAL,
  output_price REAL,
  created_at INTEGER NOT NULL,
  CHECK ((provider = 'custom') = (base_url IS NOT NULL))
) STRICT;

CREATE TABLE template (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  subject TEXT NOT NULL,
  body_md TEXT NOT NULL,
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL
) STRICT;

CREATE TABLE mailbox (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  address TEXT NOT NULL UNIQUE COLLATE NOCASE,
  sender_name TEXT NOT NULL,
  provider TEXT NOT NULL,
  smtp_host TEXT NOT NULL,
  smtp_port INTEGER NOT NULL,
  smtp_security TEXT NOT NULL CHECK (smtp_security IN ('tls', 'starttls')),
  imap_host TEXT NOT NULL,
  imap_port INTEGER NOT NULL,
  imap_security TEXT NOT NULL CHECK (imap_security IN ('tls', 'starttls')),
  auth TEXT NOT NULL CHECK (auth IN ('password', 'oauth')),
  secret TEXT NOT NULL,         -- password, app password or OAuth token; never sent to the UI
  daily_limit INTEGER NOT NULL,
  min_interval_s INTEGER NOT NULL,
  rotation_order INTEGER NOT NULL,
  paused_until INTEGER,
  pause_reason TEXT,
  created_at INTEGER NOT NULL,
  CHECK ((paused_until IS NULL) = (pause_reason IS NULL))
) STRICT;

CREATE TABLE profile (
  id INTEGER PRIMARY KEY CHECK (id = 1),
  name TEXT,
  research_interests TEXT,
  rewrite_preference TEXT,
  font TEXT CHECK (font IN ('song', 'hei')), -- NULL = the recipient's default font
  font_size_pt REAL NOT NULL,
  line_height REAL NOT NULL,
  paragraph_spacing_em REAL NOT NULL,
  first_line_indent INTEGER NOT NULL CHECK (first_line_indent IN (0, 1)),
  rotation TEXT NOT NULL CHECK (rotation IN ('failover', 'per_email')),
  default_model_config_id INTEGER REFERENCES model_config(id) ON DELETE SET NULL,
  default_template_id INTEGER REFERENCES template(id) ON DELETE SET NULL,
  ai_fact_check INTEGER NOT NULL CHECK (ai_fact_check IN (0, 1)),
  ai_reply_outcome INTEGER NOT NULL CHECK (ai_reply_outcome IN (0, 1)),
  ai_reply_todos INTEGER NOT NULL CHECK (ai_reply_todos IN (0, 1)),
  reply_notifications INTEGER NOT NULL CHECK (reply_notifications IN (0, 1)),
  open_tracking INTEGER NOT NULL CHECK (open_tracking IN (0, 1)),
  cloud_backup INTEGER NOT NULL CHECK (cloud_backup IN (0, 1)),
  last_backup_at INTEGER
) STRICT;

-- The one profile, with defaults.
INSERT INTO profile (id, font_size_pt, line_height, paragraph_spacing_em, first_line_indent, rotation,
  ai_fact_check, ai_reply_outcome, ai_reply_todos, reply_notifications, open_tracking, cloud_backup)
VALUES (1, 10.5, 1.6, 0.8, 0, 'failover', 1, 0, 0, 1, 0, 0);

CREATE TABLE material (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  file_name TEXT NOT NULL,
  size INTEGER NOT NULL,
  for_matching INTEGER NOT NULL CHECK (for_matching IN (0, 1)),
  for_writing INTEGER NOT NULL CHECK (for_writing IN (0, 1)),
  attach_by_default INTEGER NOT NULL CHECK (attach_by_default IN (0, 1)),
  backed_up_at INTEGER,
  created_at INTEGER NOT NULL,
  extracted_text TEXT
) STRICT;

CREATE TABLE mentor (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL CHECK (name <> ''),
  email TEXT UNIQUE COLLATE NOCASE CHECK (email <> ''),
  title TEXT CHECK (title <> ''),
  university TEXT CHECK (university <> ''),
  school TEXT CHECK (school <> ''),
  department TEXT CHECK (department <> ''),
  homepage_url TEXT,
  source_url TEXT,
  mailbox_id INTEGER REFERENCES mailbox(id) ON DELETE SET NULL,
  library_id TEXT UNIQUE,
  trashed_at INTEGER,
  match_state TEXT CHECK (match_state IN ('queued', 'running', 'done', 'failed')),
  match_score INTEGER CHECK (match_score BETWEEN 0 AND 100),
  matched_at INTEGER,
  match_model TEXT,
  match_error TEXT,
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL,
  CHECK ((match_state IS 'done') = (match_score IS NOT NULL AND matched_at IS NOT NULL AND match_model IS NOT NULL)),
  CHECK ((match_state IS 'failed') = (match_error IS NOT NULL))
) STRICT;
-- Long text lives apart so list scans and sorts stay on small rows.
CREATE TABLE mentor_detail (
  mentor_id INTEGER PRIMARY KEY REFERENCES mentor(id) ON DELETE CASCADE,
  research TEXT CHECK (research <> ''),
  papers TEXT,                 -- JSON array of strings, display only
  note TEXT,
  match_reason TEXT,
  match_directions TEXT,       -- JSON arrays of strings, display only
  match_highlights TEXT,
  match_risks TEXT
) STRICT;
-- Keyword search: the app writes name/school/department/research into body as space-separated
-- character bigrams, and searches with a phrase of the query's bigrams. name holds the name's
-- single characters, which a one-character word searches.
CREATE VIRTUAL TABLE mentor_search USING fts5(body, name, content='', contentless_delete=1);
CREATE INDEX mentor_list ON mentor (trashed_at, university, school, department);
CREATE INDEX mentor_by_score ON mentor (trashed_at, match_score DESC, id);
CREATE INDEX mentor_mailbox ON mentor (mailbox_id) WHERE mailbox_id IS NOT NULL;

CREATE TABLE tag (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL UNIQUE,
  color TEXT NOT NULL,
  position INTEGER NOT NULL
) STRICT;

CREATE TABLE mentor_tag (
  mentor_id INTEGER NOT NULL REFERENCES mentor(id) ON DELETE CASCADE,
  tag_id INTEGER NOT NULL REFERENCES tag(id) ON DELETE CASCADE,
  PRIMARY KEY (tag_id, mentor_id)
) STRICT, WITHOUT ROWID;
CREATE INDEX mentor_tag_mentor ON mentor_tag (mentor_id);

CREATE TABLE library_update (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  mentor_id INTEGER NOT NULL REFERENCES mentor(id) ON DELETE CASCADE,
  kind TEXT NOT NULL CHECK (kind IN ('conflict', 'notice')),
  field TEXT NOT NULL,
  value TEXT NOT NULL,
  created_at INTEGER NOT NULL
) STRICT;
CREATE INDEX library_update_mentor ON library_update (mentor_id);

CREATE TABLE task (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  kind TEXT NOT NULL CHECK (kind IN ('first', 'follow_up')),
  writer TEXT NOT NULL CHECK (writer IN ('ai', 'template')),
  template_id INTEGER REFERENCES template(id) ON DELETE SET NULL,
  model_config_id INTEGER REFERENCES model_config(id) ON DELETE SET NULL,
  schedule TEXT NOT NULL CHECK (schedule IN ('now', 'window')),
  window_dates TEXT,           -- JSON array of 'YYYY-MM-DD'
  window_start TEXT,
  window_end TEXT,
  per_window INTEGER,
  state TEXT NOT NULL CHECK (state IN ('active', 'paused', 'stopped')),
  created_at INTEGER NOT NULL,
  CHECK ((schedule = 'window') = (window_dates IS NOT NULL AND window_start IS NOT NULL
                                  AND window_end IS NOT NULL AND per_window IS NOT NULL))
) STRICT;

CREATE TABLE task_material (
  task_id INTEGER NOT NULL REFERENCES task(id) ON DELETE CASCADE,
  material_id INTEGER NOT NULL REFERENCES material(id) ON DELETE RESTRICT,
  role TEXT NOT NULL CHECK (role IN ('reference', 'attachment')),
  PRIMARY KEY (task_id, material_id, role)
) STRICT, WITHOUT ROWID;

-- An email the app writes and sends. Large columns last.
CREATE TABLE email (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  mentor_id INTEGER NOT NULL REFERENCES mentor(id) ON DELETE CASCADE,
  task_id INTEGER REFERENCES task(id) ON DELETE SET NULL,
  kind TEXT NOT NULL CHECK (kind IN ('first', 'follow_up', 'reply')),
  reply_to TEXT,               -- Message-ID this email replies to
  state TEXT NOT NULL CHECK (state IN ('generating', 'generation_failed', 'review', 'approved',
    'cloud', 'sending', 'maybe_sent', 'sent', 'bounced', 'failed', 'canceled')),
  planned_at INTEGER,
  mailbox_id INTEGER REFERENCES mailbox(id) ON DELETE SET NULL,
  from_address TEXT,
  model TEXT,
  message_id TEXT UNIQUE,
  sent_at INTEGER,             -- handed to SMTP (sending/maybe_sent) or accepted (sent/bounced)
  error TEXT,
  tracking_id TEXT UNIQUE,
  open_count INTEGER,
  first_opened_at INTEGER,
  last_opened_at INTEGER,
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL,
  subject TEXT,
  body_md TEXT,
  html TEXT,
  text TEXT,
  CHECK ((kind = 'first') = (reply_to IS NULL)),
  CHECK (state NOT IN ('approved', 'cloud', 'sending', 'maybe_sent', 'sent', 'bounced', 'failed')
         OR (html IS NOT NULL AND text IS NOT NULL AND planned_at IS NOT NULL)),
  CHECK (state NOT IN ('sending', 'maybe_sent', 'sent', 'bounced')
         OR (message_id IS NOT NULL AND sent_at IS NOT NULL AND from_address IS NOT NULL)),
  CHECK (state NOT IN ('generation_failed', 'failed', 'bounced', 'canceled') OR error IS NOT NULL)
) STRICT;
CREATE INDEX email_out ON email (mentor_id, sent_at, kind)
  WHERE state IN ('maybe_sent', 'sent', 'bounced');
CREATE INDEX email_queue ON email (planned_at) WHERE state = 'approved';
CREATE INDEX email_task ON email (task_id, state);
CREATE INDEX email_attention ON email (state) WHERE state IN ('generation_failed', 'failed', 'maybe_sent');
CREATE UNIQUE INDEX email_reply_draft ON email (mentor_id) WHERE kind = 'reply' AND state = 'review';

CREATE TABLE email_attachment (
  email_id INTEGER NOT NULL REFERENCES email(id) ON DELETE CASCADE,
  material_id INTEGER REFERENCES material(id) ON DELETE SET NULL,
  file_name TEXT NOT NULL,
  size INTEGER NOT NULL,
  PRIMARY KEY (email_id, file_name)
) STRICT, WITHOUT ROWID;

-- A message seen in a mailbox that the app did not send.
CREATE TABLE message (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  mailbox_id INTEGER NOT NULL REFERENCES mailbox(id) ON DELETE CASCADE,
  folder TEXT NOT NULL,
  uidvalidity INTEGER NOT NULL,
  uid INTEGER NOT NULL,
  kind TEXT NOT NULL CHECK (kind IN ('reply', 'auto_reply', 'sent')),
  message_id TEXT,
  in_reply_to TEXT,
  sent_at INTEGER NOT NULL,
  read_at INTEGER,
  outcome TEXT CHECK (outcome IN ('interested', 'no_slot', 'declined', 'unclear')),
  bounce_reason TEXT,
  from_address TEXT NOT NULL,
  subject TEXT,
  recipients TEXT NOT NULL,    -- JSON {to, cc}, display only
  refs TEXT,
  body_text TEXT,
  body_html TEXT,
  UNIQUE (mailbox_id, folder, uidvalidity, uid),
  CHECK (kind = 'reply' OR (read_at IS NULL AND outcome IS NULL)),
  CHECK (kind = 'sent' OR bounce_reason IS NULL)
) STRICT;
CREATE INDEX message_message_id ON message (message_id);

CREATE TABLE message_mentor (
  mentor_id INTEGER NOT NULL REFERENCES mentor(id) ON DELETE CASCADE,
  message_id INTEGER NOT NULL REFERENCES message(id) ON DELETE CASCADE,
  PRIMARY KEY (mentor_id, message_id)
) STRICT, WITHOUT ROWID;
CREATE INDEX message_mentor_message ON message_mentor (message_id);

CREATE TABLE todo (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  mentor_id INTEGER NOT NULL REFERENCES mentor(id) ON DELETE CASCADE,
  message_id INTEGER REFERENCES message(id) ON DELETE SET NULL,
  text TEXT NOT NULL,
  due_date TEXT,
  due_time TEXT,
  done_at INTEGER,
  reminded_at INTEGER,
  created_at INTEGER NOT NULL,
  CHECK (due_time IS NULL OR due_date IS NOT NULL)
) STRICT;
CREATE INDEX todo_open ON todo (due_date) WHERE done_at IS NULL;
CREATE INDEX todo_mentor ON todo (mentor_id);

CREATE TABLE usage (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  at INTEGER NOT NULL,
  model_config_id INTEGER REFERENCES model_config(id) ON DELETE SET NULL,
  model TEXT NOT NULL,
  purpose TEXT NOT NULL CHECK (purpose IN ('crawl', 'match', 'draft', 'rewrite', 'fact_check',
    'reply_outcome', 'reply_todos', 'reply_draft')),
  crawl_id INTEGER REFERENCES crawl(id) ON DELETE SET NULL,
  email_id INTEGER REFERENCES email(id) ON DELETE SET NULL,
  input_tokens INTEGER NOT NULL,
  cached_tokens INTEGER NOT NULL,
  output_tokens INTEGER NOT NULL
) STRICT;
CREATE INDEX usage_at ON usage (at);
CREATE INDEX usage_crawl ON usage (crawl_id) WHERE crawl_id IS NOT NULL;

CREATE TABLE crawl (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  kind TEXT NOT NULL CHECK (kind IN ('site', 'agent')),
  university TEXT,
  school TEXT,
  start_urls TEXT,             -- JSON array
  model_config_id INTEGER REFERENCES model_config(id) ON DELETE SET NULL,
  state TEXT NOT NULL CHECK (state IN ('running', 'paused', 'done', 'failed', 'canceled')),
  error TEXT,
  created_at INTEGER NOT NULL,
  finished_at INTEGER,
  CHECK ((kind = 'site') = (start_urls IS NOT NULL))
) STRICT;

CREATE TABLE crawl_run (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  crawl_id INTEGER NOT NULL REFERENCES crawl(id) ON DELETE CASCADE,
  app_version TEXT NOT NULL,
  started_at INTEGER NOT NULL,
  ended_at INTEGER
) STRICT;
CREATE INDEX crawl_run_crawl ON crawl_run (crawl_id);

CREATE TABLE crawl_page (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  crawl_id INTEGER NOT NULL REFERENCES crawl(id) ON DELETE CASCADE,
  url TEXT NOT NULL,
  mode TEXT NOT NULL CHECK (mode IN ('http', 'webview')),
  status TEXT NOT NULL,
  fetched_at INTEGER NOT NULL,
  text TEXT,
  UNIQUE (crawl_id, url)
) STRICT;

CREATE TABLE candidate (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  crawl_id INTEGER NOT NULL REFERENCES crawl(id) ON DELETE CASCADE,
  review TEXT NOT NULL CHECK (review IN ('pending', 'accepted', 'rejected', 'merged')),
  mentor_id INTEGER REFERENCES mentor(id) ON DELETE SET NULL,
  enrich TEXT NOT NULL CHECK (enrich IN ('none', 'queued', 'running', 'done', 'failed')),
  name TEXT NOT NULL,
  email TEXT,
  title TEXT,
  university TEXT,
  school TEXT,
  department TEXT,
  homepage_url TEXT,
  source_url TEXT,
  research TEXT,
  papers TEXT
) STRICT;
CREATE INDEX candidate_crawl ON candidate (crawl_id, review);

CREATE TABLE mailbox_folder (
  mailbox_id INTEGER NOT NULL REFERENCES mailbox(id) ON DELETE CASCADE,
  folder TEXT NOT NULL,
  role TEXT NOT NULL CHECK (role IN ('inbox', 'sent')),
  uidvalidity INTEGER NOT NULL,
  last_uid INTEGER NOT NULL,
  history_before_uid INTEGER,  -- NULL once the history scan is done
  PRIMARY KEY (mailbox_id, folder)
) STRICT, WITHOUT ROWID;

CREATE TABLE mentor_scan (
  mentor_id INTEGER NOT NULL REFERENCES mentor(id) ON DELETE CASCADE,
  mailbox_id INTEGER NOT NULL REFERENCES mailbox(id) ON DELETE CASCADE,
  created_at INTEGER NOT NULL,
  PRIMARY KEY (mentor_id, mailbox_id)
) STRICT, WITHOUT ROWID;

CREATE TABLE device (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  public_key BLOB NOT NULL UNIQUE,
  paired_at INTEGER NOT NULL,
  last_seen_at INTEGER
) STRICT;

-- Written only by the triggers at the end of this file.
CREATE TABLE mentor_contact (
  mentor_id INTEGER PRIMARY KEY REFERENCES mentor(id) ON DELETE CASCADE,
  status TEXT NOT NULL CHECK (status IN ('contacted', 'replied', 'bounced')),
  last_out_at INTEGER,
  last_reply_at INTEGER,
  contact_count INTEGER NOT NULL,
  unread INTEGER NOT NULL CHECK (unread IN (0, 1))
) STRICT;
CREATE INDEX mentor_contact_status ON mentor_contact (status);
CREATE INDEX mentor_contact_unread ON mentor_contact (mentor_id) WHERE unread = 1;

-- The one definition of the contact summary (docs/data-model.md#联系状态). Each column filters by
-- mentor inside its own subquery: SQLite does not push a correlated mentor_id into a shared UNION
-- or aggregate, and the triggers below run this for one mentor at a time on every write.
CREATE VIEW mentor_contact_live AS
SELECT mentor_id,
       CASE WHEN last_reply_at IS NOT NULL THEN 'replied'
            WHEN last_bounced_at = last_out_at THEN 'bounced'
            ELSE 'contacted' END AS status,
       last_out_at, last_reply_at, contact_count,
       coalesce(last_unread_at > coalesce(last_out_at, 0), 0) AS unread
FROM (SELECT m.id AS mentor_id,
             (SELECT max(at) FROM (
                SELECT max(sent_at) AS at FROM email
                WHERE mentor_id = m.id AND state IN ('maybe_sent', 'sent', 'bounced')
                UNION ALL
                SELECT max(msg.sent_at) FROM message_mentor mm JOIN message msg ON msg.id = mm.message_id
                WHERE mm.mentor_id = m.id AND msg.kind = 'sent')) AS last_out_at,
             (SELECT max(at) FROM (
                SELECT max(CASE WHEN state = 'bounced' THEN sent_at END) AS at FROM email
                WHERE mentor_id = m.id AND state IN ('maybe_sent', 'sent', 'bounced')
                UNION ALL
                SELECT max(msg.sent_at) FROM message_mentor mm JOIN message msg ON msg.id = mm.message_id
                WHERE mm.mentor_id = m.id AND msg.kind = 'sent' AND msg.bounce_reason IS NOT NULL)) AS last_bounced_at,
             (SELECT count(*) FROM email
              WHERE mentor_id = m.id AND state IN ('maybe_sent', 'sent', 'bounced') AND kind = 'first')
             + (SELECT count(*) FROM message_mentor mm JOIN message msg ON msg.id = mm.message_id
                WHERE mm.mentor_id = m.id AND msg.kind = 'sent' AND msg.in_reply_to IS NULL) AS contact_count,
             (SELECT max(msg.sent_at) FROM message_mentor mm JOIN message msg ON msg.id = mm.message_id
              WHERE mm.mentor_id = m.id AND msg.kind = 'reply') AS last_reply_at,
             (SELECT max(msg.sent_at) FROM message_mentor mm JOIN message msg ON msg.id = mm.message_id
              WHERE mm.mentor_id = m.id AND msg.kind = 'reply' AND msg.read_at IS NULL) AS last_unread_at
      FROM mentor m)
WHERE last_out_at IS NOT NULL OR last_reply_at IS NOT NULL;

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

CREATE TRIGGER message_mentor_ai AFTER INSERT ON message_mentor BEGIN
  DELETE FROM mentor_contact WHERE mentor_id = NEW.mentor_id;
  INSERT INTO mentor_contact SELECT * FROM mentor_contact_live WHERE mentor_id = NEW.mentor_id;
END;

CREATE TRIGGER message_mentor_ad AFTER DELETE ON message_mentor BEGIN
  DELETE FROM mentor_contact WHERE mentor_id = OLD.mentor_id;
  INSERT INTO mentor_contact SELECT * FROM mentor_contact_live WHERE mentor_id = OLD.mentor_id;
END;

CREATE TRIGGER message_mentor_au AFTER UPDATE ON message_mentor BEGIN
  DELETE FROM mentor_contact WHERE mentor_id IN (OLD.mentor_id, NEW.mentor_id);
  INSERT INTO mentor_contact SELECT * FROM mentor_contact_live WHERE mentor_id IN (OLD.mentor_id, NEW.mentor_id);
END;

CREATE TRIGGER message_au AFTER UPDATE OF kind, sent_at, read_at, in_reply_to, bounce_reason ON message BEGIN
  DELETE FROM mentor_contact
  WHERE mentor_id IN (SELECT mentor_id FROM message_mentor WHERE message_id = NEW.id);
  INSERT INTO mentor_contact SELECT * FROM mentor_contact_live
  WHERE mentor_id IN (SELECT mentor_id FROM message_mentor WHERE message_id = NEW.id);
END;
