CREATE TABLE account (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    email TEXT NOT NULL,
    token TEXT
);
