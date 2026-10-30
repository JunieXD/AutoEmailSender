-- Derived from the password with the login key; encrypts cloud backups.
ALTER TABLE account ADD COLUMN backup_key BLOB CHECK (length(backup_key) = 32);

-- cloud_backup and last_backup_at (the last successful run, uploaded or unchanged) are in 0001.
-- The device id is random and leaves with the data's exports, so a restored copy gets its own.
ALTER TABLE profile ADD COLUMN cloud_backup_device TEXT;
-- committed_at of the current cloud backup when this computer made it with the current key.
ALTER TABLE profile ADD COLUMN cloud_backup_commit INTEGER;
-- committed_at of another computer's backup the user agreed to replace.
ALTER TABLE profile ADD COLUMN cloud_backup_replace INTEGER;
-- Since when backing up has been failing, and the latest reason.
ALTER TABLE profile ADD COLUMN cloud_backup_failed_at INTEGER;
ALTER TABLE profile ADD COLUMN cloud_backup_error TEXT
  CHECK ((cloud_backup_error IS NULL) = (cloud_backup_failed_at IS NULL));

-- Materials already in the cloud, encrypted with the current backup key.
CREATE TABLE cloud_backup_material (
  material_id INTEGER PRIMARY KEY REFERENCES material(id) ON DELETE CASCADE,
  object TEXT NOT NULL,
  size INTEGER NOT NULL
) STRICT;

-- Uploaded objects belong to one key: a new one (a changed password) uploads everything again.
CREATE TRIGGER account_backup_key AFTER UPDATE OF backup_key ON account
WHEN NEW.backup_key IS NOT OLD.backup_key BEGIN
  DELETE FROM cloud_backup_material;
  UPDATE profile SET cloud_backup_commit = NULL;
END;

-- Logging out or into another account turns cloud backup off; it belonged to that account.
CREATE TRIGGER account_ad AFTER DELETE ON account BEGIN
  DELETE FROM cloud_backup_material;
  UPDATE profile SET cloud_backup = 0, last_backup_at = NULL, cloud_backup_commit = NULL,
    cloud_backup_replace = NULL, cloud_backup_failed_at = NULL, cloud_backup_error = NULL;
END;

-- Never used; cloud_backup_material records what is uploaded, for the key it was uploaded with.
ALTER TABLE material DROP COLUMN backed_up_at;
