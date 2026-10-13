-- Fase 2B.1: documentos adicionais da Biblioteca de Referências.
-- Execute manualmente após database/minutas_referencias.sql.
-- Não altera, move ou exclui registros e objetos existentes.

CREATE TABLE IF NOT EXISTS mpc_portarias.minutas_documentos_complementares (
    id UUID PRIMARY KEY,
    referencia_id UUID NOT NULL REFERENCES mpc_portarias.minutas_referencias(id)
        ON DELETE CASCADE,
    tipo TEXT NOT NULL CHECK (tipo IN (
        'Relatório de Análise de Defesa',
        'Relatório de Complementação de Instrução',
        'Cota Ministerial',
        'Outros relatórios ou manifestações processuais'
    )),
    descricao TEXT NOT NULL DEFAULT '',
    posicao INTEGER NOT NULL CHECK (posicao > 0),
    data_documento DATE,
    nome TEXT NOT NULL,
    mime TEXT NOT NULL CHECK (mime IN (
        'application/pdf',
        'application/vnd.openxmlformats-officedocument.wordprocessingml.document'
    )),
    tamanho BIGINT NOT NULL CHECK (tamanho BETWEEN 1 AND 10485760),
    path TEXT NOT NULL UNIQUE,
    criado_em TEXT NOT NULL,
    criado_por TEXT NOT NULL,
    atualizado_em TEXT NOT NULL,
    atualizado_por TEXT NOT NULL,
    CONSTRAINT minutas_documentos_complementares_ordem_key
        UNIQUE (referencia_id, posicao) DEFERRABLE INITIALLY DEFERRED,
    CHECK (
        tipo <> 'Outros relatórios ou manifestações processuais'
        OR btrim(descricao) <> ''
    )
);

CREATE INDEX IF NOT EXISTS minutas_documentos_complementares_ref_idx
    ON mpc_portarias.minutas_documentos_complementares (
        referencia_id, posicao, id
    );

ALTER TABLE mpc_portarias.minutas_documentos_complementares
    ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON mpc_portarias.minutas_documentos_complementares
    FROM PUBLIC, anon, authenticated;
