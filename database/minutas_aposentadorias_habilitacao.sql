-- Habilitação MANUAL e exclusiva de Aposentadorias.
-- Não executar pelo aplicativo. Conferir o SELECT antes do COMMIT.
-- Recusa a operação se as pré-condições não estiverem satisfeitas.
-- Não altera Prefeituras, Câmaras, Institutos, Secretarias, Pensões, Reformas ou Admissões.

BEGIN;

DO $$
DECLARE
    alvo mpc_portarias.minutas_especialidades%ROWTYPE;
    atualizados integer;
BEGIN
    SELECT * INTO alvo
    FROM mpc_portarias.minutas_especialidades
    WHERE id = 'elvira.pessoal.aposentadorias'
    FOR UPDATE;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'Especialidade de Aposentadorias não encontrada em mpc_portarias.minutas_especialidades.';
    END IF;

    IF alvo.procuradora_chave IS DISTINCT FROM 'elvira samara'
       OR alvo.grupo IS DISTINCT FROM 'ATOS_PESSOAL'
       OR alvo.nome IS DISTINCT FROM 'Aposentadorias'
       OR alvo.motor IS DISTINCT FROM ''
       OR alvo.config_versao IS DISTINCT FROM '1'
       OR alvo.estado IS DISTINCT FROM 'A iniciar' THEN
        RAISE EXCEPTION
            'Pré-condições recusadas. id=% motor=% config_versao=% estado=% grupo=%',
            alvo.id, alvo.motor, alvo.config_versao, alvo.estado, alvo.grupo;
    END IF;

    IF EXISTS (
        SELECT 1
        FROM mpc_portarias.minutas_especialidades
        WHERE id <> 'elvira.pessoal.aposentadorias'
          AND motor = 'aposentadorias-v1'
    ) THEN
        RAISE EXCEPTION 'O motor aposentadorias-v1 já está ligado a outra especialidade. Operação recusada.';
    END IF;

    UPDATE mpc_portarias.minutas_especialidades
       SET motor = 'aposentadorias-v1',
           estado = 'Em testes',
           config_versao = '1',
           revisao = revisao + 1,
           atualizado_em = to_char(
               clock_timestamp() AT TIME ZONE 'UTC',
               'YYYY-MM-DD"T"HH24:MI:SS.MS"+00:00"'
           ),
           atualizado_por = 'habilitacao-manual-aposentadorias-v1'
     WHERE id = 'elvira.pessoal.aposentadorias'
       AND procuradora_chave = 'elvira samara'
       AND grupo = 'ATOS_PESSOAL'
       AND nome = 'Aposentadorias'
       AND motor = ''
       AND config_versao = '1'
       AND estado = 'A iniciar';

    GET DIAGNOSTICS atualizados = ROW_COUNT;
    IF atualizados <> 1 THEN
        RAISE EXCEPTION 'Atualização recusada: % linha(s) alterada(s).', atualizados;
    END IF;
END $$;

SELECT id, grupo, motor, estado, config_versao, revisao, atualizado_em
FROM mpc_portarias.minutas_especialidades
WHERE id = 'elvira.pessoal.aposentadorias';

SELECT id, motor, estado, config_versao
FROM mpc_portarias.minutas_especialidades
WHERE id IN (
    'elvira.pca.rpps',
    'elvira.pca.camaras',
    'elvira.pca.prefeituras',
    'elvira.pca.secretarias_municipais',
    'elvira.pessoal.pensoes',
    'elvira.pessoal.reformas',
    'elvira.pessoal.admissoes'
)
ORDER BY id;

COMMIT;
