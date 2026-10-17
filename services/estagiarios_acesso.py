"""Acesso de estagiário: vínculo vigente e Minutas IA, sem outros módulos."""

from datetime import date, datetime
from zoneinfo import ZoneInfo

from database.estagiarios import GABINETES as GABINETES_ESTAGIO
from database.estagiarios import LOTACOES

CATEGORIA_INSTITUCIONAL = "INSTITUCIONAL"
CATEGORIA_ESTAGIARIO = "ESTAGIARIO"
CATEGORIAS = (CATEGORIA_INSTITUCIONAL, CATEGORIA_ESTAGIARIO)
TIPO_INSTITUCIONAL = "INSTITUCIONAL"
TIPO_ESTAGIARIO = "ESTAGIARIO"
TIPOS_SOLICITANTE = (TIPO_INSTITUCIONAL, TIPO_ESTAGIARIO)
_FUSO = ZoneInfo("America/Sao_Paulo")


def hoje_institucional():
    return datetime.now(_FUSO).date()


def categoria_de(record):
    valor = str((record or {}).get("categoria_acesso") or CATEGORIA_INSTITUCIONAL).upper()
    if valor not in CATEGORIAS:
        return CATEGORIA_INSTITUCIONAL
    return valor


def eh_estagiario(principal_ou_registro):
    if principal_ou_registro is None:
        return False
    if isinstance(principal_ou_registro, dict):
        return categoria_de(principal_ou_registro) == CATEGORIA_ESTAGIARIO
    return getattr(principal_ou_registro, "categoria_acesso", CATEGORIA_INSTITUCIONAL) == CATEGORIA_ESTAGIARIO


def email_externo_confirmado(identity):
    """Estagiário só entra quando o provedor confirma o e-mail da Conta Google."""
    return (identity or {}).get("email_verified") is True


def _data(valor):
    if not valor:
        return None
    if isinstance(valor, date):
        return valor
    return date.fromisoformat(str(valor)[:10])


def vinculo_vigente(row, hoje=None):
    """A data-limite programada é inclusiva. Encerramento administrativo bloqueia na hora."""
    if not row or not row.get("ativo"):
        return False
    if row.get("data_encerramento"):
        return False
    hoje = hoje or hoje_institucional()
    inicio = _data(row.get("data_inicio"))
    limite = _data(row.get("data_limite"))
    if inicio is None or limite is None or hoje < inicio or hoje > limite:
        return False
    return True


def carregar_vinculo(store, lotacao_id):
    if not lotacao_id:
        return None
    from database.store import unwrap_store

    fonte = unwrap_store(store)
    with fonte.connection(read_only=True) as conexao:
        conexao.execute("SAVEPOINT vinculo_estagio")
        try:
            linha = conexao.execute(
                "SELECT id,pessoa_id,lotacao,data_inicio,data_limite,data_encerramento,ativo,atualizado_em "
                "FROM estagiarios_lotacoes WHERE id=?",
                (lotacao_id,),
            ).fetchone()
            conexao.execute("RELEASE SAVEPOINT vinculo_estagio")
        except Exception:
            conexao.execute("ROLLBACK TO SAVEPOINT vinculo_estagio")
            return None
    return dict(linha) if linha else None


def gabinetes_autorizados(store, usuario_id, vinculo):
    """O gabinete vivo do estágio prevalece. Extras são autorização explícita de Minutas."""
    if not vinculo_vigente(vinculo):
        return ()
    siglas = []
    lotacao = vinculo.get("lotacao")
    if lotacao in LOTACOES:
        siglas.append(lotacao)
    from database.store import unwrap_store

    fonte = unwrap_store(store)
    with fonte.connection(read_only=True) as conexao:
        conexao.execute("SAVEPOINT gabinetes_minutas")
        try:
            linhas = conexao.execute(
                "SELECT gabinete FROM usuario_minutas_gabinetes WHERE usuario_id=? ORDER BY gabinete",
                (usuario_id,),
            ).fetchall()
            conexao.execute("RELEASE SAVEPOINT gabinetes_minutas")
        except Exception:
            conexao.execute("ROLLBACK TO SAVEPOINT gabinetes_minutas")
            linhas = []
    for linha in linhas:
        codigo = linha[0]
        if codigo in LOTACOES and codigo not in siglas:
            siglas.append(codigo)
    return tuple(siglas)


def exigir_gabinete_minutas(principal, sigla):
    if not eh_estagiario(principal):
        return
    from services.minutas_library import MinutasAccessDenied

    if not getattr(principal, "estagio_vigente", False):
        raise MinutasAccessDenied("Acesso não autorizado a este módulo.")
    if sigla not in set(getattr(principal, "minutas_gabinetes", ()) or ()):
        raise MinutasAccessDenied("Acesso não autorizado a este gabinete.")


def sanitizar_cadastro_estagiario(payload, vinculo):
    """Ignora flags de módulos enviadas pelo formulário."""
    if not vinculo or not vinculo.get("ativo"):
        raise ValueError("Selecione um vínculo de estágio ativo antes de liberar o acesso.")
    if vinculo.get("data_encerramento"):
        raise ValueError("O vínculo de estágio está encerrado.")
    limite = _data(vinculo.get("data_limite"))
    if limite is None or hoje_institucional() > limite:
        raise ValueError("O vínculo de estágio está vencido.")
    if vinculo.get("lotacao") not in LOTACOES:
        raise ValueError("Lotação de estágio inválida.")
    extras = []
    for codigo in payload.get("minutas_gabinetes") or ():
        if codigo not in LOTACOES:
            raise ValueError("Gabinete de Minutas inválido.")
        if codigo not in extras:
            extras.append(codigo)
    if vinculo["lotacao"] not in extras:
        extras.insert(0, vinculo["lotacao"])
    return {
        "perfil": "USUARIO",
        "pode_portarias": False,
        "pode_agenda": False,
        "pode_oficios": False,
        "pode_memorandos": False,
        "pode_relatorios": False,
        "pode_representacoes": False,
        "pode_peticoes": False,
        "pode_ouvidoria": False,
        "pode_representacoes_registrar_protocolo": False,
        "pode_representacoes_enviar_comunicacao": False,
        "pode_comunicacoes_configurar_destinatarios": False,
        "pode_comunicacoes_enviar_teste": False,
        "pode_peticoes_cadastrar": False,
        "pode_peticoes_editar": False,
        "pode_peticoes_registrar_andamento": False,
        "pode_peticoes_registrar_resultado": False,
        "pode_peticoes_concluir": False,
        "pode_admin": False,
        "gabinetes": [],
        "categoria_acesso": CATEGORIA_ESTAGIARIO,
        "estagio_lotacao_id": int(vinculo["id"]),
        "minutas_gabinetes": extras,
    }


def opcoes_gabinete_estagio():
    return tuple(f"{GABINETES_ESTAGIO[codigo]} — {codigo}" for codigo in LOTACOES)


def sigla_da_opcao(rotulo):
    texto = (rotulo or "").strip()
    if texto in LOTACOES:
        return texto
    if " — " in texto:
        codigo = texto.rsplit(" — ", 1)[-1].strip()
        if codigo in LOTACOES:
            return codigo
    return None
