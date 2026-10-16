-- Habilitação MANUAL, exclusiva e reversível do motor denuncias-v1.
-- Não executar pelo aplicativo. Confira o SELECT final antes do COMMIT.
BEGIN;

DO $$
DECLARE
    alvo mpc_portarias.minutas_especialidades%ROWTYPE;
    atualizados integer;
BEGIN
    SELECT * INTO alvo FROM mpc_portarias.minutas_especialidades
    WHERE id = 'elvira.denuncias' FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'Especialidade elvira.denuncias não encontrada.';
    END IF;
    IF alvo.procuradora_chave IS DISTINCT FROM 'elvira samara'
       OR alvo.grupo IS DISTINCT FROM 'DENUNCIA'
       OR alvo.nome IS DISTINCT FROM 'Denúncias'
       OR alvo.motor IS DISTINCT FROM ''
       OR alvo.config_versao IS DISTINCT FROM '1'
       OR alvo.estado IS DISTINCT FROM 'A iniciar' THEN
        RAISE EXCEPTION 'Pré-condições recusadas: id=% motor=% versão=% estado=% grupo=%',
            alvo.id, alvo.motor, alvo.config_versao, alvo.estado, alvo.grupo;
    END IF;
    IF EXISTS (SELECT 1 FROM mpc_portarias.minutas_especialidades
               WHERE id <> 'elvira.denuncias' AND motor = 'denuncias-v1') THEN
        RAISE EXCEPTION 'denuncias-v1 já está associado a outra especialidade.';
    END IF;
    UPDATE mpc_portarias.minutas_especialidades
       SET motor = 'denuncias-v1', estado = 'Em testes', config_versao = '1',
           revisao = revisao + 1,
           atualizado_em = to_char(clock_timestamp() AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.MS"+00:00"'),
           atualizado_por = 'habilitacao-manual-denuncias-v1'
     WHERE id = 'elvira.denuncias' AND procuradora_chave = 'elvira samara'
       AND grupo = 'DENUNCIA' AND nome = 'Denúncias' AND motor = ''
       AND config_versao = '1' AND estado = 'A iniciar';
    GET DIAGNOSTICS atualizados = ROW_COUNT;
    IF atualizados <> 1 THEN
        RAISE EXCEPTION 'Atualização recusada: % linha(s) alterada(s).', atualizados;
    END IF;
END $$;

SELECT id, grupo, motor, estado, config_versao, revisao, atualizado_em
FROM mpc_portarias.minutas_especialidades WHERE id = 'elvira.denuncias';
COMMIT;

-- REVERSÃO MANUAL: use somente se esta ativação for a última alteração da linha.
-- BEGIN;
-- UPDATE mpc_portarias.minutas_especialidades
-- SET motor='', estado='A iniciar', revisao=revisao+1,
--     atualizado_em=to_char(clock_timestamp() AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.MS"+00:00"'),
--     atualizado_por='reversao-manual-denuncias-v1'
-- WHERE id='elvira.denuncias' AND procuradora_chave='elvira samara'
--   AND motor='denuncias-v1' AND config_versao='1' AND estado='Em testes'
--   AND revisao=<REVISAO_RETORNADA_NA_ATIVACAO>;
-- COMMIT;
