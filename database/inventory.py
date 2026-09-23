"""Application table inventory derived from current schema code.

Names come from database/store.py, postgresql_schema.sql, access, audit,
memorandos, agenda and oficios. Do not invent extra tables here.
"""

# Core Portarias catalog, numbering, snapshots and administrative deletion.
PORTARIAS_TABLES = (
    "procuradores",
    "funcoes",
    "assentos",
    "motivos_afastamento",
    "bases_legais",
    "funcoes_institucionais",
    "configuracoes",
    "sequencias",
    "sequencia_baselines",
    "portarias",
    "substituicoes",
    "eventos",
    "audit_log",
    "exportacoes",
    "audit_arquivos",
)

ACCESS_TABLES = ("usuarios_acesso", "usuario_gabinetes", "access_requests")

MEMORANDOS_TABLES = (
    "servidores",
    "servidores_importacoes",
    "memorandos",
    "memorandos_substituicao",
    "memorandos_substituicao_etapas",
    "memorandos_arquivos",
)

AGENDA_TABLES = ("agenda_compromissos", "agenda_compromisso_procuradores")

OFICIOS_TABLES = (
    "oficio_series",
    "oficio_sequencias",
    "oficios",
    "oficio_destinatarios",
    "oficio_arquivos",
    "oficio_movimentacoes",
    "oficio_numeros_liberados",
    "oficio_quarentena",
)

REPRESENTACOES_TABLES = (
    "representacoes",
    "representacao_integrantes",
    "representacao_andamentos",
    "representacao_documentos",
)

OUVIDORIA_TABLES = (
    "ouvidoria_sequencias",
    "ouvidoria_manifestacoes",
    "ouvidoria_integrantes",
    "ouvidoria_andamentos",
    "ouvidoria_providencias",
    "ouvidoria_documentos",
)

AUDIT_TABLES = ("auditoria_eventos",)

INTERNAL_COLLABORATION_TABLES = (
    "notas_internas",
    "encaminhamentos_internos",
)

POSTGRES_ONLY_TABLES = ("schema_migrations", "backup_snapshots")

APPLICATION_TABLES = (
    PORTARIAS_TABLES
    + ACCESS_TABLES
    + MEMORANDOS_TABLES
    + AGENDA_TABLES
    + OFICIOS_TABLES
    + REPRESENTACOES_TABLES
    + OUVIDORIA_TABLES
    + AUDIT_TABLES
    + INTERNAL_COLLABORATION_TABLES
    + POSTGRES_ONLY_TABLES
)

ESSENTIAL_TABLES = (
    PORTARIAS_TABLES
    + ACCESS_TABLES
    + MEMORANDOS_TABLES
    + AGENDA_TABLES
    + OFICIOS_TABLES
    + REPRESENTACOES_TABLES
    + OUVIDORIA_TABLES
    + AUDIT_TABLES
    + INTERNAL_COLLABORATION_TABLES
)

SCHEMA_MARKERS = (
    "acesso_schema_v1",
    "acesso_solicitacoes_schema_v1",
    "memorandos_schema_v1",
    "auditoria_schema_v1",
    "oficios_schema_v1",
    "oficios_schema_v2",
    "oficios_schema_v3",
    "colaboracao_interna_schema_v1",
    "representacoes_schema_v1",
    "ouvidoria_schema_v1",
)

ESSENTIAL_COLUMNS = {
    "usuarios_acesso": ("email", "perfil", "pode_admin", "pode_memorandos", "pode_representacoes", "pode_ouvidoria", "protegido", "tema"),
    "ouvidoria_manifestacoes": ("numero_interno", "situacao", "classificacao_acesso", "representacao_id"),
    "usuario_gabinetes": ("usuario_id", "gabinete"),
    "access_requests": ("nome", "email", "gabinete", "status", "created_at"),
    "auditoria_eventos": ("evento", "modulo", "resultado", "criado_em"),
    "portarias": ("numero", "ano", "status", "payload", "docx", "pdf"),
    "memorandos": ("status", "payload", "numero_oficial"),
    "memorandos_arquivos": ("nome", "tipo", "sha256", "conteudo"),
    "oficios": ("direcao", "status", "payload"),
    "notas_internas": (
        "origem_modulo",
        "origem_id",
        "autor_id",
        "texto",
        "criado_em",
    ),
    "encaminhamentos_internos": (
        "origem_modulo",
        "origem_id",
        "destinatario_id",
        "status",
        "criado_em",
    ),
    "oficio_arquivos": ("nome", "tipo", "conteudo"),
    "agenda_compromissos": ("tipo", "inicio", "payload"),
    "sequencias": ("ano", "ultimo"),
    "sequencia_baselines": ("ano", "baseline"),
}

BLOB_COLUMNS = {
    "portarias": ("docx", "pdf"),
    "memorandos_arquivos": ("conteudo",),
    "oficio_arquivos": ("conteudo",),
    "representacao_documentos": ("arquivo",),
    "ouvidoria_documentos": ("arquivo",),
    "oficio_quarentena": ("arquivos",),
    "backup_snapshots": ("conteudo",),
}

# Engine-specific dump of previous backups; embedding it would recurse and
# duplicate BYTEA dumps. Paths/SHA-256 remain in audit_log when present.
BACKUP_EXCLUDED_TABLES = frozenset({"backup_snapshots", "sqlite_sequence"})

DOCUMENT_FOLDERS = {
    "portarias": "documentos/portarias",
    "memorandos_arquivos": "documentos/memorandos",
    "oficio_arquivos": "documentos/oficios",
    "oficio_quarentena": "documentos/oficios_quarentena",
}


def essential_tables(backend):
    tables = list(ESSENTIAL_TABLES)
    if backend == "postgresql":
        tables.append("schema_migrations")
        tables.append("backup_snapshots")
    return tables


def backup_tables(backend, existing):
    wanted = set(APPLICATION_TABLES)
    if backend != "postgresql":
        wanted -= set(POSTGRES_ONLY_TABLES)
    return tuple(
        table
        for table in APPLICATION_TABLES
        if table in wanted
        and table in existing
        and table not in BACKUP_EXCLUDED_TABLES
    )
