-- Fase 2B.2: texto extraído dos documentos da Biblioteca de Referências.
-- Execução manual após as estruturas das Fases 2A e 2B.1.
-- Não modifica documentos nem registros existentes.

CREATE TABLE IF NOT EXISTS mpc_portarias.minutas_extracoes (
    id UUID PRIMARY KEY,
    referencia_id UUID NOT NULL REFERENCES mpc_portarias.minutas_referencias(id)
        ON DELETE CASCADE,
    documento_id UUID,
    papel TEXT NOT NULL CHECK (papel IN ('auditoria', 'complementar', 'parecer')),
    tipo TEXT NOT NULL,
    posicao INTEGER,
    procuradora TEXT NOT NULL,
    procuradora_chave TEXT NOT NULL,
    arquivo_nome TEXT NOT NULL,
    arquivo_mime TEXT NOT NULL,
    arquivo_tamanho BIGINT NOT NULL CHECK (
        arquivo_tamanho BETWEEN 1 AND 10485760
    ),
    arquivo_path TEXT NOT NULL UNIQUE,
    arquivo_sha256 TEXT NOT NULL CHECK (length(arquivo_sha256) = 64),
    extrator_versao TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('PROCESSADO', 'ATENCAO')),
    texto_integral TEXT NOT NULL DEFAULT '',
    unidades_json TEXT NOT NULL DEFAULT '[]',
    secoes_json TEXT NOT NULL DEFAULT '[]',
    erro TEXT NOT NULL DEFAULT '',
    processado_em TEXT NOT NULL,
    processado_por TEXT NOT NULL,
    atualizado_em TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS minutas_extracoes_referencia_idx
    ON mpc_portarias.minutas_extracoes (referencia_id, papel, posicao);

CREATE INDEX IF NOT EXISTS minutas_extracoes_procuradora_idx
    ON mpc_portarias.minutas_extracoes (
        procuradora_chave, referencia_id, papel, posicao
    );

ALTER TABLE mpc_portarias.minutas_extracoes
    ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON mpc_portarias.minutas_extracoes
    FROM PUBLIC, anon, authenticated;

