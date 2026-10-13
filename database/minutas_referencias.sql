-- Fase 2A: estruturas isoladas para a Biblioteca de Referências do Minutas IA.
-- Execute manualmente no SQL Editor do projeto Supabase correto.
-- Idempotente: não remove nem altera tabelas ou buckets existentes.

CREATE TABLE IF NOT EXISTS mpc_portarias.minutas_referencias (
    id UUID PRIMARY KEY,
    procuradora TEXT NOT NULL,
    procuradora_chave TEXT NOT NULL,
    numero_processo TEXT NOT NULL,
    processo_chave TEXT NOT NULL,
    exercicio INTEGER NOT NULL CHECK (exercicio BETWEEN 1900 AND 2200),
    subtipo TEXT NOT NULL CHECK (subtipo IN (
        'Prefeitura',
        'Câmara Municipal',
        'Instituto/Autarquia',
        'Secretaria Municipal ou Estadual'
    )),
    auditoria_nome TEXT NOT NULL,
    auditoria_mime TEXT NOT NULL CHECK (auditoria_mime = 'application/pdf'),
    auditoria_tamanho BIGINT NOT NULL CHECK (
        auditoria_tamanho BETWEEN 1 AND 10485760
    ),
    auditoria_path TEXT NOT NULL UNIQUE,
    parecer_nome TEXT NOT NULL,
    parecer_mime TEXT NOT NULL CHECK (parecer_mime IN (
        'application/pdf',
        'application/vnd.openxmlformats-officedocument.wordprocessingml.document'
    )),
    parecer_tamanho BIGINT NOT NULL CHECK (
        parecer_tamanho BETWEEN 1 AND 10485760
    ),
    parecer_path TEXT NOT NULL UNIQUE,
    -- ISO 8601 TEXT preserves the existing Store contract. A future conversion
    -- to TIMESTAMPTZ requires an explicit, separately validated migration.
    criado_em TEXT NOT NULL,
    criado_por TEXT NOT NULL,
    atualizado_em TEXT NOT NULL,
    atualizado_por TEXT NOT NULL,
    UNIQUE (procuradora_chave, processo_chave)
);

CREATE INDEX IF NOT EXISTS minutas_referencias_lista_idx
    ON mpc_portarias.minutas_referencias (
        exercicio DESC, subtipo, numero_processo
    );

REVOKE ALL ON mpc_portarias.minutas_referencias FROM anon, authenticated;

INSERT INTO storage.buckets (
    id, name, public, file_size_limit, allowed_mime_types
) VALUES (
    'minutas-referencias',
    'minutas-referencias',
    false,
    10485760,
    ARRAY[
        'application/pdf',
        'application/vnd.openxmlformats-officedocument.wordprocessingml.document'
    ]
) ON CONFLICT (id) DO NOTHING;

-- Nenhuma policy para anon/authenticated é criada em storage.objects.
-- O bucket permanece privado e é acessado somente pelo backend com service_role.
