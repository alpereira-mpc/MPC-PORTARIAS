-- Execução manual, uma única vez, após backup restaurável conferido.
-- Não é chamado pelo aplicativo. Execute primeiro em cópia isolada do banco.
BEGIN;

-- Pré-condições: relações instaladas e catálogo canônico disponível.
DO $$
BEGIN
    IF to_regclass('mpc_portarias.minutas_especialidades') IS NULL
       OR to_regclass('mpc_portarias.minutas_classificacoes') IS NULL
       OR to_regclass('mpc_portarias.minutas_acervo_vinculos') IS NULL THEN
        RAISE EXCEPTION 'Estrutura de Minutas IA ausente; interrompido sem alterações.';
    END IF;
    IF EXISTS (
        SELECT 1 FROM mpc_portarias.minutas_classificacoes
        WHERE especialidade_id IN ('elvira.pca.governo', 'elvira.pca.gestao')
          AND estado = 'CLASSIFICADO' AND classe <> 'PCA'
    ) THEN
        RAISE EXCEPTION 'Classificação legada de Prefeitura fora de PCA; revisar antes da unificação.';
    END IF;
END $$;

-- Inventário prévio: registre este resultado junto à evidência de backup.
SELECT especialidade_id, estado, COUNT(*) AS processos
FROM mpc_portarias.minutas_classificacoes
WHERE especialidade_id IN ('elvira.pca.governo', 'elvira.pca.gestao', 'elvira.pca.prefeituras')
GROUP BY especialidade_id, estado ORDER BY especialidade_id, estado;

-- Catálogo canônico. Os IDs legados permanecem para manter a trilha histórica.
INSERT INTO mpc_portarias.minutas_especialidades
    (id, procuradora_chave, grupo, nome, perfil, estado, config_versao, motor, config_json,
     casos_testados, ultima_validacao, pendencias, resultados_testes, revisao, atualizado_em, atualizado_por)
VALUES
    ('elvira.pca.prefeituras', 'elvira samara', 'PCA', 'Prefeituras Municipais', 'PREFEITURA',
     'A iniciar', '1', '', '{}', 0, NULL, '', '', 1, '', '')
ON CONFLICT (id) DO NOTHING;

-- Backup auditável e idempotente para reversão. Não contém arquivos nem extrações.
CREATE TABLE IF NOT EXISTS mpc_portarias.minutas_unificacao_prefeituras_backup (
    referencia_id UUID PRIMARY KEY,
    especialidade_anterior TEXT NOT NULL,
    vinculos_anteriores_json TEXT NOT NULL,
    criado_em TIMESTAMPTZ NOT NULL DEFAULT now()
);

INSERT INTO mpc_portarias.minutas_unificacao_prefeituras_backup
    (referencia_id, especialidade_anterior, vinculos_anteriores_json)
SELECT mc.referencia_id, mc.especialidade_id,
       COALESCE(jsonb_agg(jsonb_build_object('especialidade_id', mv.especialidade_id,
                                              'justificativa', mv.justificativa))
                FILTER (WHERE mv.especialidade_id IN ('elvira.pca.governo', 'elvira.pca.gestao')), '[]'::jsonb)::text
FROM mpc_portarias.minutas_classificacoes mc
LEFT JOIN mpc_portarias.minutas_acervo_vinculos mv ON mv.referencia_id = mc.referencia_id
WHERE mc.especialidade_id IN ('elvira.pca.governo', 'elvira.pca.gestao')
GROUP BY mc.referencia_id, mc.especialidade_id
ON CONFLICT (referencia_id) DO NOTHING;

-- Primeiro garante o vínculo canônico; depois remove somente os vínculos antigos duplicados.
INSERT INTO mpc_portarias.minutas_acervo_vinculos (referencia_id, especialidade_id, justificativa)
SELECT mv.referencia_id, 'elvira.pca.prefeituras',
       'Unificação técnica de Prefeituras; vínculo original preservado em minutas_unificacao_prefeituras_backup.'
FROM mpc_portarias.minutas_acervo_vinculos mv
WHERE mv.especialidade_id IN ('elvira.pca.governo', 'elvira.pca.gestao')
ON CONFLICT (referencia_id, especialidade_id) DO NOTHING;

UPDATE mpc_portarias.minutas_classificacoes
SET especialidade_id = 'elvira.pca.prefeituras',
    perfil = 'PREFEITURA',
    modalidade = CASE
        WHEN modalidade IN ('Prefeituras / Contas de Governo', 'Prefeituras / Contas de Gestão')
        THEN modalidade
        ELSE modalidade
    END,
    revisao = revisao + 1,
    atualizado_em = now()::text,
    atualizado_por = 'migração controlada: unificação de Prefeituras'
WHERE especialidade_id IN ('elvira.pca.governo', 'elvira.pca.gestao');

DELETE FROM mpc_portarias.minutas_acervo_vinculos
WHERE especialidade_id IN ('elvira.pca.governo', 'elvira.pca.gestao');

-- Reconciliação: nenhuma referência, arquivo ou extração é excluída por este roteiro.
SELECT especialidade_id, estado, COUNT(*) AS processos
FROM mpc_portarias.minutas_classificacoes
WHERE especialidade_id IN ('elvira.pca.governo', 'elvira.pca.gestao', 'elvira.pca.prefeituras')
GROUP BY especialidade_id, estado ORDER BY especialidade_id, estado;

COMMIT;

-- REVERSÃO (somente após validação do backup acima; executar em nova transação):
-- BEGIN;
-- UPDATE mpc_portarias.minutas_classificacoes mc
-- SET especialidade_id=b.especialidade_anterior, revisao=mc.revisao+1,
--     atualizado_em=now()::text, atualizado_por='reversão controlada: unificação de Prefeituras'
-- FROM mpc_portarias.minutas_unificacao_prefeituras_backup b
-- WHERE mc.referencia_id=b.referencia_id AND mc.especialidade_id='elvira.pca.prefeituras';
-- DELETE FROM mpc_portarias.minutas_acervo_vinculos
-- WHERE especialidade_id='elvira.pca.prefeituras'
--   AND referencia_id IN (SELECT referencia_id FROM mpc_portarias.minutas_unificacao_prefeituras_backup);
-- INSERT INTO mpc_portarias.minutas_acervo_vinculos (referencia_id, especialidade_id, justificativa)
-- SELECT b.referencia_id, item->>'especialidade_id', item->>'justificativa'
-- FROM mpc_portarias.minutas_unificacao_prefeituras_backup b
-- CROSS JOIN LATERAL jsonb_array_elements(b.vinculos_anteriores_json::jsonb) item
-- ON CONFLICT (referencia_id, especialidade_id) DO NOTHING;
-- COMMIT;
