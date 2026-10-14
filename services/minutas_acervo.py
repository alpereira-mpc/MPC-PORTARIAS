"""Administração e migração explícita do acervo; sem chamadas ao Gemini."""

from collections import Counter, defaultdict
from hashlib import sha256
import uuid
import re
import logging

from database.store import now
from database.minutas_especialidades import MinutasSpecialtiesStore
from services.minutas_specialties import PROSECUTOR_KEY, CLASSES, scope, suggest, fingerprint
from services.minutas_library import MinutasValidationError, _validate_file, _normalize_process, _validate_year


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
        self.library = library
        self.repository = MinutasSpecialtiesStore(library.repository.store)

    def catalog(self):
        self.library._authorize()
        return self.repository.catalog()

    def dry_run(self):
        self.library._authorize()
        return migration_plan(self.repository.snapshot())

    def apply(self, reviewed_plan, backup):
        actor = self.library._authorize()
        current = self.dry_run()
        if current != reviewed_plan: raise MinutasValidationError("Inventário alterado; simule e confira novamente.")
        before = current["contagens"]
        changed = self.repository.apply_plan(current, actor, backup)
        after = inventory(self.repository.snapshot())
        if before != after: raise MinutasValidationError("Divergência de reconciliação; confira o relatório operacional.")
        return {"alterados": changed, "antes": before, "depois": after}

    def classify(self, identifier, specialty, formal_class, entity, reason, themes=()):
        actor = self.library._authorize()
        profile = scope(specialty)
        if formal_class not in CLASSES: raise MinutasValidationError("Informe a classe processual efetiva.")
        if profile["grupo"] != "TEMATICO" and formal_class != profile["grupo"]:
            raise MinutasValidationError("Classe incompatível com a especialidade.")
        if not reason.strip(): raise MinutasValidationError("Registre o fundamento da classificação.")
        return self.repository.classify({"referencia_id": identifier, "especialidade_id": specialty,
            "estado": "CLASSIFICADO", "classe": formal_class, "entidade": entity.strip(),
            "perfil": profile["perfil"], "modalidade": profile["nome"], "temas": list(themes),
            "evidencia": [reason.strip()], "motivo": reason.strip()}, actor)

    def update_state(self, specialty, values):
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
        text = extracted["texto_integral"]
        name_keys, text_keys, years = identify_process_keys(name, text)
        warnings = []
        recovered = None
        if name_keys and text_keys and name_keys != text_keys:
            warnings.append("O número extraído do nome do arquivo diverge do texto do documento. Confira antes de cadastrar.")
        elif len(name_keys | text_keys) > 1:
            warnings.append("Há mais de um número de processo no documento; selecione o processo já cadastrado.")
        elif len(name_keys | text_keys) == 1 and specialty:
            recovered = self.find_active_process(specialty, next(iter(name_keys | text_keys)))
        processo = ""
        exercicio = int(next(iter(years))) if len(years) == 1 else None
        origem = ""
        classe = ""
        referencia_id = None
        suggestion = "Necessita conferência"
        if recovered:
            processo = recovered.get("numero_processo") or display_process_key(
                stable_process_key(recovered.get("processo_chave")))
            exercicio = int(recovered["exercicio"]) if recovered.get("exercicio") not in (None, "") else exercicio
            origem = recovered.get("origem") or ""
            classe = recovered.get("classe") or ""
            referencia_id = str(recovered["id"])
            if years and exercicio not in years:
                warnings.append(
                    "O exercício indicado no documento diverge do cadastro. "
                    "Confira o exercício das contas; o ano da numeração do processo não foi utilizado."
                )
        else:
            if len(name_keys) == 1 and (not text_keys or name_keys == text_keys):
                processo = display_process_key(next(iter(name_keys)))
            elif len(text_keys) == 1 and not name_keys:
                processo = display_process_key(next(iter(text_keys)))
            classified = suggest({"id": "preview", "procuradora_chave": PROSECUTOR_KEY},
                [{"referencia_id": "preview", "procuradora_chave": PROSECUTOR_KEY, "texto_integral": text}])
            origem = classified.get("entidade") or ""
            suggestion = classified.get("especialidade_id") or classified.get("motivo") or ""
        if recovered:
            suggestion = "Processo já cadastrado — dados recuperados do acervo"
        return {"nome": name, "sha256": sha256(content).hexdigest(),
                "processo": processo, "exercicio": exercicio, "origem": origem, "classe": classe,
                "referencia_id": referencia_id, "recuperado": bool(recovered),
                "advertencias": warnings, "sugestao": suggestion, "status": extracted["status"]}

    def _upload_existing(self, c, specialty, key, year, digest, name, confirm_version):
        """Checks only metadata; reused before upload and inside the short transaction."""
        from database.minutas_especialidades import active_clause, separation_enabled
        enabled = separation_enabled(c, getattr(self.library.repository.store, "backend", ""))
        active = active_clause(enabled, "")
        matches = c.execute("SELECT * FROM minutas_referencias WHERE procuradora_chave=? "
            "AND LOWER(processo_chave) IN (?,?,?,?)" + active,
            (PROSECUTOR_KEY, key, "tc" + key, "processotc" + key, "processo" + key)).fetchall()
        if len(matches) > 1:
            raise MinutasValidationError("Há registros equivalentes para este processo; confira os vínculos antes de cadastrar.")
        record = dict(matches[0]) if matches else None
        scoped = active_clause(enabled)
        known = c.execute("SELECT e.referencia_id FROM minutas_extracoes e "
            "JOIN minutas_referencias r ON r.id=e.referencia_id WHERE r.procuradora_chave=? AND e.arquivo_sha256=?"
            + scoped + " "
            "UNION SELECT d.referencia_id FROM minutas_acervo_documentos d JOIN minutas_referencias r "
            "ON r.id=d.referencia_id WHERE r.procuradora_chave=? AND d.sha256=?" + scoped,
            (PROSECUTOR_KEY, digest, PROSECUTOR_KEY, digest)).fetchall()
        if any(str(d["referencia_id"]) != str((record or {}).get("id")) for d in known):
            raise MinutasValidationError("Conteúdo já vinculado a outro processo; confira os vínculos antes de cadastrar uma cópia.")
        if not record:
            return None, False, []
        if int(record["exercicio"]) != year:
            raise MinutasValidationError("Exercício divergente do processo cadastrado; confira os metadados antes do upload.")
        classification = c.execute("SELECT * FROM minutas_classificacoes WHERE referencia_id=?", (record["id"],)).fetchone()
        if not classification or classification["estado"] != "CLASSIFICADO" or classification["especialidade_id"] != specialty:
            raise MinutasValidationError("Processo existente em outro escopo ou pendente; confira sua classificação antes do upload.")
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
        if not values.get("fundamento", "").strip(): raise MinutasValidationError("Confirme o fundamento da classificação.")
        _process, key = _normalize_process(values.get("numero_processo"))
        # Novos registros usam chave estável independente da grafia TC.
        key = re.sub(r"^(?:processo)?(?:tc)?", "", key)
        if not re.fullmatch(r"\d{7}", key):
            raise MinutasValidationError("Informe o processo com cinco dígitos e ano de dois dígitos.")
        year = _validate_year(values.get("exercicio"))
        file = _validate_file("auditoria" if role == "auditoria" else "parecer", upload)
        digest = sha256(file["content"]).hexdigest()
        from services.minutas_storage import acervo_object_path
        path = acervo_object_path(file["name"])
        parameters = (specialty, key, year, digest, file["name"], confirm_version)
        with self.library.repository.store.connection(read_only=True) as c:
            existing, duplicate, unhashed = self._upload_existing(c, *parameters)
        if duplicate:
            return {"estado": "DUPLICADO", "referencia_id": str(existing["id"])}
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
                    c.execute("SELECT pg_advisory_xact_lock(hashtext(?))", (PROSECUTOR_KEY + key,))
                record, duplicate, current_unhashed = self._upload_existing(c, *parameters)
                if duplicate:
                    return {"estado": "DUPLICADO", "referencia_id": str(record["id"])}
                if set(current_unhashed) - set(legacy_hashes) or (record or {}).get("_same_name"):
                    raise MinutasValidationError("Acervo alterado durante o upload; confira os documentos e tente novamente.")
                if not record:
                    identifier = str(uuid.uuid4())
                    stamp = now()
                    from services.minutas_library import PROCURADORA
                    subtype = {"RPPS": "Instituto/Autarquia", "CAMARA": "Câmara Municipal",
                        "PREFEITURA": "Prefeitura", "SECRETARIA_MUNICIPAL": "Secretaria Municipal ou Estadual",
                        "ORGAO_ESTADUAL": "Secretaria Municipal ou Estadual"}.get(profile["perfil"])
                    from database.minutas_especialidades import separation_enabled
                    if separation_enabled(c, getattr(self.library.repository.store, "backend", "")):
                        c.execute("INSERT INTO minutas_referencias (id,procuradora,procuradora_chave,numero_processo,processo_chave,"
                            "exercicio,subtipo,acervo,criado_em,criado_por,atualizado_em,atualizado_por) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                            (identifier, PROCURADORA, PROSECUTOR_KEY, "TC " + key[:5] + "/" + key[5:], key, year, subtype, "ativo", stamp, actor, stamp, actor))
                    else:
                        c.execute("INSERT INTO minutas_referencias (id,procuradora,procuradora_chave,numero_processo,processo_chave,"
                            "exercicio,subtipo,criado_em,criado_por,atualizado_em,atualizado_por) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                            (identifier, PROCURADORA, PROSECUTOR_KEY, "TC " + key[:5] + "/" + key[5:], key, year, subtype, stamp, actor, stamp, actor))
                    record = {"id": identifier}
                    self.repository._classify(c, {"referencia_id": identifier, "estado": "CLASSIFICADO", "especialidade_id": specialty,
                        "classe": formal, "modalidade": profile["nome"], "perfil": profile["perfil"],
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
