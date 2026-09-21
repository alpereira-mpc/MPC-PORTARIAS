-- Supabase/PostgreSQL: migration aditiva e segura para solicitações de acesso.
-- Execute uma vez no SQL Editor do projeto. Não altera dados existentes.
ALTER TABLE access_requests
    ADD COLUMN IF NOT EXISTS viewed_at TEXT NULL;
