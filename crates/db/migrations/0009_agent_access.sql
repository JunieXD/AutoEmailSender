ALTER TABLE profile ADD COLUMN agent_access INTEGER NOT NULL DEFAULT 0 CHECK (agent_access IN (0, 1));
