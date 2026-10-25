CREATE UNIQUE INDEX email_follow_up_draft ON email (mentor_id)
  WHERE kind = 'follow_up' AND task_id IS NULL AND state = 'review';
