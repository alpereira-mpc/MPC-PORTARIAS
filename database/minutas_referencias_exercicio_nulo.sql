-- Migração MANUAL: exercício nulo em mpc_portarias.minutas_referencias.
-- Necessária para cadastrar Aposentadorias sem exercício financeiro das contas.
-- Não executar pelo aplicativo. Backup recuperável e conferência do esquema antes.
-- Não recria tabelas, não altera linhas, classificações, documentos nem vínculos.
-- A aplicação continua exigindo exercício nas PCAs e nas demais especialidades.

-- Pré-checagem somente leitura (execute antes do BEGIN):
-- SELECT column_name, is_nullable, data_type
-- FROM information_schema.columns
-- WHERE table_schema = 'mpc_portarias'
--   AND table_name = 'minutas_referencias'
--   AND column_name = 'exercicio';
-- SELECT conname, pg_get_constraintdef(oid)
-- FROM pg_constraint
-- WHERE conrelid = 'mpc_portarias.minutas_referencias'::regclass
--   AND pg_get_constraintdef(oid) ILIKE '%exercicio%';
-- SELECT COUNT(*) AS total,
--        COUNT(*) FILTER (WHERE exercicio IS NULL) AS sem_exercicio
-- FROM mpc_portarias.minutas_referencias;

BEGIN;

DO $$
DECLARE
    tipo text;
    nulo text;
BEGIN
    SELECT data_type, is_nullable
      INTO tipo, nulo
    FROM information_schema.columns
    WHERE table_schema = 'mpc_portarias'
      AND table_name = 'minutas_referencias'
      AND column_name = 'exercicio';

    IF tipo IS NULL THEN
        RAISE EXCEPTION
            'Coluna mpc_portarias.minutas_referencias.exercicio não encontrada.';
    END IF;

    IF tipo IS DISTINCT FROM 'integer' THEN
        RAISE EXCEPTION
            'Pré-condição recusada: exercicio deve ser integer. tipo=%',
            tipo;
    END IF;
END $$;

ALTER TABLE mpc_portarias.minutas_referencias
    ALTER COLUMN exercicio DROP NOT NULL;

COMMIT;

-- Conferência somente leitura, fora da transação:
-- SELECT column_name, is_nullable, data_type
-- FROM information_schema.columns
-- WHERE table_schema = 'mpc_portarias'
--   AND table_name = 'minutas_referencias'
--   AND column_name = 'exercicio';
-- Esperado: is_nullable = 'YES'.
-- SELECT COUNT(*) AS total,
--        COUNT(*) FILTER (WHERE exercicio IS NULL) AS sem_exercicio
-- FROM mpc_portarias.minutas_referencias;
-- O COUNT total deve permanecer igual ao da pré-checagem; nenhuma linha é alterada.
-- CHECK (exercicio BETWEEN 1900 AND 2200) permanece: NULL não viola o CHECK.
-- A obrigatoriedade de exercício nas PCAs fica na validação de aplicação.
