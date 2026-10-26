-- Count in mentor ID order to keep filter membership and affiliation probes local.
CREATE INDEX mentor_filter_count ON mentor(trashed_at, id, match_score);

-- Missing-value probes need all affiliations but none of the other stored fields.
CREATE INDEX mentor_affiliation_fields
 ON mentor_affiliation(mentor_id, university, school, department, title);

-- The university index already serves value filtering and distinct-mentor counts.
CREATE INDEX mentor_affiliation_school ON mentor_affiliation(school, mentor_id)
 WHERE school IS NOT NULL;
CREATE INDEX mentor_affiliation_department ON mentor_affiliation(department, mentor_id)
 WHERE department IS NOT NULL;
CREATE INDEX mentor_affiliation_title ON mentor_affiliation(title, mentor_id)
 WHERE title IS NOT NULL;
