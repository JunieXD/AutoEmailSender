-- When the page at this link, read for the candidate, proved not to be theirs: the link no longer
-- identifies them until a page read for them again proves it is.
ALTER TABLE candidate_profile ADD COLUMN mismatched_at INTEGER;

-- The candidate a list's profile page was last read for, chosen when it is claimed and moved to
-- whom they merge into, so the result counts for them however the links change meanwhile.
ALTER TABLE crawl_page ADD COLUMN target_id INTEGER REFERENCES candidate(id) ON DELETE SET NULL
  CHECK (target_id IS NULL OR kind = 'profile');
CREATE INDEX crawl_page_target ON crawl_page (target_id) WHERE target_id IS NOT NULL;
