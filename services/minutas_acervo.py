"""Administração e migração explícita do acervo; sem chamadas ao Gemini."""

from collections import Counter, defaultdict
from hashlib import sha256
import uuid
import re
import logging

from database.store import now
from database.minutas_especialidades import MinutasSpecialtiesStore
from services.minutas_specialties import (
    PROSECUTOR_KEY, CLASSES, plain, scope, suggest, fingerprint, requires_account_year,
)
from services.minutas_library import (
    MinutasValidationError, _validate_file, _normalize_process, coerce_account_year,
)


_PROCESS_IN_NAME = re.compile(r"(?i)(?:proc(?:esso)?|tc)[_ \-.]*(\d{5})[_/\-](\d{2})(?!\d)")
_PROCESS_IN_TEXT = re.compile(
    r"(?i)(?:processo|proc\.?)\s*(?:tc)?\s*(?:n[º°o.]*)?\s*[:\-]?\s*(\d{5})\s*/\s*(\d{2})"
    r"|\bTC\s*(?:n[º°o.]*)?\s*[:\-]?\s*(\d{5})\s*/\s*(\d{2})"
)
_ACCOUNT_YEAR = re.compile(
    r"(?i)exerc[ií]cio(?:\s+(?:das\s+contas|financeiro))?\s*[:\-]?\s*(20\d{2})"
)


def stable_process_key(value):
    key = re.sub(r"[^0-9a-z]", "", str(value or "").casefold())
    key = re.sub(r"^(?:processo)?(?:tc)?", "", key)
    return key if re.fullmatch(r"\d{7}", key) else ""


def identify_process_keys(name="", text=""):
    """Nome do arquivo e cabeçalho; o ano da numeração não é exercício das contas."""
    name_keys = {a + b for a, b in _PROCESS_IN_NAME.findall(str(name or ""))}
    text_keys = set()
    for match in _PROCESS_IN_TEXT.finditer(str(text or "")):
        first, second, third, fourth = match.groups()
        if first and second:
            text_keys.add(first + second)
        elif third and fourth:
            text_keys.add(third + fourth)
    years = {int(year) for year in _ACCOUNT_YEAR.findall(str(text or ""))}
    return name_keys, text_keys, years


def display_process_key(key):
    return key[:5] + "/" + key[5:] if re.fullmatch(r"\d{7}", key or "") else ""


_HEADER_LINE = (
    ("processo", re.compile(r"(?i)^processo(?:\s+tc|\s+n[º°o.]*)?\s*:\s*(.*)$")),
    ("processo", re.compile(r"(?i)^processo(?:\s+(?:tc|n[º°o.]*))+\s*:?\s*(\d[\d.]{2,14}\s*/\s*\d{2,4})\s*$")),
    ("subcategoria", re.compile(r"(?i)^subcategoria\s*:\s*(.*)$")),
    ("jurisdicionado", re.compile(r"(?i)^(?:jurisdicionado|origem|entidade(?:\s+fiscalizada)?)\s*:\s*(.*)$")),
    ("responsavel", re.compile(r"(?i)^respons[aá]vel(?:es)?\s*:\s*(.*)$")),
    ("relator", re.compile(r"(?i)^relator(?:es)?\s*:\s*(.*)$")),
    ("auditor", re.compile(r"(?i)^auditor(?:es)?(?:\s+de\s+contas)?\s*:\s*(.*)$")),
    ("assunto", re.compile(r"(?i)^assunto\s*:\s*(.*)$")),
    ("exercicio", re.compile(r"(?i)^exerc[ií]cio(?:\s+das\s+contas|\s+financeiro)?\s*:\s*(.*)$")),
)
_PROCESS_VALUE = re.compile(r"(\d{1,3}(?:\.\d{3})+|\d{4,6})\s*/\s*(\d{2}|\d{4})")


def _header_label(line):
    for name, pattern in _HEADER_LINE:
        match = pattern.match(line)
        if match:
            return name, (match.group(1) or "").strip()
    return "", ""


def normalize_process_token(raw):
    """02.434/2025 e 02281/26 viram a chave de cinco dígitos e ano de dois."""
    text = " ".join(str(raw or "").strip().split())
    if not text or len(text) > 24:
        return ""
    match = _PROCESS_VALUE.fullmatch(text)
    if not match:
        return ""
    number = match.group(1).replace(".", "")
    year = match.group(2)
    if len(year) == 4:
        if not year.startswith("20"):
            return ""
        year = year[2:]
    if not number.isdigit() or not 1 <= len(number) <= 5:
        return ""
    return number.zfill(5) + year


def _exercise_value(raw):
    text = str(raw or "").strip().rstrip(".")
    return int(text) if re.fullmatch(r"20\d{2}", text) else None


def _stored_year(record):
    value = (record or {}).get("exercicio")
    return int(value) if value not in (None, "") else None


def optional_exercise_label(record, prefix="Exercício"):
    year = _stored_year(record)
    return f"{prefix} {year}" if year is not None else ""


def _same_text(left, right):
    return re.sub(r"\s+", " ", plain(left)).strip() == re.sub(r"\s+", " ", plain(right)).strip()


def _class_from_subcategory(raw):
    if re.search(r"(?i)\bpca\b|presta[cç][aã]o de contas", str(raw or "")):
        return "PCA"
    return ""


def parse_report_header(text):
    """Tabela de identificação das primeiras linhas. Jurisdicionado é a origem."""
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in str(text or "").splitlines()]
    lines = [line for line in lines if line][:40]
    found = {}
    index = 0
    while index < len(lines):
        label, value = _header_label(lines[index])
        if not label:
            index += 1
            continue
        if not value and index + 1 < len(lines) and not _header_label(lines[index + 1])[0]:
            parts = [lines[index + 1]]
            index += 1
            while label == "jurisdicionado" and index + 1 < len(lines) and len(parts) < 3:
                if _header_label(lines[index + 1])[0] or len(lines[index + 1]) > 120:
                    break
                parts.append(lines[index + 1])
                index += 1
            value = " ".join(parts).strip()
        if label not in found and value:
            found[label] = value
        if {"processo", "jurisdicionado", "exercicio"} <= set(found):
            break
        index += 1
    key = normalize_process_token(found.get("processo", ""))
    return {
        "processo": display_process_key(key),
        "processo_chave": key,
        "processo_original": found.get("processo", ""),
        "exercicio": _exercise_value(found.get("exercicio", "")),
        "origem": found.get("jurisdicionado", "").strip(),
        "classe": _class_from_subcategory(found.get("subcategoria", "")),
        "subcategoria": found.get("subcategoria", ""),
    }


def _header_text(extracted):
    units = extracted.get("unidades") or []
    pages = [unit for unit in units if unit.get("pagina") in (1, 2)]
    chosen = pages or units[:6]
    return "\n".join(str(unit.get("texto") or "") for unit in chosen)


def inventory(snapshot):
    records, documents, extractions = (snapshot[k] for k in ("processos", "documentos", "extracoes"))
    paths = {d["path"]: d for d in documents}
    ids = {str(r["id"]) for r in records}
    from services.minutas_extraction import EXTRACTOR_VERSION
    indexed = {e["arquivo_path"] for e in extractions if e["status"] == "PROCESSADO"
               and e.get("extrator_versao") == EXTRACTOR_VERSION}
    hashes = defaultdict(list)
    for e in extractions:
        digest = e.get("arquivo_sha256")
        if digest and digest != "0" * 64: hashes[digest].append(e["arquivo_path"])
    return {
        "processos": len(records), "arquivos_vinculados": len(documents),
        "tipos": dict(Counter(d["papel"] for d in documents)),
        "extracoes": len(extractions), "nao_indexados": sorted(set(paths) - indexed),
        "extracoes_sem_vinculo": sorted(e["arquivo_path"] for e in extractions
            if e["arquivo_path"] not in paths or str(e["referencia_id"]) not in ids
            or str(paths[e["arquivo_path"]]["referencia_id"]) != str(e["referencia_id"])),
        "duplicados": [v for v in hashes.values() if len(v) > 1],
        "metadados_incompletos": [str(r["id"]) for r in records if not r.get("origem") or not r.get("responsavel")],
        "identificadores": sorted(ids), "caminhos": sorted(paths),
        "omitidos_pelo_filtro_legado_de_geracao": sorted(str(r["id"]) for r in records
            if re.sub(r"\D", "", str(r.get("numero_processo") or ""))
            not in {"0149926", "0165326", "0265025", "0272025"}),
        "objetos_storage": "Não verificados; exige inventário separado do bucket privado.",
    }


def storage_inventory(snapshot, objects):
    """Reconcilia metadados do bucket sem baixar nem modificar documentos."""
    linked = {d["path"] for d in snapshot["documentos"]}
    present = {str(obj["name"]) for obj in objects}
    return {"objetos": len(present), "caminhos_vinculados": len(linked),
            "vinculos_sem_objeto": sorted(linked - present),
            "objetos_sem_vinculo_no_escopo": sorted(present - linked),
            "observacao": "Objetos fora do escopo Elvira podem pertencer a outros acervos; não excluir automaticamente.",
            "objetos_hash": fingerprint(objects)}


def migration_plan(snapshot):
    from services.minutas_extraction import EXTRACTOR_VERSION
    docs = {d["path"]: d for d in snapshot["documentos"]}
    grouped = defaultdict(list)
    for row in snapshot["extracoes"]:
        doc = docs.get(row["arquivo_path"])
        if (doc and str(doc["referencia_id"]) == str(row["referencia_id"])
                and doc["papel"] == row["papel"] and doc["nome"] == row["arquivo_nome"]
                and int(doc["tamanho"]) == int(row["arquivo_tamanho"])
                and row.get("status") == "PROCESSADO" and row.get("extrator_versao") == EXTRACTOR_VERSION):
            grouped[str(row["referencia_id"])].append(row)
    existing = {str(c["referencia_id"]): c for c in snapshot.get("classificacoes", [])}
    items = []
    for record in snapshot["processos"]:
        previous = existing.get(str(record["id"]))
        if previous and previous["estado"] == "CLASSIFICADO":
            continue  # Nunca sobrescreve configuração correta ou decisão humana.
        items.append(suggest(record, grouped[str(record["id"])]))
    return {"lote": fingerprint(snapshot), "registros_hash": fingerprint(snapshot["processos"]),
            "documentos_hash": fingerprint(snapshot["documentos"]), "extracoes_hash": fingerprint(snapshot["extracoes"]),
            "contagens": inventory(snapshot), "itens": items,
            "resultados": dict(Counter(item["estado"] for item in items))}


class MinutasAcervoService:
    def __init__(self, library):
        from services.minutas_procuradores import MinutasContextoError

        self.library = library
        procurador = getattr(library, "procurador", None)
        if procurador is None or not getattr(procurador, "chave", None):
            raise MinutasContextoError("Selecione o Procurador antes de continuar.")
        self.repository = MinutasSpecialtiesStore(
            library.repository.store,
            procuradora_chave=procurador.chave,
        )

    def catalog(self):
        self.library._authorize()
        return self.repository.catalog()

    def dry_run(self):
        self.library._authorize()
        return migration_plan(self.repository.snapshot())

    def apply(self, reviewed_plan, backup):
        from services.minutas_procuradores import exigir_admin_estrutural
        exigir_admin_estrutural(self.library)
        actor = self.library._authorize()
        current = self.dry_run()
        if current != reviewed_plan: raise MinutasValidationError("Inventário alterado; simule e confira novamente.")
        before = current["contagens"]
        changed = self.repository.apply_plan(current, actor, backup)
        after = inventory(self.repository.snapshot())
        if before != after: raise MinutasValidationError("Divergência de reconciliação; confira o relatório operacional.")
        return {"alterados": changed, "antes": before, "depois": after}

    def classify(
        self, identifier, specialty, formal_class, entity, reason, themes=(),
        *, modality="", profile_value="",
    ):
        actor = self.library._authorize()
        profile = scope(specialty)
        if formal_class not in CLASSES: raise MinutasValidationError("Informe a classe processual efetiva.")
        if profile["grupo"] != "TEMATICO" and formal_class != profile["grupo"]:
            raise MinutasValidationError("Classe incompatível com a especialidade.")
        if not reason.strip(): raise MinutasValidationError("Registre o fundamento da classificação.")
        from services.minutas_specialties import RECURSOS
        if specialty == RECURSOS:
            from services.minutas_recursos import MODALIDADES
            if modality not in set(MODALIDADES.values()) or not str(profile_value or "").strip() or not tuple(themes):
                raise MinutasValidationError("Confirme modalidade, matéria originária e teses do recurso.")
        return self.repository.classify({"referencia_id": identifier, "especialidade_id": specialty,
            "estado": "CLASSIFICADO", "classe": formal_class, "entidade": entity.strip(),
            "perfil": str(profile_value or "").strip() if specialty == RECURSOS else profile["perfil"],
            "modalidade": modality if specialty == RECURSOS else profile["nome"], "temas": list(themes),
            "evidencia": [reason.strip()], "motivo": reason.strip()}, actor)

    def update_state(self, specialty, values):
        from services.minutas_procuradores import exigir_admin_estrutural
        exigir_admin_estrutural(self.library)
        return self.repository.update_state(specialty, values, self.library._authorize())

    def restore(self, event_id):
        return self.repository.restore_classification(event_id, self.library._authorize())

    def link(self, identifier, specialty, reason):
        return self.repository.link(identifier, specialty, reason, self.library._authorize())

    def find_active_process(self, specialty, key):
        """Único processo ATIVO da Procuradora na especialidade; ignora o legado."""
        self.library._authorize()
        scope(specialty)
        key = stable_process_key(key)
        if not key:
            return None
        query = display_process_key(key)
        matches = []
        seen = set()
        for row in self.repository.list_records(specialty, query=query, limit=100):
            if stable_process_key(row.get("processo_chave") or row.get("numero_processo")) != key:
                continue
            identifier = str(row["id"])
            if identifier in seen:
                continue
            seen.add(identifier)
            matches.append(row)
        return matches[0] if len(matches) == 1 else None

    def preview_upload(self, name, content, role, specialty=None):
        """Extração local explícita; não conserva bytes em caches globais."""
        self.library._authorize()
        from services.minutas_extraction import extract_content
        if role not in ("auditoria", "complementar", "parecer", "modelo"):
            raise MinutasValidationError("Finalidade documental inválida.")
        file = _validate_file("auditoria" if role == "auditoria" else "parecer", (name, content))
        extracted = extract_content(content, file["mime"], "complementar" if role == "modelo" else role)
        header = parse_report_header(_header_text(extracted))
        name_keys = identify_process_keys(name, "")[0]
        filename_key = next(iter(name_keys)) if len(name_keys) == 1 else ""
        warnings = []
        if len(name_keys) > 1:
            filename_key = ""
            warnings.append("O nome do arquivo contém mais de um número de processo. Use o cabeçalho ou selecione o processo.")
        processo = header["processo"]
        fonte = "cabeçalho" if processo else ""
        if not processo and filename_key:
            processo = display_process_key(filename_key)
            fonte = "nome do arquivo"
            check = (
                "Confira o processo e a origem."
                if specialty and not requires_account_year(specialty)
                else "Confira o exercício e a origem."
            )
            warnings.append(
                "O processo foi lido do nome do arquivo porque o cabeçalho não trouxe um número seguro. "
                + check
            )
        name_conflict = bool(header["processo"] and filename_key and header["processo_chave"] != filename_key)
        if name_conflict:
            warnings.append(
                f"Cabeçalho: processo {header['processo']}. Nome do arquivo: processo {display_process_key(filename_key)}. "
                "Confirme qual processo recebe o relatório."
            )
        recovered = None
        if specialty and not name_conflict and (header["processo_chave"] or (fonte == "nome do arquivo" and filename_key)):
            recovered = self.find_active_process(specialty, header["processo_chave"] or filename_key)
        conflito = name_conflict
        recovered_year = _stored_year(recovered)
        if (
            specialty
            and requires_account_year(specialty)
            and recovered
            and header["exercicio"] is not None
            and recovered_year != header["exercicio"]
        ):
            warnings.append(
                f"Exercício do cabeçalho: {header['exercicio']}. Exercício cadastrado: {recovered.get('exercicio')}. "
                "O cadastro existente não será alterado; confirme a vinculação."
            )
            conflito = True
        if recovered and header["origem"] and not _same_text(header["origem"], recovered.get("origem") or ""):
            warnings.append(
                f"Jurisdicionado do relatório: {header['origem']}. Origem cadastrada: {recovered.get('origem') or '—'}. "
                "O cadastro existente não será alterado; confirme a vinculação."
            )
            conflito = True
        if recovered and header["classe"] and recovered.get("classe") and header["classe"] != recovered.get("classe"):
            warnings.append(
                f"Classe do cabeçalho: {header['classe']}. Classe cadastrada: {recovered.get('classe')}. "
                "O cadastro existente não será alterado; confirme a vinculação."
            )
            conflito = True
        if recovered and not conflito:
            processo = recovered.get("numero_processo") or display_process_key(
                stable_process_key(recovered.get("processo_chave")))
            exercicio = recovered_year
            origem = recovered.get("origem") or header["origem"]
            classe = recovered.get("classe") or header["classe"]
            referencia_id = str(recovered["id"])
            suggestion = "Processo identificado no acervo — relatório será adicionado ao processo existente."
        else:
            omit_year = bool(specialty) and not requires_account_year(specialty)
            exercicio = None if omit_year else header["exercicio"]
            origem = header["origem"]
            classe = header["classe"]
            referencia_id = None
            fill = "Preencha processo e origem." if omit_year else "Preencha processo, exercício e origem."
            if header["processo"]:
                suggestion = "Dados extraídos do cabeçalho do relatório."
            elif fonte == "nome do arquivo":
                suggestion = "Processo identificado pelo nome do arquivo."
            elif role == "parecer":
                from services.minutas_procuradores import legado_espo
                pessoa = getattr(self.library, "procurador", None) or legado_espo()
                classified = suggest({"id": "preview", "procuradora_chave": pessoa.chave},
                    [{"referencia_id": "preview", "procuradora_chave": pessoa.chave,
                      "texto_integral": extracted.get("texto_integral") or ""}])
                origem = origem or classified.get("entidade") or ""
                suggestion = classified.get("especialidade_id") or fill
            else:
                suggestion = fill
        return {"nome": name, "sha256": sha256(content).hexdigest(),
                "processo": processo, "exercicio": exercicio, "origem": origem, "classe": classe,
                "fonte": fonte, "referencia_id": referencia_id, "recuperado": bool(recovered and not conflito),
                "conflito": conflito, "advertencias": warnings, "sugestao": suggestion,
                "status": extracted["status"]}

    def _upload_existing(self, c, specialty, key, year, digest, name, confirm_version):
        """Checks only metadata; reused before upload and inside the short transaction."""
        from database.minutas_especialidades import active_clause, separation_enabled
        from services.minutas_procuradores import legado_espo
        enabled = separation_enabled(c, getattr(self.library.repository.store, "backend", ""))
        active = active_clause(enabled, "")
        chave = (getattr(self.library, "procurador", None) or legado_espo()).chave
        matches = c.execute("SELECT * FROM minutas_referencias WHERE procuradora_chave=? "
            "AND LOWER(processo_chave) IN (?,?,?,?)" + active,
            (chave, key, "tc" + key, "processotc" + key, "processo" + key)).fetchall()
        if len(matches) > 1:
            raise MinutasValidationError("Há registros equivalentes para este processo; confira os vínculos antes de cadastrar.")
        record = dict(matches[0]) if matches else None
        scoped = active_clause(enabled)
        known = c.execute("SELECT e.referencia_id FROM minutas_extracoes e "
            "JOIN minutas_referencias r ON r.id=e.referencia_id WHERE r.procuradora_chave=? AND e.arquivo_sha256=?"
            + scoped + " "
            "UNION SELECT d.referencia_id FROM minutas_acervo_documentos d JOIN minutas_referencias r "
            "ON r.id=d.referencia_id WHERE r.procuradora_chave=? AND d.sha256=?" + scoped,
            (chave, digest, chave, digest)).fetchall()
        if any(str(d["referencia_id"]) != str((record or {}).get("id")) for d in known):
            raise MinutasValidationError("Conteúdo já vinculado a outro processo; confira os vínculos antes de cadastrar uma cópia.")
        if not record:
            return None, False, []
        classification = c.execute("SELECT * FROM minutas_classificacoes WHERE referencia_id=?", (record["id"],)).fetchone()
        if not classification or classification["estado"] != "CLASSIFICADO" or classification["especialidade_id"] != specialty:
            raise MinutasValidationError("Processo existente em outro escopo ou pendente; confira sua classificação antes do upload.")
        if requires_account_year(specialty) and _stored_year(record) != year:
            raise MinutasValidationError("Exercício divergente do processo cadastrado; confira os metadados antes do upload.")
        if known:
            return record, True, []
        if not confirm_version and c.execute("SELECT id FROM minutas_documentos_catalogo WHERE referencia_id=? AND nome=?",
                                             (record["id"], name)).fetchone():
            # A versão será recusada após a conferência de hashes legados: um
            # arquivo idêntico é duplicata, mesmo que ainda não tenha extração.
            same_name = True
        else:
            same_name = False
        unhashed = c.execute("SELECT d.path FROM minutas_documentos_catalogo d "
            "LEFT JOIN minutas_extracoes e ON e.arquivo_path=d.path AND e.referencia_id=d.referencia_id "
            "LEFT JOIN minutas_acervo_documentos n ON n.path=d.path WHERE d.referencia_id=? "
            "AND n.id IS NULL AND (e.arquivo_sha256 IS NULL OR e.arquivo_sha256=?)",
            (record["id"], "0" * 64)).fetchall()
        record["_same_name"] = same_name
        return record, False, [d["path"] for d in unhashed]

    def add_file(self, specialty, values, upload, *, role, confirm_version=False):
        """Uma transação por arquivo; duplicatas idempotentes; versões nunca sobrescrevem."""
        actor = self.library._authorize()
        profile = scope(specialty)
        formal = values.get("classe")
        if formal not in CLASSES or (profile["grupo"] != "TEMATICO" and formal != profile["grupo"]):
            raise MinutasValidationError("Confirme a classe processual efetiva.")
        if role not in ("auditoria", "complementar", "parecer", "modelo"):
            raise MinutasValidationError("Finalidade inválida.")
        from services.minutas_specialties import RECURSOS
        if specialty == RECURSOS:
            from services.minutas_recursos import MODALIDADES
            if values.get("modalidade_recurso") not in set(MODALIDADES.values()):
                raise MinutasValidationError("Informe Apelação ou Recurso Ordinário para o parecer.")
            if not str(values.get("materia_originaria") or "").strip():
                raise MinutasValidationError("Informe a matéria originária do recurso.")
            if not [item for item in values.get("teses") or () if str(item).strip()]:
                raise MinutasValidationError("Informe ao menos uma tese jurídica do parecer.")
        if not values.get("fundamento", "").strip(): raise MinutasValidationError("Confirme o fundamento da classificação.")
        _process, key = _normalize_process(values.get("numero_processo"))
        # Novos registros usam chave estável independente da grafia TC.
        key = re.sub(r"^(?:processo)?(?:tc)?", "", key)
        if not re.fullmatch(r"\d{7}", key):
            raise MinutasValidationError("Informe o processo com cinco dígitos e ano de dois dígitos.")
        year = coerce_account_year(
            values.get("exercicio"), required=requires_account_year(specialty),
        )
        file = _validate_file("auditoria" if role == "auditoria" else "parecer", upload)
        digest = sha256(file["content"]).hexdigest()
        from services.minutas_storage import acervo_object_path
        path = acervo_object_path(file["name"])
        parameters = (specialty, key, year, digest, file["name"], confirm_version)
        with self.library.repository.store.connection(read_only=True) as c:
            existing, duplicate, unhashed = self._upload_existing(c, *parameters)
        if duplicate:
            return {"estado": "DUPLICADO", "referencia_id": str(existing["id"])}
        requested = str(values.get("referencia_id") or "").strip()
        if requested and (not existing or str(existing["id"]) != requested):
            raise MinutasValidationError("O processo confirmado não corresponde ao registro selecionado.")
        # Toda I/O de Storage ocorre fora da transação de escrita compartilhada.
        legacy_hashes = {p: sha256(self.library.storage.download(p)).hexdigest() for p in unhashed}
        if digest in legacy_hashes.values():
            return {"estado": "DUPLICADO", "referencia_id": str(existing["id"])}
        if (existing or {}).get("_same_name"):
            raise MinutasValidationError("Nome já existente: confirme o cadastro como nova versão.")
        uploaded = False
        persisted = False
        try:
            uploaded = True
            self.library.storage.upload(path, file["content"], file["mime"])
            with self.library.repository.store.connection() as c:
                c.execute("BEGIN IMMEDIATE")
                if getattr(self.library.repository.store, "backend", "") == "postgresql":
                    pessoa_lock = self.library.procurador
                    c.execute("SELECT pg_advisory_xact_lock(hashtext(?))", (pessoa_lock.chave + key,))
                record, duplicate, current_unhashed = self._upload_existing(c, *parameters)
                if duplicate:
                    return {"estado": "DUPLICADO", "referencia_id": str(record["id"])}
                if set(current_unhashed) - set(legacy_hashes) or (record or {}).get("_same_name"):
                    raise MinutasValidationError("Acervo alterado durante o upload; confira os documentos e tente novamente.")
                if requested and (not record or str(record["id"]) != requested):
                    raise MinutasValidationError("O processo confirmado não corresponde ao registro selecionado.")
                if not record:
                    identifier = str(uuid.uuid4())
                    stamp = now()
                    from database.minutas_referencias import MinutasReferenciasStore
                    pessoa = self.library.procurador
                    subtype = {"RPPS": "Instituto/Autarquia", "CAMARA": "Câmara Municipal",
                        "PREFEITURA": "Prefeitura", "SECRETARIA_MUNICIPAL": "Secretaria Municipal ou Estadual",
                        "ORGAO_ESTADUAL": "Secretaria Municipal ou Estadual"}.get(profile["perfil"])
                    from database.minutas_especialidades import separation_enabled
                    if separation_enabled(c, getattr(self.library.repository.store, "backend", "")):
                        c.execute("INSERT INTO minutas_referencias (id,procuradora,procuradora_chave,numero_processo,processo_chave,"
                            "exercicio,subtipo,acervo,criado_em,criado_por,atualizado_em,atualizado_por) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                            (identifier, pessoa.nome_exibicao, pessoa.chave, "TC " + key[:5] + "/" + key[5:], key, year, subtype, "ativo", stamp, actor, stamp, actor))
                    else:
                        c.execute("INSERT INTO minutas_referencias (id,procuradora,procuradora_chave,numero_processo,processo_chave,"
                            "exercicio,subtipo,criado_em,criado_por,atualizado_em,atualizado_por) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                            (identifier, pessoa.nome_exibicao, pessoa.chave, "TC " + key[:5] + "/" + key[5:], key, year, subtype, stamp, actor, stamp, actor))
                    MinutasReferenciasStore._associar_sigla(c, {"id": identifier, "procuradora_chave": pessoa.chave})
                    record = {"id": identifier}
                    recurso = specialty == RECURSOS
                    self.repository._classify(c, {"referencia_id": identifier, "estado": "CLASSIFICADO", "especialidade_id": specialty,
                        "classe": formal,
                        "modalidade": values.get("modalidade_recurso", "") if recurso else profile["nome"],
                        "perfil": values.get("materia_originaria", "") if recurso else profile["perfil"],
                        "temas": values.get("teses", []) if recurso else [],
                        "entidade": values.get("origem", ""), "motivo": values["fundamento"]}, actor)
                identifier = str(record["id"])
                position = c.execute("SELECT COALESCE(MAX(posicao),0)+1 AS next FROM ("
                    "SELECT posicao FROM minutas_documentos_complementares WHERE referencia_id=? UNION ALL "
                    "SELECT posicao FROM minutas_acervo_documentos WHERE referencia_id=?) positions",
                    (identifier, identifier)).fetchone()["next"]
                stamp = now()
                c.execute("INSERT INTO minutas_acervo_documentos (id,referencia_id,papel,tipo,descricao,posicao,nome,mime,tamanho,path,"
                    "sha256,criado_em,criado_por,atualizado_em,atualizado_por) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (str(uuid.uuid4()), identifier, role, {"parecer": "Parecer definitivo", "auditoria": "Relatório da Auditoria",
                    "complementar": "Documento instrutório", "modelo": "Modelo visual"}[role], values.get("descricao", ""), position,
                    file["name"], file["mime"], file["size"], path, digest, stamp, actor, stamp, actor))
                c.execute("UPDATE minutas_referencias SET atualizado_em=?,atualizado_por=? WHERE id=?", (stamp, actor, identifier))
                c.execute("UPDATE minutas_especialidades SET revisao=revisao+1 WHERE id=?", (specialty,))
                self.repository._event(c, identifier, specialty, "ADICIONAR_DOCUMENTO", None,
                    {"path": path, "sha256": digest, "papel": role}, actor)
            persisted = True
            return {"estado": "CADASTRADO", "referencia_id": identifier, "path": path}
        finally:
            if uploaded and not persisted:
                try:
                    self.library.storage.delete(path)
                except Exception:
                    logging.getLogger(__name__).warning("MINUTAS: compensação de upload falhou; conferir inventário do bucket.")
