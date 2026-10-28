-- no-transaction
-- Tests build databases directly from migrations. WAL lets readers and writers proceed together,
-- matching the journal mode used by db::open.
PRAGMA journal_mode = WAL;
