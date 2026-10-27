-- Reading research for update comparisons must not load papers, notes or analysis with it.
CREATE INDEX mentor_detail_library_research ON mentor_detail (mentor_id, research);
