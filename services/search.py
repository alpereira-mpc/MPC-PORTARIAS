"""Global metadata search. Permissions are applied in SQL; no document files."""

from dataclasses import dataclass, field
from datetime import date
import json
import logging
import re
import unicodedata

from database.store import unwrap_store
from services.access import has_permission
from services.audit import registrar_erro
from services.pending import (
    _cabinet_map,
    _has_table,
    _scope_cabinets,
    _sql_in,
    visible_cabinets,
)

LOGGER = logging.getLogger("mpc.search")
MIN_CHARS = 3
PER_MODULE = 8
TOTAL_CAP = 50
LIKE_ESCAPE = "\\"
NUMBER_YEAR = re.compile(r"^(\d{1,6})\s*/\s*(\d{4})$")
YEAR_ONLY = re.compile(r"^(20\d{2})$")
NUMBER_ONLY = re.compile(r"^\d{1,6}$")
DATE_BR = re.compile(r"^(\d{2})/(\d{2})/(\d{4})$")
MODULE_LABELS = {
    "portarias": "Portarias",
    "oficios": "Ofícios",
    "agenda": "Agenda e Afastamentos",
    "memorandos": "Memorandos",
    "tarefas": "Tarefas",
    "representacoes": "Representações",
    "ouvidoria": "Ouvidoria",
    "admin": "Administração",
}


@dataclass(frozen=True)
class SearchHit:
    source_module: str
    source_id: str
    gabinete: str
    title: str
    subtitle: str
    description: str
    date: date | None
    status: str
    score: int
    navigation: str
    metadata: dict = field(default_factory=dict)

    @property
    def module_label(self):
        return MODULE_LABELS.get(self.source_module, self.navigation)


def normalize_term(value):
    return " ".join(str(value or "").strip().split())


def normalize_search_text(value):
    """Normalize user text for portable, accent-insensitive SQL matching."""
    decomposed = unicodedata.normalize("NFKD", normalize_term(value))
    return "".join(
        character for character in decomposed if not unicodedata.combining(character)
    ).casefold()


def parse_number_year(term):
    match = NUMBER_YEAR.match(term)
    if match:
        return int(match.group(1)), int(match.group(2))
    return None, None


def like_value(term):
    escaped = (
        term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    )
    return "%" + escaped + "%"


def _like_sql(column):
    return f"LOWER({column}) LIKE LOWER(?) ESCAPE '{LIKE_ESCAPE}'"


def _normalized_sql(column):
    """Return a SQLite/PostgreSQL-compatible accent-folded text expression."""
    expression = f"COALESCE({column},'')"
    for accented, plain in (
        ("á", "a"),
        ("à", "a"),
        ("â", "a"),
        ("ã", "a"),
        ("ä", "a"),
        ("é", "e"),
        ("è", "e"),
        ("ê", "e"),
        ("ë", "e"),
        ("í", "i"),
        ("ì", "i"),
        ("î", "i"),
        ("ï", "i"),
        ("ó", "o"),
        ("ò", "o"),
        ("ô", "o"),
        ("õ", "o"),
        ("ö", "o"),
        ("ú", "u"),
        ("ù", "u"),
        ("û", "u"),
        ("ü", "u"),
        ("ç", "c"),
        ("ñ", "n"),
    ):
        expression = (
            f"REPLACE(REPLACE({expression},'{accented}','{plain}'),"
            f"'{accented.upper()}','{plain}')"
        )
    return f"LOWER({expression})"


def _normalized_like_sql(column):
    return f"{_normalized_sql(column)} LIKE ? ESCAPE '{LIKE_ESCAPE}'"


def _date_br_sql(column):
    """Format ISO date text as DD/MM/YYYY without changing persisted values."""
    return (
        f"CASE WHEN COALESCE({column},'')='' THEN '' ELSE "
        f"SUBSTR({column},9,2)||'/'||SUBSTR({column},6,2)||'/'||"
        f"SUBSTR({column},1,4) END"
    )


def _parse_date(value):
    if not value:
        return None
    text = str(value)[:10]
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def _score(term, *, code="", title="", extra="", number=None, year=None):
    needle = term.casefold()
    score = 10
    if code and code.casefold() == needle:
        score = 100
    elif number is not None and str(number) == needle:
        score = 95
    elif year is not None and str(year) == needle:
        score = 72
    title_cf = (title or "").casefold()
    if title_cf == needle:
        score = max(score, 90)
    elif title_cf.startswith(needle):
        score = max(score, 80)
    elif needle in title_cf:
        score = max(score, 60)
    elif needle in (extra or "").casefold() or needle in (code or "").casefold():
        score = max(score, 40)
    return score


def _payload_obj(raw):
    if isinstance(raw, dict):
        return raw
    try:
        return json.loads(raw or "{}")
    except (TypeError, ValueError):
        return {}


def _authorized_oficios(principal, gabinete, gabinete_filter):
    if principal.administrator:
        allowed = True
    else:
        allowed = gabinete in visible_cabinets(principal)
    if not allowed:
        return False
    if gabinete_filter and gabinete != gabinete_filter:
        return False
    return True


def search_portarias(connection, store, principal, term, *, limit):
    if not has_permission(principal, "portarias"):
        return []
    if not _has_table(store, "portarias", connection):
        return []
    like = like_value(term)
    number, year = parse_number_year(term)
    text = (
        "("
        + _like_sql("p.payload")
        + " OR "
        + _like_sql("CAST(p.numero AS TEXT)")
        + " OR "
        + _like_sql("CAST(p.ano AS TEXT)")
        + " OR EXISTS (SELECT 1 FROM substituicoes s WHERE s.portaria_id=p.id AND "
        + _like_sql("s.payload")
        + "))"
    )
    params = [like, like, like, like]
    extra = ""
    if number is not None:
        extra = " OR (p.numero=? AND p.ano=?)"
        params.extend([number, year])
    elif YEAR_ONLY.match(term):
        extra = " OR p.ano=?"
        params.append(int(term))
    elif NUMBER_ONLY.match(term):
        extra = " OR p.numero=?"
        params.append(int(term))
    rows = connection.execute(
        "SELECT p.id,p.numero,p.ano,p.status,p.payload,p.criada FROM portarias p "
        "WHERE " + text + extra + " ORDER BY p.criada DESC, p.id DESC LIMIT ?",
        (*params, limit),
    ).fetchall()
    items = []
    for row in rows:
        payload = _payload_obj(row["payload"])
        people = []
        sign = payload.get("signatario") or {}
        if isinstance(sign, dict) and sign.get("nome"):
            people.append(sign["nome"])
        for item in payload.get("substituicoes") or []:
            for key in ("titular", "substituto"):
                person = item.get(key) or {}
                if isinstance(person, dict) and person.get("nome"):
                    people.append(person["nome"])
        motivo = ""
        subs = payload.get("substituicoes") or []
        if subs:
            motivo = (subs[0].get("motivo_texto") or subs[0].get("motivo") or "")[:80]
        number = row["numero"]
        year = row["ano"]
        title = (
            f"Portaria nº {number}/{year}"
            if number
            else "Rascunho de Portaria"
        )
        extra = " ".join(people) + " " + motivo
        items.append(
            SearchHit(
                source_module="portarias",
                source_id=str(row["id"]),
                gabinete="—",
                title=title,
                subtitle=motivo,
                description=", ".join(dict.fromkeys(people))[:120],
                date=_parse_date(row["criada"]),
                status=row["status"] or "",
                score=_score(
                    term,
                    code=f"{number}/{year}" if number else "",
                    title=title,
                    extra=extra,
                    number=number,
                    year=year,
                ),
                navigation="Portarias",
                metadata={},
            )
        )
    return items


def search_oficios(
    connection, store, principal, term, *, limit, cabinet_map=None
):
    if not has_permission(principal, "oficios"):
        return []
    if not _has_table(store, "oficios", connection):
        return []
    allowed = _scope_cabinets(principal, None)
    if not allowed:
        return []
    like = like_value(normalize_search_text(term))
    number, year = parse_number_year(term)
    placeholders = _sql_in(allowed)
    scope = (
        "(o.serie IN (" + placeholders + ") "
        "OR o.membro_id IN (SELECT membro_id FROM oficio_series WHERE sigla IN ("
        + placeholders
        + ")) "
        "OR EXISTS (SELECT 1 FROM oficio_destinatarios d "
        "JOIN oficio_series s ON s.membro_id=d.membro_id "
        "WHERE d.oficio_id=o.id AND s.sigla IN ("
        + placeholders
        + ")))"
    )
    searchable_columns = (
        "o.assunto",
        "o.destinatario",
        "o.numero_externo",
        "o.serie",
        "o.status",
        "o.direcao",
        "CAST(o.numero AS TEXT)",
        "CAST(o.ano AS TEXT)",
        "o.payload",
        "o.responde_a",
        "CAST(o.aguarda_resposta AS TEXT)",
    )
    searchable_dates = (
        "o.data",
        "o.prazo",
        "o.data_envio",
        "o.cancelada",
        "o.data_esperada_resposta",
    )
    searchable_text = " || ' ' || ".join(
        [f"COALESCE({column},'')" for column in searchable_columns]
        + [_date_br_sql(column) for column in searchable_dates]
    )
    text_parts = [_normalized_like_sql(searchable_text)]
    text_parts.extend(
        (
            "EXISTS (SELECT 1 FROM procuradores p WHERE p.id=o.membro_id AND "
            + _normalized_like_sql("p.nome")
            + ")",
            "EXISTS (SELECT 1 FROM oficio_destinatarios d JOIN procuradores p "
            "ON p.id=d.membro_id WHERE d.oficio_id=o.id AND "
            + _normalized_like_sql("p.nome")
            + ")",
            "EXISTS (SELECT 1 FROM oficio_movimentacoes m WHERE m.oficio_id=o.id AND ("
            + _normalized_like_sql("m.anterior")
            + " OR "
            + _normalized_like_sql("m.novo")
            + " OR "
            + _normalized_like_sql("m.observacao")
            + "))",
            "EXISTS (SELECT 1 FROM oficio_arquivos a WHERE a.oficio_id=o.id AND "
            + _normalized_like_sql("a.nome")
            + ")",
        )
    )
    text_params = [like] * 7
    if match := DATE_BR.match(term):
        day, month, year_text = match.groups()
        text_parts.append(_normalized_like_sql("o.payload"))
        text_params.append(like_value(f"{year_text}-{month}-{day}"))
    text = "(" + " OR ".join(text_parts) + ")"
    params = [
        *allowed,
        *allowed,
        *allowed,
        *text_params,
    ]
    filter_sql = text
    if number is not None:
        filter_sql = "(" + text + " OR (o.numero=? AND o.ano=?))"
        params.extend([number, year])
    elif YEAR_ONLY.match(term):
        filter_sql = "(" + text + " OR o.ano=?)"
        params.append(int(term))
    elif NUMBER_ONLY.match(term):
        filter_sql = "(" + text + " OR o.numero=?)"
        params.append(int(term))
    rows = connection.execute(
        "SELECT o.id,o.direcao,o.serie,o.ano,o.numero,o.status,o.data,"
        "o.assunto,o.destinatario,o.numero_externo,o.membro_id "
        "FROM oficios o WHERE "
        + scope
        + " AND "
        + filter_sql
        + " ORDER BY o.atualizada DESC, o.id DESC LIMIT ?",
        (*params, limit),
    ).fetchall()
    mapping = cabinet_map if cabinet_map is not None else _cabinet_map(connection)
    items = []
    for row in rows:
        gabinete = (
            row["serie"]
            or mapping.get(int(row["membro_id"] or 0), "")
            or "—"
        )
        if not _authorized_oficios(principal, gabinete if gabinete != "—" else "", None):
            if gabinete != "—" and gabinete not in allowed:
                continue
        number = row["numero"]
        if row["direcao"] == "ENVIADO" and number:
            title = f"Ofício nº {number}/{row['ano']}"
        elif row["numero_externo"]:
            title = f"Ofício {row['numero_externo']}"
        else:
            title = "Ofício recebido"
        extra_text = " ".join(
            part
            for part in (row["assunto"], row["destinatario"], row["numero_externo"], row["status"])
            if part
        )
        items.append(
            SearchHit(
                source_module="oficios",
                source_id=str(row["id"]),
                gabinete=gabinete or "—",
                title=title,
                subtitle=(row["assunto"] or "")[:80],
                description=(row["destinatario"] or "")[:80],
                date=_parse_date(row["data"]),
                status=row["status"] or "",
                score=_score(
                    term,
                    code=f"{number}/{row['ano']}" if number else (row["numero_externo"] or ""),
                    title=title + " " + (row["assunto"] or ""),
                    extra=extra_text,
                    number=number,
                    year=row["ano"],
                ),
                navigation="Ofícios",
                metadata={"direcao": row["direcao"]},
            )
        )
    return items


def search_agenda(
    connection, store, principal, term, *, limit, cabinet_map=None
):
    if not has_permission(principal, "agenda"):
        return []
    items = []
    like = like_value(term)
    mapping = cabinet_map if cabinet_map is not None else _cabinet_map(connection)
    restrict = bool(visible_cabinets(principal)) or principal.administrator
    allowed = _scope_cabinets(principal, None) if restrict else []
    if restrict and not allowed:
        return []
    if _has_table(store, "agenda_compromissos", connection):
        rows = connection.execute(
            "SELECT a.id,a.tipo,a.inicio,a.situacao,a.payload FROM agenda_compromissos a "
            "WHERE "
            + _like_sql("a.payload")
            + " OR "
            + _like_sql("a.tipo")
            + " OR "
            + _like_sql("a.situacao")
            + " ORDER BY a.inicio DESC, a.id DESC LIMIT ?",
            (like, like, like, limit),
        ).fetchall()
        people_map = {}
        if rows:
            ids = [row["id"] for row in rows]
            for item in connection.execute(
                "SELECT compromisso_id,procurador_id FROM agenda_compromisso_procuradores "
                "WHERE compromisso_id IN (" + _sql_in(ids) + ")",
                ids,
            ):
                people_map.setdefault(item["compromisso_id"], []).append(int(item["procurador_id"]))
        for row in rows:
            members = people_map.get(row["id"], [])
            cabinets = [mapping.get(mid, "") for mid in members]
            cabinets = [code for code in cabinets if code]
            if restrict and cabinets and not any(code in allowed for code in cabinets):
                continue
            if restrict and not cabinets and not principal.administrator:
                continue
            payload = _payload_obj(row["payload"])
            title = (payload.get("titulo") or row["tipo"] or "Compromisso")[:80]
            extra = " ".join(
                part
                for part in (
                    payload.get("local"),
                    payload.get("categoria"),
                    payload.get("reuniao_com"),
                    payload.get("processo"),
                    row["tipo"],
                )
                if part
            )
            items.append(
                SearchHit(
                    source_module="agenda",
                    source_id=str(row["id"]),
                    gabinete=cabinets[0] if cabinets else "—",
                    title=title,
                    subtitle=(payload.get("local") or "")[:80],
                    description=extra[:120],
                    date=_parse_date(row["inicio"]),
                    status=row["situacao"] or "",
                    score=_score(term, title=title, extra=extra),
                    navigation="Agenda",
                    metadata={"compromisso_id": str(row["id"])},
                )
            )
    if _has_table(store, "agenda_afastamentos", connection):
        rows = connection.execute(
            "SELECT id,procurador_id,motivo,data_inicio,data_fim,cancelado FROM agenda_afastamentos "
            "WHERE "
            + _like_sql("COALESCE(motivo,'')")
            + " OR "
            + _like_sql("COALESCE(motivo_outro,'')")
            + " OR "
            + _like_sql("COALESCE(observacao,'')")
            + " ORDER BY data_inicio DESC, id DESC LIMIT ?",
            (like, like, like, limit),
        ).fetchall()
        for row in rows:
            gabinete = mapping.get(int(row["procurador_id"] or 0), "")
            if restrict and gabinete and gabinete not in allowed:
                continue
            status = "Cancelado" if row["cancelado"] else "Afastamento"
            title = "Afastamento"
            items.append(
                SearchHit(
                    source_module="agenda",
                    source_id=str(row["id"]),
                    gabinete=gabinete or "—",
                    title=title,
                    subtitle=(row["motivo"] or "")[:80],
                    description="",
                    date=_parse_date(row["data_inicio"]),
                    status=status,
                    score=_score(term, title=title, extra=row["motivo"] or ""),
                    navigation="Agenda",
                    metadata={"afastamento_id": str(row["id"])},
                )
            )
    items.sort(key=lambda item: (-item.score, item.date or date.min, item.title))
    return items[:limit]


def search_memorandos(
    connection, store, principal, term, *, limit, cabinet_map=None
):
    if not has_permission(principal, "memorandos"):
        return []
    if not _has_table(store, "memorandos", connection):
        return []
    restrict = bool(visible_cabinets(principal)) or principal.administrator
    allowed = _scope_cabinets(principal, None) if restrict else []
    if restrict and not allowed:
        return []
    like = like_value(term)
    mapping = cabinet_map if cabinet_map is not None else _cabinet_map(connection)
    rows = connection.execute(
        "SELECT m.id,m.status,m.numero_oficial,m.atualizado_em,s.data_inicio,s.motivo,"
        "s.motivo_texto,s.gabinete_procurador_id,s.gabinete_snapshot "
        "FROM memorandos m LEFT JOIN memorandos_substituicao s ON s.memorando_id=m.id "
        "WHERE "
        + _like_sql("COALESCE(m.numero_oficial,'')")
        + " OR "
        + _like_sql("COALESCE(s.motivo,'')")
        + " OR "
        + _like_sql("COALESCE(s.motivo_texto,'')")
        + " OR "
        + _like_sql("COALESCE(s.gabinete_snapshot,'')")
        + " ORDER BY m.atualizado_em DESC, m.id DESC LIMIT ?",
        (like, like, like, like, limit),
    ).fetchall()
    items = []
    for row in rows:
        gabinete = ""
        if row["gabinete_procurador_id"] is not None:
            gabinete = mapping.get(int(row["gabinete_procurador_id"]), "")
        gabinete = gabinete or (row["gabinete_snapshot"] or "")
        if restrict and gabinete and gabinete not in allowed:
            continue
        number = (row["numero_oficial"] or "").strip()
        title = f"Memorando {number}" if number else "Memorando de substituição"
        extra = " ".join(part for part in (row["motivo"], row["motivo_texto"], number) if part)
        items.append(
            SearchHit(
                source_module="memorandos",
                source_id=str(row["id"]),
                gabinete=gabinete or "—",
                title=title,
                subtitle=(row["motivo"] or "")[:80],
                description=(row["motivo_texto"] or "")[:120],
                date=_parse_date(row["data_inicio"] or row["atualizado_em"]),
                status=row["status"] or "",
                score=_score(term, code=number, title=title, extra=extra),
                navigation="Memorandos",
                metadata={},
            )
        )
    return items


def search_tarefas(connection, store, principal, term, *, limit):
    if not has_permission(principal, "tarefas"):
        return []
    if not _has_table(store, "tarefas", connection):
        return []
    owner = getattr(principal, "id", None)
    if owner is None:
        return []
    like = like_value(term)
    rows = connection.execute(
        "SELECT id,titulo,descricao,status,prazo_data,categoria FROM tarefas "
        "WHERE owner_user_id=? AND ("
        + _like_sql("titulo")
        + " OR "
        + _like_sql("COALESCE(descricao,'')")
        + " OR "
        + _like_sql("COALESCE(categoria,'')")
        + ") ORDER BY atualizado_em DESC, id DESC LIMIT ?",
        (owner, like, like, like, limit),
    ).fetchall()
    labels = {
        "A_FAZER": "A fazer",
        "EM_ANDAMENTO": "Em andamento",
        "AGUARDANDO": "Aguardando",
        "CONCLUIDA": "Concluída",
        "CANCELADA": "Cancelada",
    }
    items = []
    for row in rows:
        items.append(
            SearchHit(
                source_module="tarefas",
                source_id=str(row["id"]),
                gabinete="—",
                title=(row["titulo"] or "Tarefa")[:80],
                subtitle=(row["categoria"] or "")[:80],
                description="",
                date=_parse_date(row["prazo_data"]),
                status=labels.get(row["status"], row["status"] or ""),
                score=_score(term, title=row["titulo"] or "", extra=row["categoria"] or ""),
                navigation="Tarefas",
                metadata={},
            )
        )
    return items


def search_representacoes(connection, store, principal, term, *, limit):
    if not has_permission(principal, "representacoes"):
        return []
    if not _has_table(store, "representacoes", connection):
        return []
    like = like_value(term)
    rows = connection.execute(
        "SELECT id,titulo,objeto,representado,situacao,numero_processo,data_abertura,tema "
        "FROM representacoes WHERE "
        + _like_sql("titulo")
        + " OR "
        + _like_sql("COALESCE(objeto,'')")
        + " OR "
        + _like_sql("COALESCE(representado,'')")
        + " OR "
        + _like_sql("COALESCE(numero_processo,'')")
        + " OR "
        + _like_sql("COALESCE(tema,'')")
        + " ORDER BY data_abertura DESC, id DESC LIMIT ?",
        (like, like, like, like, like, limit),
    ).fetchall()
    from services.representacoes import SITUACOES

    items = []
    for row in rows:
        title = (row["titulo"] or "Representação")[:80]
        items.append(
            SearchHit(
                source_module="representacoes",
                source_id=str(row["id"]),
                gabinete="—",
                title=title,
                subtitle=(row["representado"] or row["tema"] or "")[:80],
                description=(row["numero_processo"] or "")[:80],
                date=_parse_date(row["data_abertura"]),
                status=SITUACOES.get(row["situacao"], row["situacao"] or ""),
                score=_score(
                    term,
                    code=row["numero_processo"] or "",
                    title=title,
                    extra=" ".join(
                        part
                        for part in (row["representado"], row["tema"], row["numero_processo"])
                        if part
                    ),
                ),
                navigation="Representações",
                metadata={},
            )
        )
    return items


def search_ouvidoria(connection, store, principal, term, *, limit):
    if not has_permission(principal, "ouvidoria"):
        return []
    if not _has_table(store, "ouvidoria_manifestacoes", connection):
        return []
    like = like_value(term)
    rows = connection.execute(
        "SELECT id,numero_interno,titulo,representado,situacao,data_recebimento,tema "
        "FROM ouvidoria_manifestacoes WHERE "
        + _like_sql("titulo")
        + " OR "
        + _like_sql("COALESCE(numero_interno,'')")
        + " OR "
        + _like_sql("COALESCE(representado,'')")
        + " OR "
        + _like_sql("COALESCE(tema,'')")
        + " ORDER BY data_recebimento DESC, id DESC LIMIT ?",
        (like, like, like, like, limit),
    ).fetchall()
    from services.ouvidoria import SITUACOES

    items = []
    for row in rows:
        title = (row["numero_interno"] or row["titulo"] or "Notícia de fato")[:80]
        items.append(
            SearchHit(
                source_module="ouvidoria",
                source_id=str(row["id"]),
                gabinete="—",
                title=title,
                subtitle=(row["titulo"] or "")[:80],
                description=(row["representado"] or "")[:80],
                date=_parse_date(row["data_recebimento"]),
                status=SITUACOES.get(row["situacao"], row["situacao"] or ""),
                score=_score(
                    term,
                    code=row["numero_interno"] or "",
                    title=row["titulo"] or "",
                    extra=row["representado"] or "",
                ),
                navigation="Ouvidoria",
                metadata={},
            )
        )
    return items


def search_admin(connection, store, principal, term, *, limit):
    if not has_permission(principal, "admin"):
        return []
    items = []
    like = like_value(term)
    if _has_table(store, "usuarios_acesso", connection):
        rows = connection.execute(
            "SELECT id,nome,email,perfil,ativo FROM usuarios_acesso WHERE "
            + _like_sql("nome")
            + " OR "
            + _like_sql("email")
            + " ORDER BY nome LIMIT ?",
            (like, like, limit),
        ).fetchall()
        for row in rows:
            items.append(
                SearchHit(
                    source_module="admin",
                    source_id="user-" + str(row["id"]),
                    gabinete="—",
                    title=row["nome"] or row["email"],
                    subtitle=row["email"] or "",
                    description=row["perfil"] or "",
                    date=None,
                    status="Ativo" if row["ativo"] else "Inativo",
                    score=_score(term, title=row["nome"] or "", extra=row["email"] or ""),
                    navigation="Administração",
                    metadata={"secao": "Acessos e Auditoria"},
                )
            )
    if _has_table(store, "access_requests", connection):
        rows = connection.execute(
            "SELECT id,nome,email,gabinete,status,created_at FROM access_requests WHERE "
            + _like_sql("nome")
            + " OR "
            + _like_sql("email")
            + " OR "
            + _like_sql("gabinete")
            + " ORDER BY created_at DESC, id DESC LIMIT ?",
            (like, like, like, limit),
        ).fetchall()
        for row in rows:
            items.append(
                SearchHit(
                    source_module="admin",
                    source_id="req-" + str(row["id"]),
                    gabinete=row["gabinete"] or "—",
                    title="Solicitação de acesso",
                    subtitle=((row["nome"] or "") + " · " + (row["email"] or ""))[:80],
                    description="",
                    date=_parse_date(row["created_at"]),
                    status=row["status"] or "",
                    score=_score(term, title=row["nome"] or "", extra=row["email"] or ""),
                    navigation="Administração",
                    metadata={"secao": "Solicitações"},
                )
            )
    items.sort(key=lambda item: (-item.score, item.title))
    return items[:limit]


SOURCES = (
    "portarias",
    "oficios",
    "agenda",
    "memorandos",
    "tarefas",
    "representacoes",
    "ouvidoria",
    "admin",
)
LOADERS = {
    "portarias": search_portarias,
    "oficios": search_oficios,
    "agenda": search_agenda,
    "memorandos": search_memorandos,
    "tarefas": search_tarefas,
    "representacoes": search_representacoes,
    "ouvidoria": search_ouvidoria,
    "admin": search_admin,
}


def should_run_search(term):
    cleaned = normalize_term(term)
    if not cleaned:
        return False, "empty", cleaned
    if len(cleaned) >= MIN_CHARS:
        return True, "ok", cleaned
    if NUMBER_ONLY.match(cleaned) or NUMBER_YEAR.match(cleaned):
        return True, "ok", cleaned
    return False, "short", cleaned


def global_search(store, principal, term, *, limit_per_module=PER_MODULE):
    store = unwrap_store(store)
    run, status, cleaned = should_run_search(term)
    if not run:
        return [], {}, {"status": status, "term": cleaned, "total": 0}
    errors = {}
    collected = []
    limit = max(1, min(int(limit_per_module or PER_MODULE), PER_MODULE))
    with store.connection(read_only=True) as connection:
        cabinet_map = None
        for key in SOURCES:
            loader = LOADERS[key]
            if not has_permission(principal, key):
                continue
            try:
                kwargs = {}
                if key in ("oficios", "agenda", "memorandos"):
                    if cabinet_map is None:
                        cabinet_map = _cabinet_map(connection)
                    kwargs["cabinet_map"] = cabinet_map
                collected.extend(
                    loader(
                        connection,
                        store,
                        principal,
                        cleaned,
                        limit=limit,
                        **kwargs,
                    )
                )
                errors[key] = None
            except Exception as exc:
                LOGGER.exception("Falha na busca global de %s", key)
                try:
                    registrar_erro(
                        store,
                        modulo="busca",
                        acao="CONSULTAR",
                        erro=exc,
                        principal=principal,
                    )
                except Exception:
                    LOGGER.exception(
                        "Falha ao registrar erro da busca global de %s", key
                    )
                errors[key] = key
    collected.sort(key=lambda item: (-item.score, item.source_module, item.title))
    if len(collected) > TOTAL_CAP:
        collected = collected[:TOTAL_CAP]
    return collected, errors, {"status": "ok", "term": cleaned, "total": len(collected)}


def group_hits(items):
    groups = []
    seen = {}
    for item in items:
        if item.source_module not in seen:
            seen[item.source_module] = []
            groups.append((item.source_module, seen[item.source_module]))
        seen[item.source_module].append(item)
    return groups
