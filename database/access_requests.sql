-- Additive schema for public access requests (SQLite and PostgreSQL).
-- Does not grant portal permissions. Safe to run more than once.
-- Existing tables and columns are not altered.

CREATE TABLE IF NOT EXISTS access_requests (
    id INTEGER PRIMARY KEY,
    nome TEXT NOT NULL,
    email TEXT NOT NULL,
    gabinete TEXT NOT NULL,
    unidade_outro TEXT,
    status TEXT NOT NULL DEFAULT 'pendente'
        CHECK(status IN ('pendente','aprovado','recusado')),
    created_at TEXT NOT NULL,
    viewed_at TEXT,
    processed_at TEXT,
    processed_by TEXT
);

CREATE INDEX IF NOT EXISTS access_requests_email_idx ON access_requests(email);

CREATE UNIQUE INDEX IF NOT EXISTS access_requests_email_pendente_idx
    ON access_requests(email) WHERE status = 'pendente';

INSERT INTO configuracoes VALUES('acesso_solicitacoes_schema_v2','1')
ON CONFLICT DO NOTHING;
