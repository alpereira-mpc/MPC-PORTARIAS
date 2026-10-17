-- Minutas IA multiprocurador. Aditivo e idempotente.
-- Não apaga, não trunca e não reescreve pareceres, extrações ou embeddings.
-- A chave histórica "elvira samara" continua sendo o acervo ESPO.
-- Registros com outra chave não são atribuídos automaticamente.
-- Executar no esquema mpc_portarias do projeto já usado pelas Ferramentas MPC-PB.
-- Reversão de interface: Secrets MINUTAS_NAV_LEGADA = true.
-- Reversão destes objetos: não é necessária para voltar à navegação legada.
-- Não remover a tabela depois que houver configuração dos novos gabinetes.

BEGIN;

CREATE TABLE IF NOT EXISTS mpc_portarias.minutas_procuradores (
    sigla TEXT PRIMARY KEY,
    nome TEXT NOT NULL,
    nome_exibicao TEXT NOT NULL,
    chave TEXT NOT NULL UNIQUE,
    prefixo TEXT NOT NULL UNIQUE,
    ativo INTEGER NOT NULL DEFAULT 1 CHECK (ativo IN (0, 1)),
    legado INTEGER NOT NULL DEFAULT 0 CHECK (legado IN (0, 1)),
    config_json TEXT NOT NULL DEFAULT '{}',
    redacao_json TEXT NOT NULL DEFAULT '{}',
    visual_json TEXT NOT NULL DEFAULT '{}',
    criado_em TEXT NOT NULL,
    atualizado_em TEXT NOT NULL
);

INSERT INTO mpc_portarias.minutas_procuradores (
    sigla, nome, nome_exibicao, chave, prefixo, ativo, legado,
    config_json, redacao_json, visual_json, criado_em, atualizado_em
) VALUES
    ('ESPO', 'Elvira Samara Pereira de Oliveira', 'Elvira Samara', 'elvira samara', 'elvira', 1, 1,
     '{"origem":"piloto"}', '{}', '{"modelo":"legado"}',
     to_char(clock_timestamp() AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.MS"+00:00"'),
     to_char(clock_timestamp() AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.MS"+00:00"')),
    ('MTTF', 'Marcílio Toscano Franca Filho', 'Marcílio Toscano Franca Filho', 'mttf', 'mttf', 1, 0,
     '{}', '{}', '{"modelo":"institucional"}',
     to_char(clock_timestamp() AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.MS"+00:00"'),
     to_char(clock_timestamp() AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.MS"+00:00"')),
    ('BLTC', 'Bradson Tibério Luna Camelo', 'Bradson Tibério Luna Camelo', 'bltc', 'bltc', 1, 0,
     '{}', '{}', '{"modelo":"institucional"}',
     to_char(clock_timestamp() AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.MS"+00:00"'),
     to_char(clock_timestamp() AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.MS"+00:00"')),
    ('SBBQ', 'Sheyla Barreto Braga de Queiroz', 'Sheyla Barreto Braga de Queiroz', 'sbbq', 'sbbq', 1, 0,
     '{}', '{}', '{"modelo":"institucional"}',
     to_char(clock_timestamp() AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.MS"+00:00"'),
     to_char(clock_timestamp() AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.MS"+00:00"')),
    ('IBMF', 'Isabella Barbosa Marinho Falcão', 'Isabella Barbosa Marinho Falcão', 'ibmf', 'ibmf', 1, 0,
     '{}', '{}', '{"modelo":"institucional"}',
     to_char(clock_timestamp() AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.MS"+00:00"'),
     to_char(clock_timestamp() AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.MS"+00:00"')),
    ('MASN', 'Manoel Antônio dos Santos Neto', 'Manoel Antônio dos Santos Neto', 'masn', 'masn', 1, 0,
     '{}', '{}', '{"modelo":"institucional"}',
     to_char(clock_timestamp() AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.MS"+00:00"'),
     to_char(clock_timestamp() AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.MS"+00:00"')),
    ('LAF', 'Luciano Andrade Farias', 'Luciano Andrade Farias', 'laf', 'laf', 1, 0,
     '{}', '{}', '{"modelo":"institucional"}',
     to_char(clock_timestamp() AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.MS"+00:00"'),
     to_char(clock_timestamp() AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.MS"+00:00"'))
ON CONFLICT (sigla) DO NOTHING;

CREATE TABLE IF NOT EXISTS mpc_portarias.minutas_procurador_config (
    sigla TEXT NOT NULL REFERENCES mpc_portarias.minutas_procuradores(sigla),
    especialidade_id TEXT NOT NULL DEFAULT '',
    juridico_json TEXT NOT NULL DEFAULT '{}',
    redacao_json TEXT NOT NULL DEFAULT '{}',
    visual_json TEXT NOT NULL DEFAULT '{}',
    atualizado_em TEXT NOT NULL,
    atualizado_por TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (sigla, especialidade_id)
);

-- Associação explícita somente quando a chave já comprova o piloto.
-- Linhas ambíguas permanecem com procurador_sigla nulo.
ALTER TABLE mpc_portarias.minutas_referencias
    ADD COLUMN IF NOT EXISTS procurador_sigla TEXT;

UPDATE mpc_portarias.minutas_referencias
   SET procurador_sigla = 'ESPO'
 WHERE procuradora_chave = 'elvira samara'
   AND procurador_sigla IS NULL;

CREATE INDEX IF NOT EXISTS minutas_referencias_procurador_idx
    ON mpc_portarias.minutas_referencias (procurador_sigla, procuradora_chave);

ALTER TABLE mpc_portarias.minutas_procuradores ENABLE ROW LEVEL SECURITY;
ALTER TABLE mpc_portarias.minutas_procurador_config ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON mpc_portarias.minutas_procuradores, mpc_portarias.minutas_procurador_config
    FROM PUBLIC, anon, authenticated;

COMMIT;
