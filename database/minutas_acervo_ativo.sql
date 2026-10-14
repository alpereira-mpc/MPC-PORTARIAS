-- Separação manual do acervo ativo. Não executada pelo aplicativo.
-- Aditiva e idempotente: não apaga linhas, caminhos, extrações nem objetos do Storage.
-- Tudo o que já existe passa a ser 'legado'. Inserções posteriores nascem 'ativo'.
-- O número de processo pode repetir-se entre legado e ativo, nunca dentro do mesmo acervo.
BEGIN;

ALTER TABLE mpc_portarias.minutas_referencias
    ADD COLUMN IF NOT EXISTS acervo TEXT;

UPDATE mpc_portarias.minutas_referencias
    SET acervo = 'legado'
    WHERE acervo IS NULL OR btrim(acervo) = '';

ALTER TABLE mpc_portarias.minutas_referencias
    ALTER COLUMN acervo SET DEFAULT 'ativo';

ALTER TABLE mpc_portarias.minutas_referencias
    ALTER COLUMN acervo SET NOT NULL;

ALTER TABLE mpc_portarias.minutas_referencias
    DROP CONSTRAINT IF EXISTS minutas_referencias_procuradora_chave_processo_chave_key;

CREATE UNIQUE INDEX IF NOT EXISTS minutas_referencias_processo_ativo_uq
    ON mpc_portarias.minutas_referencias (procuradora_chave, processo_chave)
    WHERE acervo = 'ativo';

CREATE UNIQUE INDEX IF NOT EXISTS minutas_referencias_processo_legado_uq
    ON mpc_portarias.minutas_referencias (procuradora_chave, processo_chave)
    WHERE acervo = 'legado';

DO $$
BEGIN
    ALTER TABLE mpc_portarias.minutas_referencias
        ADD CONSTRAINT minutas_referencias_acervo_check
        CHECK (acervo IN ('ativo', 'legado'));
EXCEPTION
    WHEN duplicate_object THEN NULL;
END $$;

COMMIT;

-- Conferência somente leitura, fora da transação:
-- SELECT acervo, COUNT(*) FROM mpc_portarias.minutas_referencias GROUP BY acervo;
-- SELECT COUNT(*) FROM mpc_portarias.minutas_referencias;          -- permanece 10
-- SELECT COUNT(*) FROM mpc_portarias.minutas_extracoes;            -- permanece 22
-- SELECT COUNT(*) FROM mpc_portarias.minutas_documentos_complementares; -- permanece 2
-- SELECT COUNT(*) FROM mpc_portarias.minutas_referencias WHERE acervo = 'ativo'; -- 0
