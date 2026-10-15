-- Ativação MANUAL e pontual do gerador experimental de PCA - Prefeituras Municipais.
-- Não é executado pelo aplicativo. Faça backup recuperável e valide a restauração antes.
-- Aplicação: o bloco falha e a transação não é confirmada se a pré-condição não corresponder a uma única linha.
BEGIN;

SELECT id, procuradora_chave, estado, motor, config_versao, revisao
FROM mpc_portarias.minutas_especialidades
WHERE id = 'elvira.pca.prefeituras'
  AND procuradora_chave = 'elvira samara'
FOR UPDATE;

DO $$
DECLARE
    linhas_alteradas INTEGER;
BEGIN
    UPDATE mpc_portarias.minutas_especialidades
    SET motor = 'pca-rpps-v1',
        estado = 'Em testes',
        revisao = revisao + 1,
        atualizado_em = CURRENT_TIMESTAMP,
        atualizado_por = CURRENT_USER
    WHERE id = 'elvira.pca.prefeituras'
      AND procuradora_chave = 'elvira samara'
      AND config_versao = '1'
      AND motor = ''
      AND estado = 'A iniciar';

    GET DIAGNOSTICS linhas_alteradas = ROW_COUNT;
    IF linhas_alteradas <> 1 THEN
        RAISE EXCEPTION 'Ativação recusada: esperada uma linha em A iniciar, configuração 1 e sem motor; encontradas %.', linhas_alteradas;
    END IF;
END $$;

SELECT id, estado, motor, config_versao, revisao, atualizado_em, atualizado_por
FROM mpc_portarias.minutas_especialidades
WHERE id = 'elvira.pca.prefeituras'
  AND procuradora_chave = 'elvira samara';

COMMIT;

-- REVERSÃO MANUAL (use somente se a ativação acima for a última alteração da linha).
-- Substitua <REVISAO_RETORNADA_PELA_ATIVACAO> pelo valor devolvido pelo UPDATE de ativação.
-- BEGIN;
-- UPDATE mpc_portarias.minutas_especialidades
-- SET motor = '', estado = 'A iniciar', revisao = revisao + 1,
--     atualizado_em = CURRENT_TIMESTAMP, atualizado_por = CURRENT_USER
-- WHERE id = 'elvira.pca.prefeituras'
--   AND procuradora_chave = 'elvira samara'
--   AND config_versao = '1'
--   AND motor = 'pca-rpps-v1'
--   AND estado = 'Em testes'
--   AND revisao = <REVISAO_RETORNADA_PELA_ATIVACAO>
-- RETURNING id, estado, motor, config_versao, revisao, atualizado_em, atualizado_por;
-- COMMIT;
