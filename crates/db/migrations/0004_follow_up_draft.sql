DROP INDEX email_first_draft;
CREATE UNIQUE INDEX email_contact_draft ON email (mentor_id)
  WHERE kind IN ('first', 'follow_up') AND task_id IS NULL AND state = 'review';
