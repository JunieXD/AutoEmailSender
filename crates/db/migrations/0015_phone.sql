-- Phones paired for using the app remotely (docs/design.md#手机远程). The pairing key stays on
-- this computer: the interface cannot read it, and backups leave it out.
CREATE TABLE phone (
  id TEXT PRIMARY KEY CHECK (length(id) = 32),
  name TEXT NOT NULL,
  key BLOB NOT NULL CHECK (length(key) = 32),
  paired_at INTEGER NOT NULL,
  seen_at INTEGER
) STRICT;

ALTER TABLE profile ADD COLUMN phone_access INTEGER NOT NULL DEFAULT 0
  CHECK (phone_access IN (0, 1));
-- The relay room, made the first time it is needed, and the account it belongs to.
ALTER TABLE profile ADD COLUMN phone_room TEXT CHECK (length(phone_room) = 32);
ALTER TABLE profile ADD COLUMN phone_room_account TEXT
  CHECK ((phone_room IS NULL) = (phone_room_account IS NULL));

-- Logging into another account leaves the room, with the phones paired in it; logging out and in
-- again with the same account keeps them.
CREATE TRIGGER account_phone_room AFTER INSERT ON account
WHEN NEW.email IS NOT (SELECT phone_room_account FROM profile) BEGIN
  DELETE FROM phone;
  UPDATE profile SET phone_room = NULL, phone_room_account = NULL;
END;
