ALTER TABLE email ADD COLUMN generated_at INTEGER;

CREATE INDEX email_generated_at ON email(generated_at, task_id)
WHERE generated_at IS NOT NULL;
