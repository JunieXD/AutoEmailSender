DROP TABLE library_update;
DROP TABLE mentor_library;

CREATE TABLE mentor_library (
  library_id TEXT PRIMARY KEY,
  mentor_id INTEGER NOT NULL REFERENCES mentor(id) ON DELETE CASCADE,
  status TEXT NOT NULL CHECK (status IN ('active', 'retired', 'departed', 'deceased', 'removed')),
  record TEXT NOT NULL CHECK (json_valid(record))
) STRICT, WITHOUT ROWID;
CREATE INDEX mentor_library_mentor ON mentor_library (mentor_id);

CREATE TABLE library_update (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  library_id TEXT NOT NULL REFERENCES mentor_library(library_id) ON DELETE CASCADE,
  field TEXT NOT NULL CHECK (field IN ('name', 'research', 'sourceUrl', 'title',
    'primaryEmail', 'primaryAffiliation')),
  university TEXT NOT NULL DEFAULT '',
  school TEXT NOT NULL DEFAULT '',
  department TEXT NOT NULL DEFAULT '',
  original TEXT NOT NULL CHECK (json_valid(original)),
  incoming TEXT NOT NULL CHECK (json_valid(incoming)),
  CHECK (field = 'title' OR (university = '' AND school = '' AND department = '')),
  UNIQUE (library_id, field, university, school, department)
) STRICT;

ALTER TABLE account ADD COLUMN library_revision INTEGER NOT NULL DEFAULT 0;
ALTER TABLE account ADD COLUMN library_checked_at INTEGER;
