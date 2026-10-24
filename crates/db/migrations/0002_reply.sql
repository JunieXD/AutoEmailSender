ALTER TABLE message ADD COLUMN from_name TEXT;
-- The approved thread is kept even if the received message or its mailbox is deleted.
ALTER TABLE email ADD COLUMN refs TEXT;
