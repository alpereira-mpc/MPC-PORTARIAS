-- Habilitação MANUAL e idempotente do motor recursos-v1.
-- Não altera esquema, documentos, classificações, vínculos ou extrações.
BEGIN;

DO $$
DECLARE
    perfil RECORD;
BEGIN
    SELECT id,procuradora_chave,grupo,motor,config_versao
      INTO perfil
      FROM mpc_portarias.minutas_especialidades
     WHERE id='elvira.recursos'
     FOR UPDATE;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'Especialidade elvira.recursos não provisionada';
    END IF;
    IF perfil.procuradora_chave <> 'elvira samara' OR perfil.grupo <> 'RECURSO' THEN
        RAISE EXCEPTION 'Identidade ou grupo incompatível para elvira.recursos';
    END IF;
    IF perfil.motor NOT IN ('', 'recursos-v1') THEN
        RAISE EXCEPTION 'Outro motor já está configurado para elvira.recursos';
    END IF;
    IF perfil.config_versao NOT IN ('', '1') THEN
        RAISE EXCEPTION 'Versão de configuração incompatível para recursos-v1';
    END IF;

    UPDATE mpc_portarias.minutas_especialidades
       SET motor='recursos-v1',
           config_versao='1',
           estado=CASE WHEN estado IN ('A iniciar','Em alimentação') THEN 'Em testes' ELSE estado END,
           revisao=revisao+1,
           atualizado_em=CURRENT_TIMESTAMP::text,
           atualizado_por='MIGRACAO_MANUAL_RECURSOS_V1'
     WHERE id='elvira.recursos'
       AND (motor<> 'recursos-v1' OR config_versao<> '1' OR estado IN ('A iniciar','Em alimentação'));
END $$;

COMMIT;
