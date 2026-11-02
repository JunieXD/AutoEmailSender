-- Since when the hourly synchronization of the mentor library has been failing, and the latest
-- reason; cleared by the next success.
ALTER TABLE account ADD COLUMN library_failed_at INTEGER;
ALTER TABLE account ADD COLUMN library_error TEXT
  CHECK ((library_error IS NULL) = (library_failed_at IS NULL));
