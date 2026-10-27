-- Count and identity queries do not need the large record JSON in the primary-key pages.
CREATE INDEX mentor_library_lookup ON mentor_library (library_id, mentor_id);
