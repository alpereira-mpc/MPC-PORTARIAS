-- Executar manualmente no banco PostgreSQL do ambiente alvo, uma única vez.
-- Esta migração é idempotente e não altera documentos, ofícios ou andamentos existentes.
ALTER TABLE mpc_portarias.oficio_arquivos
    ADD COLUMN IF NOT EXISTS descricao TEXT NOT NULL DEFAULT '';
