-- Implantação MANUAL, após inventário e backup recuperável. Não executada pelo app.
-- Aditiva: preserva IDs, caminhos, extrações e índice GIN existentes.
BEGIN;
CREATE TABLE IF NOT EXISTS mpc_portarias.minutas_especialidades (
    id TEXT PRIMARY KEY,
    procuradora_chave TEXT NOT NULL,
    grupo TEXT NOT NULL,
    nome TEXT NOT NULL,
    perfil TEXT NOT NULL DEFAULT '',
    estado TEXT NOT NULL DEFAULT 'A iniciar' CHECK (estado IN (
        'A iniciar','Em alimentação','Em testes','Funcional com ajustes','Validada')),
    config_versao TEXT NOT NULL DEFAULT '1',
    motor TEXT NOT NULL DEFAULT '',
    config_json TEXT NOT NULL DEFAULT '{}',
    casos_testados INTEGER NOT NULL DEFAULT 0 CHECK (casos_testados >= 0),
    ultima_validacao TEXT,
    pendencias TEXT NOT NULL DEFAULT '',
    resultados_testes TEXT NOT NULL DEFAULT '',
    revisao INTEGER NOT NULL DEFAULT 1,
    atualizado_em TEXT NOT NULL DEFAULT '',
    atualizado_por TEXT NOT NULL DEFAULT ''
);
INSERT INTO mpc_portarias.minutas_especialidades (id,procuradora_chave,grupo,nome,perfil,estado,motor)
SELECT 'elvira.'||id,'elvira samara',grupo,nome,perfil,
       CASE WHEN id='pca.rpps' THEN 'Funcional com ajustes' ELSE 'A iniciar' END,
       CASE WHEN id='pca.rpps' THEN 'pca-rpps-v1' ELSE '' END
FROM (VALUES
 ('pca.rpps','PCA','Institutos Previdenciários (RPPS)','RPPS'),
 ('pca.camaras','PCA','Câmaras Municipais','CAMARA'),
 ('pca.prefeituras','PCA','Prefeituras Municipais','PREFEITURA'),
 ('pca.secretarias_municipais','PCA','Secretarias Municipais','SECRETARIA_MUNICIPAL'),
 ('pca.orgaos_estaduais','PCA','Secretarias e Órgãos Estaduais','ORGAO_ESTADUAL'),
 ('pca.fundos','PCA','Fundos Públicos','FUNDO'),
 ('pca.autarquias','PCA','Demais Autarquias e Fundações','AUTARQUIA_FUNDACAO'),
 ('pca.outras','PCA','Outras Entidades Jurisdicionadas','OUTRA'),
 ('pessoal.aposentadorias','ATOS_PESSOAL','Aposentadorias',''),
 ('pessoal.pensoes','ATOS_PESSOAL','Pensões',''),
 ('pessoal.reformas','ATOS_PESSOAL','Reformas',''),
 ('pessoal.admissoes','ATOS_PESSOAL','Admissões de Pessoal',''),
 ('tema.concursos','TEMATICO','Concursos Públicos',''),
 ('inspecao','INSPECAO','Inspeção Especial de Contas',''),
 ('tema.licitacoes','TEMATICO','Licitações',''),
 ('tema.contratos','TEMATICO','Contratos Administrativos',''),
 ('tema.convenios','TEMATICO','Convênios e Instrumentos Congêneres',''),
 ('tce','TCE','Tomada de Contas Especial',''),
 ('denuncias','DENUNCIA','Denúncias',''),
 ('representacoes','REPRESENTACAO','Representações',''),
 ('consultas','CONSULTA','Consultas',''),
 ('recursos','RECURSO','Recursos',''),
 ('rescisorios','RESCISORIO','Pedidos Rescisórios','')
) AS initial(id,grupo,nome,perfil) WHERE TRUE ON CONFLICT(id) DO NOTHING;

CREATE TABLE IF NOT EXISTS mpc_portarias.minutas_classificacoes (
    referencia_id UUID PRIMARY KEY REFERENCES mpc_portarias.minutas_referencias(id) ON DELETE CASCADE,
    especialidade_id TEXT REFERENCES mpc_portarias.minutas_especialidades(id),
    estado TEXT NOT NULL CHECK (estado IN ('CLASSIFICADO','PENDENTE','NAO_CLASSIFICADO')),
    classe TEXT NOT NULL DEFAULT '', modalidade TEXT NOT NULL DEFAULT '',
    perfil TEXT NOT NULL DEFAULT '', entidade TEXT NOT NULL DEFAULT '',
    temas_json TEXT NOT NULL DEFAULT '[]', evidencia_json TEXT NOT NULL DEFAULT '[]',
    motivo TEXT NOT NULL DEFAULT '', origem TEXT NOT NULL,
    revisao INTEGER NOT NULL DEFAULT 1,
    atualizado_em TEXT NOT NULL, atualizado_por TEXT NOT NULL,
    CHECK (estado <> 'CLASSIFICADO' OR (especialidade_id IS NOT NULL AND classe <> ''))
);
CREATE TABLE IF NOT EXISTS mpc_portarias.minutas_acervo_vinculos (
    referencia_id UUID NOT NULL REFERENCES mpc_portarias.minutas_referencias(id) ON DELETE CASCADE,
    especialidade_id TEXT NOT NULL REFERENCES mpc_portarias.minutas_especialidades(id),
    justificativa TEXT NOT NULL,
    PRIMARY KEY(referencia_id,especialidade_id)
);
CREATE TABLE IF NOT EXISTS mpc_portarias.minutas_especialidades_eventos (
    id UUID PRIMARY KEY, referencia_id UUID, especialidade_id TEXT,
    acao TEXT NOT NULL, anterior_json TEXT NOT NULL, posterior_json TEXT NOT NULL,
    lote TEXT NOT NULL DEFAULT '', criado_em TEXT NOT NULL, criado_por TEXT NOT NULL
);
-- Novos processos podem começar por apenas um documento. Nenhum valor legado é alterado.
ALTER TABLE mpc_portarias.minutas_referencias
    ALTER COLUMN subtipo DROP NOT NULL,
    ALTER COLUMN auditoria_nome DROP NOT NULL, ALTER COLUMN auditoria_mime DROP NOT NULL,
    ALTER COLUMN auditoria_tamanho DROP NOT NULL, ALTER COLUMN auditoria_path DROP NOT NULL,
    ALTER COLUMN parecer_nome DROP NOT NULL, ALTER COLUMN parecer_mime DROP NOT NULL,
    ALTER COLUMN parecer_tamanho DROP NOT NULL, ALTER COLUMN parecer_path DROP NOT NULL;
CREATE TABLE IF NOT EXISTS mpc_portarias.minutas_acervo_documentos (
    id UUID PRIMARY KEY,
    referencia_id UUID NOT NULL REFERENCES mpc_portarias.minutas_referencias(id) ON DELETE CASCADE,
    papel TEXT NOT NULL CHECK (papel IN ('auditoria','complementar','parecer','modelo')),
    tipo TEXT NOT NULL, descricao TEXT NOT NULL DEFAULT '', posicao INTEGER NOT NULL,
    data_documento DATE, nome TEXT NOT NULL, mime TEXT NOT NULL,
    tamanho BIGINT NOT NULL CHECK (tamanho BETWEEN 1 AND 10485760),
    path TEXT NOT NULL UNIQUE, sha256 TEXT NOT NULL CHECK(length(sha256)=64),
    criado_em TEXT NOT NULL, criado_por TEXT NOT NULL,
    atualizado_em TEXT NOT NULL, atualizado_por TEXT NOT NULL,
    UNIQUE(referencia_id,sha256)
);
CREATE INDEX IF NOT EXISTS minutas_classificacoes_escopo_idx
 ON mpc_portarias.minutas_classificacoes(especialidade_id,estado,referencia_id);
CREATE INDEX IF NOT EXISTS minutas_vinculos_escopo_idx
 ON mpc_portarias.minutas_acervo_vinculos(especialidade_id,referencia_id);
CREATE INDEX IF NOT EXISTS minutas_extracoes_hash_idx
 ON mpc_portarias.minutas_extracoes(arquivo_sha256,referencia_id);
CREATE INDEX IF NOT EXISTS minutas_acervo_documentos_hash_idx
 ON mpc_portarias.minutas_acervo_documentos(sha256,referencia_id);
CREATE OR REPLACE VIEW mpc_portarias.minutas_documentos_catalogo AS
 SELECT id::text||':auditoria' AS id,id AS referencia_id,'auditoria'::text AS papel,
 'Relatório da Auditoria'::text AS tipo,auditoria_nome AS nome,auditoria_mime AS mime,
 auditoria_tamanho AS tamanho,auditoria_path AS path FROM mpc_portarias.minutas_referencias WHERE auditoria_path IS NOT NULL
 UNION ALL SELECT id::text||':parecer',id,'parecer','Parecer definitivo',parecer_nome,parecer_mime,
 parecer_tamanho,parecer_path FROM mpc_portarias.minutas_referencias WHERE parecer_path IS NOT NULL
 UNION ALL SELECT id::text,referencia_id,'complementar',tipo,nome,mime,tamanho,path
 FROM mpc_portarias.minutas_documentos_complementares
 UNION ALL SELECT id::text,referencia_id,papel,tipo,nome,mime,tamanho,path
 FROM mpc_portarias.minutas_acervo_documentos;
ALTER TABLE mpc_portarias.minutas_especialidades ENABLE ROW LEVEL SECURITY;
ALTER TABLE mpc_portarias.minutas_classificacoes ENABLE ROW LEVEL SECURITY;
ALTER TABLE mpc_portarias.minutas_acervo_vinculos ENABLE ROW LEVEL SECURITY;
ALTER TABLE mpc_portarias.minutas_especialidades_eventos ENABLE ROW LEVEL SECURITY;
ALTER TABLE mpc_portarias.minutas_acervo_documentos ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON mpc_portarias.minutas_especialidades,mpc_portarias.minutas_classificacoes,
 mpc_portarias.minutas_acervo_vinculos,mpc_portarias.minutas_especialidades_eventos,
 mpc_portarias.minutas_acervo_documentos,mpc_portarias.minutas_documentos_catalogo
 FROM PUBLIC,anon,authenticated;
COMMIT;
