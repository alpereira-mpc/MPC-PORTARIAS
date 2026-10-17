-- Ferramentas MPC-PB - ampliacao do acesso a estagiarios (PostgreSQL / Supabase)
-- Migração aditiva. Executar APOS conferir backups e a estrutura do banco.
-- Compatível com o esquema mpc_portarias; nenhum dado do Minutas IA é alterado.
-- Mantém todos os usuários anteriores com categoria INSTITUCIONAL.

BEGIN;

-- Falhar integralmente, sem efeitos parciais, se o esquema esperado não existir.
DO $validar_estrutura$
BEGIN
    IF to_regclass('mpc_portarias.usuarios_acesso') IS NULL
       OR to_regclass('mpc_portarias.access_requests') IS NULL
       OR to_regclass('mpc_portarias.estagiarios_lotacoes') IS NULL THEN
        RAISE EXCEPTION 'Migração interrompida: uma ou mais tabelas esperadas não existem em mpc_portarias.';
    END IF;
END;
$validar_estrutura$;

ALTER TABLE mpc_portarias.usuarios_acesso
    ADD COLUMN IF NOT EXISTS categoria_acesso TEXT NOT NULL DEFAULT 'INSTITUCIONAL';

ALTER TABLE mpc_portarias.usuarios_acesso
    ADD COLUMN IF NOT EXISTS estagio_lotacao_id BIGINT;

ALTER TABLE mpc_portarias.access_requests
    ADD COLUMN IF NOT EXISTS tipo_solicitante TEXT NOT NULL DEFAULT 'INSTITUCIONAL';

UPDATE mpc_portarias.usuarios_acesso
SET categoria_acesso = 'INSTITUCIONAL'
WHERE categoria_acesso IS NULL OR categoria_acesso = '';

UPDATE mpc_portarias.access_requests
SET tipo_solicitante = 'INSTITUCIONAL'
WHERE tipo_solicitante IS NULL OR tipo_solicitante = '';

-- Regras aditivas para impedir categorias inválidas e vínculos inexistentes.
DO $validar_regras$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'mpc_portarias.usuarios_acesso'::regclass
          AND conname = 'usuarios_acesso_categoria_acesso_chk'
    ) THEN
        ALTER TABLE mpc_portarias.usuarios_acesso
            ADD CONSTRAINT usuarios_acesso_categoria_acesso_chk
            CHECK (categoria_acesso IN ('INSTITUCIONAL', 'ESTAGIARIO'));
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'mpc_portarias.access_requests'::regclass
          AND conname = 'access_requests_tipo_solicitante_chk'
    ) THEN
        ALTER TABLE mpc_portarias.access_requests
            ADD CONSTRAINT access_requests_tipo_solicitante_chk
            CHECK (tipo_solicitante IN ('INSTITUCIONAL', 'ESTAGIARIO'));
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'mpc_portarias.usuarios_acesso'::regclass
          AND conname = 'usuarios_acesso_estagio_lotacao_fk'
    ) THEN
        ALTER TABLE mpc_portarias.usuarios_acesso
            ADD CONSTRAINT usuarios_acesso_estagio_lotacao_fk
            FOREIGN KEY (estagio_lotacao_id)
            REFERENCES mpc_portarias.estagiarios_lotacoes(id)
            ON DELETE RESTRICT;
    END IF;
END;
$validar_regras$;

CREATE TABLE IF NOT EXISTS mpc_portarias.usuario_minutas_gabinetes (
    usuario_id BIGINT NOT NULL
        REFERENCES mpc_portarias.usuarios_acesso(id) ON DELETE CASCADE,
    gabinete TEXT NOT NULL
        CHECK (gabinete IN ('MTTF', 'ESPO', 'BLTC', 'SBBQ', 'IBMF', 'MASN', 'LAF')),
    PRIMARY KEY (usuario_id, gabinete)
);

CREATE INDEX IF NOT EXISTS usuarios_acesso_categoria_idx
    ON mpc_portarias.usuarios_acesso (categoria_acesso);

CREATE INDEX IF NOT EXISTS usuarios_acesso_estagio_idx
    ON mpc_portarias.usuarios_acesso (estagio_lotacao_id);

-- Restrição explícita de acesso direto via papéis públicos do Supabase.
-- O backend PostgreSQL de serviço deve possuir suas próprias permissões.
REVOKE ALL ON TABLE mpc_portarias.usuario_minutas_gabinetes
    FROM PUBLIC, anon, authenticated;
ALTER TABLE mpc_portarias.usuario_minutas_gabinetes
    ENABLE ROW LEVEL SECURITY;

COMMIT;

-- Conferência pós-migração: somente leitura.
SELECT
    (SELECT COUNT(*) FROM mpc_portarias.usuarios_acesso) AS usuarios_totais,
    (SELECT COUNT(*) FROM mpc_portarias.usuarios_acesso
       WHERE categoria_acesso = 'INSTITUCIONAL') AS usuarios_institucionais,
    (SELECT COUNT(*) FROM mpc_portarias.usuarios_acesso
       WHERE categoria_acesso = 'ESTAGIARIO') AS usuarios_estagiarios,
    (SELECT COUNT(*) FROM mpc_portarias.access_requests) AS solicitacoes_totais,
    (SELECT COUNT(*) FROM mpc_portarias.usuario_minutas_gabinetes) AS autorizacoes_extras;
