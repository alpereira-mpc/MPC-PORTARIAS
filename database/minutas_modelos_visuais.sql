-- Modelo visual por Procuradora e especialidade. Execução manual. Não executado pelo aplicativo.
-- Não altera pareceres, relatórios, extrações, objetos já armazenados nem modelos antigos vinculados a processo.
-- Um modelo ativo por especialidade. A substituição preserva as versões anteriores.
-- A tabela não entra na pesquisa jurídica nem no índice dos pareceres.
BEGIN;

CREATE TABLE IF NOT EXISTS mpc_portarias.minutas_modelos_visuais (
    id UUID PRIMARY KEY,
    procuradora_chave TEXT NOT NULL,
    especialidade_id TEXT NOT NULL,
    versao INTEGER NOT NULL CHECK (versao >= 1),
    ativo INTEGER NOT NULL DEFAULT 0 CHECK (ativo IN (0, 1)),
    nome TEXT NOT NULL,
    mime TEXT NOT NULL,
    tamanho BIGINT NOT NULL CHECK (tamanho BETWEEN 1 AND 10485760),
    path TEXT NOT NULL UNIQUE,
    sha256 TEXT NOT NULL CHECK (length(sha256) = 64),
    criado_em TEXT NOT NULL,
    criado_por TEXT NOT NULL,
    UNIQUE (procuradora_chave, especialidade_id, versao)
);

CREATE UNIQUE INDEX IF NOT EXISTS minutas_modelos_visuais_ativo_idx
    ON mpc_portarias.minutas_modelos_visuais (procuradora_chave, especialidade_id)
    WHERE ativo = 1;

CREATE INDEX IF NOT EXISTS minutas_modelos_visuais_escopo_idx
    ON mpc_portarias.minutas_modelos_visuais (procuradora_chave, especialidade_id, versao);

ALTER TABLE mpc_portarias.minutas_modelos_visuais ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON mpc_portarias.minutas_modelos_visuais
    FROM PUBLIC, anon, authenticated;

COMMIT;
