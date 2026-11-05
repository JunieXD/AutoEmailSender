-- The version of the terms of service and privacy policy the account agreed to, as last read
-- from the server; empty for accounts from before the terms.
ALTER TABLE account ADD COLUMN terms TEXT;
