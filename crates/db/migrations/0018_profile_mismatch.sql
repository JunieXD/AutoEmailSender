-- When the page at this link, read for the candidate, proved not to be theirs: the link no longer
-- identifies them until a page read for them again proves it is.
ALTER TABLE candidate_profile ADD COLUMN mismatched_at INTEGER;
