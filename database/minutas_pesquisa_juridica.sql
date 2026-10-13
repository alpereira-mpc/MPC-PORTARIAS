-- Fase 3B.5A: índice textual dos pareceres definitivos já extraídos.
-- Execução manual no SQL Editor após database/minutas_extracoes.sql.
-- A operação é aditiva, idempotente e não reprocessa documentos.

CREATE INDEX IF NOT EXISTS minutas_extracoes_parecer_fts_pt_idx
    ON mpc_portarias.minutas_extracoes
    USING GIN (to_tsvector('portuguese', COALESCE(texto_integral, '')))
    WHERE papel = 'parecer' AND status = 'PROCESSADO';

-- As políticas existentes permanecem inalteradas. Nenhum acesso é concedido.
REVOKE ALL ON mpc_portarias.minutas_extracoes
    FROM PUBLIC, anon, authenticated;
