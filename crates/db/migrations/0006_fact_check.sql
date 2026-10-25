ALTER TABLE email ADD COLUMN fact_check TEXT CHECK (fact_check IS NULL OR json_valid(fact_check));
