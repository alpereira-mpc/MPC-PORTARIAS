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

ESTAGIARIOS_TABLES = ("estagiarios_lotacoes",)

AGENDA_TABLES = (
    "agenda_compromissos",
    "agenda_compromisso_procuradores",
    "agenda_compromissos_viagens",
    "agenda_afastamentos",
    "agenda_afastamentos_viagens",
)

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

LEGACY_OPTIONAL_TABLES = ("alertas_atencao",)

RECORD_ENGAGEMENT_TABLES = ("registros_seguidos", "avisos_usuario")

IA_TABLES = ("ia_telemetria",)

TAREFAS_TABLES = ("tarefas", "tarefas_checklist", "tarefas_lembretes")
PETICOES_TABLES = (
    "peticoes",
    "peticoes_signatarios",
    "peticoes_pedidos",
    "peticoes_andamentos",
    "peticoes_documentos",
)
REPORT_TABLES = (
    "relatorios_institucionais",
    "relatorios_institucionais_pdf",
    "relatorios_institucionais_envios",
    "relatorios_institucionais_envio_destinatarios",
)
TRAMITA_TABLES = ("tramita_importacoes", "tramita_movimentacoes", "tramita_estoque")
NOTIFICATION_TABLES = ("notificacoes_email", "notificacao_destinatarios")

POSTGRES_ONLY_TABLES = ("schema_migrations", "backup_snapshots")

APPLICATION_TABLES = (
    PORTARIAS_TABLES
    + ACCESS_TABLES
    + MEMORANDOS_TABLES
    + ESTAGIARIOS_TABLES
    + AGENDA_TABLES
    + OFICIOS_TABLES
    + REPRESENTACOES_TABLES
    + OUVIDORIA_TABLES
    + AUDIT_TABLES
    + INTERNAL_COLLABORATION_TABLES
    + RECORD_ENGAGEMENT_TABLES
    + LEGACY_OPTIONAL_TABLES
    + IA_TABLES
    + TAREFAS_TABLES
    + PETICOES_TABLES
    + REPORT_TABLES
    + TRAMITA_TABLES
    + NOTIFICATION_TABLES
    + POSTGRES_ONLY_TABLES
)

ESSENTIAL_TABLES = (
    PORTARIAS_TABLES
    + ACCESS_TABLES
    + MEMORANDOS_TABLES
    + ESTAGIARIOS_TABLES
    + AGENDA_TABLES
    + OFICIOS_TABLES
    + REPRESENTACOES_TABLES
    + OUVIDORIA_TABLES
    + AUDIT_TABLES
    + INTERNAL_COLLABORATION_TABLES
    + RECORD_ENGAGEMENT_TABLES
    + IA_TABLES
    + TAREFAS_TABLES
    + PETICOES_TABLES
    + REPORT_TABLES
    + TRAMITA_TABLES
    + NOTIFICATION_TABLES
)

SCHEMA_MARKERS = (
    "acesso_schema_v1",
    "acesso_solicitacoes_schema_v1",
    "memorandos_schema_v1",
    "estagiarios_schema_v1",
    "auditoria_schema_v1",
    "oficios_schema_v1",
    "oficios_schema_v2",
    "oficios_schema_v3",
    "colaboracao_interna_schema_v1",
    "tarefas_schema_v3",
    "record_engagement_schema_v1",
    "representacoes_schema_v1",
    "ouvidoria_schema_v1",
    "ia_telemetria_schema_v1",
)

ESSENTIAL_COLUMNS = {
    "usuarios_acesso": (
        "email",
        "perfil",
        "pode_admin",
        "pode_memorandos",
        "pode_representacoes",
        "pode_ouvidoria",
        "pode_representacoes_registrar_protocolo",
        "pode_representacoes_enviar_comunicacao",
        "pode_comunicacoes_configurar_destinatarios",
        "pode_comunicacoes_enviar_teste",
        "protegido",
        "tema",
    ),
    "ouvidoria_manifestacoes": (
        "numero_interno",
        "situacao",
        "classificacao_acesso",
        "representacao_id",
    ),
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
    "tarefas": ("owner_user_id", "status", "origem_modulo", "origem_id"),
    "registros_seguidos": ("usuario_id", "origem_modulo", "origem_id"),
    "avisos_usuario": (
        "usuario_id",
        "tipo",
        "origem_modulo",
        "origem_id",
        "lembrar_em",
        "status",
    ),
    "ia_telemetria": ("criado_em", "modulo", "operacao", "sucesso", "duracao_ms"),
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
    "peticoes_documentos": ("arquivo",),
    "relatorios_institucionais_pdf": ("conteudo",),
}

# Engine-specific dump of previous backups; embedding it would recurse and
# duplicate BYTEA dumps. Paths/SHA-256 remain in audit_log when present.
BACKUP_EXCLUDED_TABLES = frozenset({"backup_snapshots", "sqlite_sequence"})

DOCUMENT_FOLDERS = {
    "portarias": "documentos/portarias",
    "memorandos_arquivos": "documentos/memorandos",
    "oficio_arquivos": "documentos/oficios",
    "oficio_quarentena": "documentos/oficios_quarentena",
    "representacao_documentos": "documentos/representacoes",
    "ouvidoria_documentos": "documentos/ouvidoria",
    "peticoes_documentos": "documentos/peticoes",
    "relatorios_institucionais_pdf": "documentos/relatorios_institucionais",
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
        if table in wanted and table in existing and table not in BACKUP_EXCLUDED_TABLES
    )


# These modules initialize lazily. Absence of an entire group is reported;
# partial presence is never accepted. No DDL is run by the backup service.
LAZY_TABLE_GROUPS = (AGENDA_TABLES, OFICIOS_TABLES, PETICOES_TABLES, AUDIT_TABLES)
EXCLUSION_REASONS = {
    "backup_snapshots": "Snapshots nativos da exclusão administrativa: conservar no backup nativo PostgreSQL.",
    "sqlite_sequence": "High-water marks exportados separadamente no manifesto de schema.",
}
# Migration staging table: its presence in a live schema still blocks backup.
TRANSIENT_SCHEMA_TABLES = frozenset({"tramita_importacoes_nova"})


def check_backup_coverage(backend, existing, markers=()):
    existing = set(existing)
    expected = set(ESSENTIAL_TABLES)
    if backend == "postgresql":
        expected.update(POSTGRES_ONLY_TABLES)
    unknown = existing - set(APPLICATION_TABLES) - BACKUP_EXCLUDED_TABLES
    absent_modules = []
    marker_prefixes = {
        AGENDA_TABLES: ("agenda_member_",),
        OFICIOS_TABLES: ("oficios_schema_v",),
        AUDIT_TABLES: ("auditoria_schema_v",),
        PETICOES_TABLES: ("peticoes_schema_v",),
    }
    for group in LAZY_TABLE_GROUPS:
        initialized = any(
            marker.startswith(marker_prefixes[group]) for marker in markers
        )
        if not existing.intersection(group) and not initialized:
            expected.difference_update(group)
            absent_modules.extend(group)
    missing = expected - existing
    if unknown or missing:
        raise ValueError(
            "Cobertura de backup incompleta. Não inventariadas: "
            + ", ".join(sorted(unknown))
            + "; ausentes: "
            + ", ".join(sorted(missing))
        )
    return sorted(absent_modules)
