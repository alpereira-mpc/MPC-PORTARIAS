"""Catálogo e classificação conservadora do acervo Minutas IA (sem IA)."""

from hashlib import sha256
import json
import re
import unicodedata


PROSECUTOR_KEY = "elvira samara"
RPPS = "elvira.pca.rpps"
CAMARAS = "elvira.pca.camaras"
SECRETARIAS_MUNICIPAIS = "elvira.pca.secretarias_municipais"
PREFEITURAS = "elvira.pca.prefeituras"
PREFEITURAS_LEGACY = ("elvira.pca.governo", "elvira.pca.gestao")
SHARED_INSTRUCTION_MOTOR = "pca-rpps-v1"
APOSENTADORIAS = "elvira.pessoal.aposentadorias"
APOSENTADORIAS_MOTOR = "aposentadorias-v1"
INSTRUCTION_READY_STATES = ("Em testes", "Funcional com ajustes", "Validada")
INSTRUCTION_EXPERIMENTAL_SPECIALTIES = (
    CAMARAS,
    SECRETARIAS_MUNICIPAIS,
    PREFEITURAS,
)
STATES = ("A iniciar", "Em alimentação", "Em testes", "Funcional com ajustes", "Validada")
_PCA_SUBTYPE = {
    "RPPS": "Instituto/Autarquia",
    "CAMARA": "Câmara Municipal",
    "PREFEITURA": "Prefeitura",
    "SECRETARIA_MUNICIPAL": "Secretaria Municipal ou Estadual",
    "ORGAO_ESTADUAL": "Secretaria Municipal ou Estadual",
}
CLASSES = {
    "PCA": "Prestação de Contas Anual",
    "ATOS_PESSOAL": "Atos de pessoal sujeitos a registro",
    "INSPECAO": "Inspeção Especial de Contas",
    "TCE": "Tomada de Contas Especial",
    "DENUNCIA": "Denúncia",
    "REPRESENTACAO": "Representação",
    "CONSULTA": "Consulta",
    "RECURSO": "Recurso",
    "RESCISORIO": "Pedido Rescisório",
}
# Grupo de navegação não se confunde com classe formal. Perfis temáticos exigem
# a indicação da classe efetiva em cada vínculo, sem presumi-la pelo assunto.
_PROFILES = (
    ("pca.rpps", "PCA", "Institutos Previdenciários (RPPS)", "RPPS"),
    ("pca.camaras", "PCA", "Câmaras Municipais", "CAMARA"),
    ("pca.prefeituras", "PCA", "Prefeituras Municipais", "PREFEITURA"),
    ("pca.secretarias_municipais", "PCA", "Secretarias Municipais", "SECRETARIA_MUNICIPAL"),
    ("pca.orgaos_estaduais", "PCA", "Secretarias e Órgãos Estaduais", "ORGAO_ESTADUAL"),
    ("pca.fundos", "PCA", "Fundos Públicos", "FUNDO"),
    ("pca.autarquias", "PCA", "Demais Autarquias e Fundações", "AUTARQUIA_FUNDACAO"),
    ("pca.outras", "PCA", "Outras Entidades Jurisdicionadas", "OUTRA"),
    ("pessoal.aposentadorias", "ATOS_PESSOAL", "Aposentadorias", ""),
    ("pessoal.pensoes", "ATOS_PESSOAL", "Pensões", ""),
    ("pessoal.reformas", "ATOS_PESSOAL", "Reformas", ""),
    ("pessoal.admissoes", "ATOS_PESSOAL", "Admissões de Pessoal", ""),
    ("tema.concursos", "TEMATICO", "Concursos Públicos", ""),
    ("inspecao", "INSPECAO", "Inspeção Especial de Contas", ""),
    ("tema.licitacoes", "TEMATICO", "Licitações", ""),
    ("tema.contratos", "TEMATICO", "Contratos Administrativos", ""),
    ("tema.convenios", "TEMATICO", "Convênios e Instrumentos Congêneres", ""),
    ("tce", "TCE", "Tomada de Contas Especial", ""),
    ("denuncias", "DENUNCIA", "Denúncias", ""),
    ("representacoes", "REPRESENTACAO", "Representações", ""),
    ("consultas", "CONSULTA", "Consultas", ""),
    ("recursos", "RECURSO", "Recursos", ""),
    ("rescisorios", "RESCISORIO", "Pedidos Rescisórios", ""),
)
CATALOG = tuple({
    "id": "elvira." + key, "procuradora_chave": PROSECUTOR_KEY,
    "grupo": group, "nome": name, "perfil": profile,
    "estado": "Funcional com ajustes" if key == "pca.rpps" else "A iniciar",
    "config_versao": "1", "motor": "pca-rpps-v1" if key == "pca.rpps" else "",
    "config": {
        "orientacoes": [], "criterios": [], "defesa": [], "teses": [],
        "fundamentacao": [], "ementa": "", "dispositivo": "",
        "conclusoes_admissiveis": [], "recomendacoes": [], "validacoes": [],
        "template": "elvira" if key == "pca.rpps" else "",
        "recuperacao": {"somente_pareceres_definitivos": True, "limite": 6},
    },
} for key, group, name, profile in _PROFILES)


def canonical_specialty_id(value):
    """Mantém IDs históricos de Prefeituras legíveis durante a transição manual."""
    value = str(value or "")
    return PREFEITURAS if value in PREFEITURAS_LEGACY else value


def specialty_memberships(value):
    """IDs físicos aceitos para uma especialidade lógica, sem duplicar resultados."""
    canonical = canonical_specialty_id(value)
    return (PREFEITURAS, *PREFEITURAS_LEGACY) if canonical == PREFEITURAS else (canonical,)


def scope(value):
    profile = next((item for item in CATALOG if item["id"] == canonical_specialty_id(value)), None)
    if profile is None:
        raise ValueError("Selecione uma especialidade jurídica válida.")
    return profile


def instruction_pca_subtype(profile):
    return _PCA_SUBTYPE.get(str((profile or {}).get("perfil") or ""), "")


def requires_account_year(specialty_id):
    """Somente Aposentadorias omite o exercício financeiro das contas."""
    return canonical_specialty_id(specialty_id) != APOSENTADORIAS


def instruction_generation_allowed(profile):
    """Habilitação operacional da geração por PDFs, isolada por motor."""
    if not isinstance(profile, dict):
        return False
    motor = str(profile.get("motor") or "")
    version = str(profile.get("config_versao") or "")
    identifier = str(profile.get("id") or "")
    if identifier == APOSENTADORIAS or motor == APOSENTADORIAS_MOTOR:
        return (
            identifier == APOSENTADORIAS
            and motor == APOSENTADORIAS_MOTOR
            and version == "1"
            and str(profile.get("estado") or "") in INSTRUCTION_READY_STATES
        )
    if motor != SHARED_INSTRUCTION_MOTOR or version != "1":
        return False
    if identifier == RPPS:
        return True
    if identifier in INSTRUCTION_EXPERIMENTAL_SPECIALTIES:
        return str(profile.get("estado") or "") in INSTRUCTION_READY_STATES
    return False


def instruction_profile(library):
    """Perfil efetivo da sessão: banco quando existir, senão o catálogo."""
    repository = getattr(library, "repository", None)
    specialty = getattr(library, "specialty_id", None)
    if specialty is None:
        specialty = getattr(repository, "specialty_id", None)
    if repository is not None and callable(getattr(repository, "profile", None)):
        try:
            return repository.profile()
        except Exception:
            pass
    return scope(specialty or RPPS)


def plain(value):
    return "".join(c for c in unicodedata.normalize("NFD", str(value or "").lower())
                   if not unicodedata.combining(c))


def fingerprint(value):
    return sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()


def suggest(record, extractions):
    """Classifica somente identidade expressa; nunca usa número ou nome de arquivo."""
    result = {"referencia_id": str(record["id"]), "estado": "NAO_CLASSIFICADO",
              "especialidade_id": None, "classe": "", "modalidade": "",
              "perfil": "", "entidade": "", "temas": [], "evidencia": [],
              "motivo": "Dados insuficientes para identificar a especialidade."}
    if record.get("procuradora_chave") != PROSECUTOR_KEY:
        result["motivo"] = "Procurador fora do escopo desta implantação."
        return result
    # Extrações devem estar vinculadas ao registro e aos documentos atuais.
    text = "\n".join(str(row.get("texto_integral") or "") for row in extractions
                     if str(row.get("referencia_id")) == str(record["id"])
                     and row.get("procuradora_chave") == PROSECUTOR_KEY)
    fields = {}
    for match in re.finditer(r"(?im)^\s*(origem|entidade|órgão|orgao|unidade gestora|natureza|classe|modalidade)\s*:\s*([^\n]+)", text):
        fields.setdefault(plain(match[1]), set()).add(match[2].strip())
    for field in ("classe", "natureza", "modalidade"):
        if record.get(field):
            fields.setdefault(field, set()).add(CLASSES.get(record[field], record[field]))
    entities = set().union(*(fields.get(k, set()) for k in ("origem", "entidade", "orgao", "unidade gestora")))
    if record.get("origem"):
        entities.add(record["origem"])
    entities = {e for e in entities if not re.search(r"auditoria|tribunal de contas|tce.pb|relatorio", plain(e))}
    if len({plain(e) for e in entities}) > 1:
        result.update(estado="PENDENTE", motivo="Entidades divergentes nos metadados/documentos.")
        return result
    entity = sorted(entities, key=lambda value: (plain(value), value))[0] if entities else ""
    result["entidade"] = entity
    formal = " ".join(sorted(fields.get("natureza", set()) | fields.get("classe", set())))
    declared = plain(formal)
    formal_classes = {
        name for name, pattern in (
            ("PCA", r"prestacao de contas|\bpca\b"),
            ("ATOS_PESSOAL", r"aposentadoria|pensao|reforma|admissao"),
            ("INSPECAO", r"inspecao especial"), ("TCE", r"tomada de contas especial"),
            ("DENUNCIA", r"denuncia"), ("REPRESENTACAO", r"representacao"),
            ("CONSULTA", r"consulta"), ("RECURSO", r"recurso"), ("RESCISORIO", r"rescisorio"),
        ) if re.search(pattern, declared)
    }
    if len(formal_classes) > 1:
        result.update(estado="PENDENTE", motivo="Classes processuais divergentes; conferir a natureza efetiva.")
        return result
    legacy = record.get("subtipo") or ""
    # No esquema legado todo registro era obrigatoriamente PCA. A natureza
    # documental explícita prevalece e discrepâncias vão para conferência.
    is_pca = bool(re.search(r"prestacao de contas|\bpca\b", declared)) or (not declared and legacy in (
        "Prefeitura", "Câmara Municipal", "Instituto/Autarquia", "Secretaria Municipal ou Estadual"))
    selected = None
    if is_pca:
        result["classe"] = "PCA"
        e = plain(entity)
        matches = []
        if re.search(r"previdencia|previdenciario|\brpps\b", e): matches.append("pca.rpps")
        if "camara municipal" in e or (not e and legacy == "Câmara Municipal"): matches.append("pca.camaras")
        if "secretaria municipal" in e: matches.append("pca.secretarias_municipais")
        if re.search(r"secretaria (?:de estado|estadual)|orgao estadual", e): matches.append("pca.orgaos_estaduais")
        if "fundo " in e and not matches: matches.append("pca.fundos")
        modality = plain(" ".join(sorted(fields.get("modalidade", set()))) + " " + formal)
        prefeitura_dimensions = []
        if legacy == "Prefeitura" or "prefeitura" in e:
            matches.append("pca.prefeituras")
            if "contas de governo" in modality:
                prefeitura_dimensions.append("Contas de Governo")
            if "contas de gestao" in modality:
                prefeitura_dimensions.append("Contas de Gestão")
        if len(matches) == 1:
            selected = "elvira." + matches[0]
        elif matches or legacy:
            result.update(estado="PENDENTE", motivo="Confirmar perfil do jurisdicionado ou modalidade das contas.")
        expected = {"pca.rpps": "Instituto/Autarquia", "pca.camaras": "Câmara Municipal",
                    "pca.secretarias_municipais": "Secretaria Municipal ou Estadual",
                    "pca.orgaos_estaduais": "Secretaria Municipal ou Estadual",
                    "pca.prefeituras": "Prefeitura"}
        if selected and legacy and expected.get(selected[7:], legacy) != legacy:
            selected = None
            result.update(estado="PENDENTE", motivo="Subtipo cadastrado conflita com identificação documental.")
    else:
        for term, suffix, formal_class in (
            ("aposentadoria", "pessoal.aposentadorias", "ATOS_PESSOAL"),
            ("pensao", "pessoal.pensoes", "ATOS_PESSOAL"),
            ("reforma", "pessoal.reformas", "ATOS_PESSOAL"),
            ("admissao", "pessoal.admissoes", "ATOS_PESSOAL"),
            ("inspecao especial", "inspecao", "INSPECAO"),
            ("tomada de contas especial", "tce", "TCE"),
            ("denuncia", "denuncias", "DENUNCIA"),
            ("representacao", "representacoes", "REPRESENTACAO"),
            ("consulta", "consultas", "CONSULTA"),
            ("recurso", "recursos", "RECURSO"),
            ("pedido rescisorio", "rescisorios", "RESCISORIO"),
        ):
            if term in declared:
                if legacy:
                    result.update(estado="PENDENTE", motivo="Classe documental conflita com cadastro legado de PCA.")
                    return result
                selected, result["classe"] = "elvira." + suffix, formal_class
                break
    if selected:
        profile = scope(selected)
        result.update(estado="CLASSIFICADO", especialidade_id=selected, perfil=profile["perfil"],
                      modalidade=" e ".join(prefeitura_dimensions) if selected == PREFEITURAS and prefeitura_dimensions else profile["nome"],
                      evidencia=[entity or legacy, formal or "Cadastro legado de PCA"],
                      motivo="Identificação expressa e compatível com os vínculos existentes.")
    return result
