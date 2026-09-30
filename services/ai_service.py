"""Isolated Gemini prototype. It does not persist files, prompts or answers.

The institutional API key stays in Streamlit Secrets (GEMINI_API_KEY).
User login remains the existing OAuth flow and is not used for this call.
"""

import base64
from datetime import datetime
import json
import logging
import random
import re
import time
import unicodedata
import urllib.error
import urllib.request

LOGGER = logging.getLogger("mpc.ai")


def _endpoint(model):
    return (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        + model
        + ":generateContent"
    )


GEMINI_MODEL = "gemini-3.5-flash-lite"
GEMINI_FALLBACK_MODEL = "gemini-3.1-flash-lite"
GEMINI_ENDPOINT = _endpoint(GEMINI_MODEL)
TIMEOUT_SECONDS = 120
MAX_ATTEMPTS = 3
SERVER_ATTEMPTS = 4
RETRY_DELAYS_SECONDS = (1, 2)
SERVER_RETRY_DELAYS_SECONDS = (1, 2, 4)
JITTER_MAX_SECONDS = 0.25
RETRY_AFTER_MAX_SECONDS = 30
MAX_PDF_BYTES = 10 * 1024 * 1024
MAX_RESPONSE_BYTES = 1_000_000
MENSAGEM_NAO_CONFIGURADA = "Gemini API não configurada neste ambiente."
MENSAGEM_LIMITE_TEMPORARIO = (
    "O serviço de IA está temporariamente com muitas solicitações. "
    "Tente novamente em instantes."
)
MENSAGEM_COTA_DIARIA = (
    "A cota diária disponível para processamento por IA foi atingida. "
    "Tente novamente após a renovação das cotas."
)
MENSAGEM_SOBRECARGA = (
    "O modelo de IA está temporariamente sobrecarregado. "
    "Tente novamente em alguns minutos."
)
_MODULO_OPERACAO = {
    "laboratorio_resumo": "laboratorio",
    "representacao_resumo": "representacoes",
    "oficio_extracao": "oficios",
    "agenda_analise": "agenda",
    "tarefas_analise": "tarefas",
}
PROMPT_RESUMO = (
    "Analise exclusivamente o documento PDF fornecido.\n"
    "\n"
    "Produza um resumo objetivo, em português, contendo, quando existirem no documento:\n"
    "\n"
    "- objeto;\n"
    "- fatos principais;\n"
    "- possíveis irregularidades apontadas;\n"
    "- pedido cautelar;\n"
    "- pedidos finais.\n"
    "\n"
    "Não acrescente informações que não estejam no documento.\n"
    "Não faça inferências jurídicas além do conteúdo apresentado.\n"
    "Se determinada informação não estiver presente, "
    "informe que não foi identificada no documento."
)
PROMPT_EXTRACAO_OFICIO = (
    "Você extrai dados administrativos de um Ofício recebido em PDF.\n"
    "Leia o documento integralmente.\n"
    "Número do Ofício, remetente, cargo, instituição, processo, data e prazo "
    "exigem informação efetivamente presente e identificável com segurança.\n"
    "Se um desses dados não estiver claramente no documento, devolva string vazia.\n"
    "Não infira, não complete, não deduza e não invente esses campos.\n"
    "Não presuma órgão a partir de e-mail ou de domínio.\n"
    "Não transforme número incidental em número de processo.\n"
    "Não trate data citada no corpo como data do Ofício, "
    "salvo se for a data de emissão do documento.\n"
    "Não invente cargo, destinatário, número ou prazo.\n"
    "O remetente é quem expede o Ofício, não uma pessoa apenas mencionada.\n"
    "A instituição é o órgão remetente, não o destinatário.\n"
    "O número do Ofício identifica este documento, não outro número citado.\n"
    "O processo é o número de processo ou referência processual explícita, "
    "não o número do Ofício nem número incidental.\n"
    "A data é a data de emissão do documento, no formato AAAA-MM-DD.\n"
    "O prazo só deve ser preenchido quando houver data final claramente "
    "identificável, no formato AAAA-MM-DD.\n"
    'Prazo relativo, como "no prazo de 10 dias", permanece vazio. '
    "Não calcule a data final. Esse prazo pode ser mencionado numa providência.\n"
    "O assunto é um título cadastral curto, em uma frase, fiel ao documento "
    "e útil para identificar o Ofício sem abri-lo.\n"
    "Não copie um rótulo genérico como Convite, Solicitação, Informação, "
    "Encaminhamento, Comunicação ou Resposta quando o corpo permitir "
    "completar a finalidade com dados presentes.\n"
    'Exemplo: cabeçalho "Assunto: Convite" e corpo sobre Roda de Conversa '
    "de Saúde Mental alusiva ao Setembro Amarelo produzem assunto semelhante a "
    '"Convite para Roda de Conversa sobre Saúde Mental – Setembro Amarelo", '
    'e não apenas "Convite".\n'
    'Exemplo: cabeçalho "Assunto: Solicitação" e corpo que pede informações '
    "sobre um processo produzem assunto semelhante a "
    '"Solicitação de informações sobre o Processo nº" seguido do número '
    "presente no documento.\n"
    "Se o assunto original já for descritivo, preserve-o ou faça só ajuste "
    "mínimo de redação.\n"
    "Não transforme o assunto em resumo, juízo de valor, interpretação "
    "jurídica, obrigação inexistente ou objetivo inventado.\n"
    "providencias_sugeridas traz uma ou duas sugestões administrativas úteis, "
    "nunca mais que duas.\n"
    "Em condições normais, inclua pelo menos uma sugestão, mesmo sem ordem "
    "expressa no documento.\n"
    "Devolva lista vazia somente se o documento estiver ilegível, sem conteúdo "
    "suficiente, incompatível com análise administrativa, ou se qualquer "
    "sugestão carecer de base no texto.\n"
    "Antes de inferir uma providência, identifique pedido, solicitação, "
    "resposta requerida, confirmação, envio de informação, comparecimento, "
    "manifestação ou outra ação expressamente dirigida ao destinatário.\n"
    "Se houver ação expressamente solicitada, ela é a primeira sugestão, "
    "objetiva e fiel ao documento. A segunda, se couber, é complementar e "
    "inferida. Não crie terceira sugestão.\n"
    "Incorpore data, horário, prazo, forma de participação ou reunião somente "
    "quando esses dados estiverem claramente no documento. Não os invente.\n"
    "Linguagem condicional, como avaliar, se for o caso ou caso haja interesse, "
    "vale somente para ação que o Ofício não determinou.\n"
    "Pedido expresso de informações até uma data produz sugestão direta, "
    'como "Encaminhar as informações solicitadas até" a data presente no texto. '
    "Não suavize esse pedido com avaliar se é o caso.\n"
    "Se o documento apenas convida, sem pedir confirmação, não invente "
    "solicitação. Nesse caso prefira "
    '"Avaliar a participação e, se for o caso, confirmar presença".\n'
    'Não escreva "Confirmar presença conforme solicitado" nem '
    '"Agendar a reunião" quando o documento não exigir isso.\n'
    "Se o documento pedir expressamente a confirmação da participação, "
    "a primeira sugestão confirma essa participação, sem tratar o pedido "
    "como hipótese.\n"
    "Exemplo: convite para reunião por videoconferência em 11/09/2026, às 11h, "
    "com a frase de que se confirme a participação, produz primeira sugestão "
    "semelhante a confirmar a participação na reunião por videoconferência "
    "prevista para 11/09/2026, às 11h, e segunda sugestão condicional, "
    "como em caso de participação providenciar o registro do compromisso na "
    "agenda institucional.\n"
    "Quando a confirmação não for pedida, a sugestão complementar continua "
    "condicional, como caso haja participação providenciar o registro do "
    "compromisso. Não afirme presença confirmada nem agendamento obrigatório.\n"
    "Solicitação expressa prioriza responder, confirmar, enviar, encaminhar, "
    "comparecer, esclarecer ou providenciar o que foi pedido.\n"
    "Documento informativo pode sugerir ciência à autoridade interessada ou "
    "avaliar se a informação demanda providência. Não repita a mesma frase "
    "genérica em todo documento.\n"
    "Encaminhamento pode sugerir analisar o material ou direcioná-lo quando "
    "o contexto indicar a unidade ou autoridade.\n"
    "Resposta recebida pode sugerir verificar se atende ao pedido, dar ciência "
    "ao responsável ou avaliar manifestação complementar.\n"
    "Não use fórmulas vazias como tomar as providências cabíveis, analisar o "
    "documento ou encaminhar ao setor competente.\n"
    "Não crie obrigação inexistente, não afirme conclusão jurídica e não "
    "assuma decisão do MPC-PB.\n"
    "As sugestões são apenas texto. Não afirme que tarefa, agenda, pendência, "
    "e-mail ou resposta foram criados.\n"
    "Responda somente com um objeto JSON contendo exatamente as chaves "
    "numero_externo, remetente, cargo_remetente, instituicao, assunto, "
    "processo, data, prazo e providencias_sugeridas.\n"
    "providencias_sugeridas é uma lista de strings, normalmente com uma ou "
    "duas e no máximo duas."
)
OFICIO_EXTRAIDO_VAZIO = {
    "numero_externo": "",
    "remetente": "",
    "cargo_remetente": "",
    "instituicao": "",
    "assunto": "",
    "processo": "",
    "data": "",
    "prazo": "",
    "providencias_sugeridas": [],
}
_OFICIO_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "numero_externo": {"type": "STRING"},
        "remetente": {"type": "STRING"},
        "cargo_remetente": {"type": "STRING"},
        "instituicao": {"type": "STRING"},
        "assunto": {"type": "STRING"},
        "processo": {"type": "STRING"},
        "data": {"type": "STRING"},
        "prazo": {"type": "STRING"},
        "providencias_sugeridas": {"type": "ARRAY", "items": {"type": "STRING"}},
    },
    "required": list(OFICIO_EXTRAIDO_VAZIO),
}
_OFICIO_TEXTO = 500
_OFICIO_PROVIDENCIA = 240
MAX_ANALISE_AGENDA_BYTES = 80_000
MAX_ANALISE_TAREFAS_BYTES = 80_000
PROMPT_ANALISE_AGENDA = (
    "Você recebe dados estruturados de um período da Agenda institucional.\n"
    "Os fatos, contagens, sobreposições e coincidências já foram calculados pelo sistema.\n"
    "Não recalcule esses fatos e não os contradiga.\n"
    "Use apenas os dados fornecidos.\n"
    "Não invente compromissos, conflitos, ausências, responsáveis, prioridades ou providências.\n"
    "Produza um panorama executivo curto, institucional e objetivo.\n"
    "Ao descrever a distribuição, fale em maior concentração de registros da Agenda.\n"
    "Não use a expressão dia mais carregado.\n"
    "Se dias_maior_concentracao tiver mais de um dia, mencione todos.\n"
    "Apresente todas as datas mencionadas na resposta exclusivamente no formato brasileiro DD/MM/AAAA. "
    "Nunca utilize o formato ISO AAAA-MM-DD.\n"
    "Mencione afastamentos relevantes que constem dos dados.\n"
    "Mencione somente as sobreposições já informadas.\n"
    "Coincidência entre afastamento e compromisso é apenas coincidência temporal.\n"
    "O afastamento pode decorrer do próprio compromisso, de viagem, evento ou atividade registrada.\n"
    "Não chame essa coincidência de conflito, inconsistência ou erro.\n"
    "Não diga que a coincidência merece conferência sem elemento concreto de incompatibilidade nos dados.\n"
    "Se os registros aparentarem ser coerentes, mencione a coincidência de forma informativa "
    "ou não a destaque como ponto de atenção.\n"
    "Não invente incompatibilidade.\n"
    "Não decida cancelamento, reagendamento ou substituição.\n"
    "Não indique substituto.\n"
    "Não crie obrigação.\n"
    "Não gere tarefa, pendência, notificação, encaminhamento ou alteração da agenda.\n"
    "Não use frases genéricas como organizar a agenda ou planejar-se.\n"
    "Não transforme a resposta em lista extensa."
)
PROMPT_ANALISE_TAREFAS = (
    "Você recebe dados estruturados das tarefas ativas de um único usuário do MPC-PB.\n"
    "Use exclusivamente os dados fornecidos pelo sistema e as contagens já calculadas.\n"
    "Não recalcule fatos simples nem contradiga os dados.\n"
    "Não invente fatos, tarefas, prazos, responsáveis, prioridades, status, módulos ou agrupamentos.\n"
    "Uma tarefa só merece urgência quando a situação do prazo, a prioridade ou o conteúdo fornecido der base objetiva para isso.\n"
    "Não afirme que uma providência é juridicamente obrigatória sem informação suficiente.\n"
    "A análise é somente informativa: não marque tarefa como concluída, não altere status ou prioridade, não crie tarefa, não modifique dados, não dispare lembrete, notificação ou e-mail.\n"
    "Diferencie fatos registrados de eventuais sugestões de organização.\n"
    "Produza um briefing operacional curto, objetivo, profissional e adequado ao ambiente institucional.\n"
    "Estruture a resposta, quando houver dados para tanto, em Panorama geral, Prioridades imediatas, Prazos e pontos de atenção, Organização das pendências e Próximas ações.\n"
    "Em Panorama geral, use as contagens fornecidas.\n"
    "Em Prioridades imediatas e Próximas ações, destaque apenas tarefas com base objetiva nos dados.\n"
    "Em Organização das pendências, agrupe apenas quando houver relação clara por assunto, módulo ou natureza da providência.\n"
    "Não transforme sugestões em comandos administrativos ou decisões automáticas.\n"
    "Ao mencionar módulos do sistema, utilize exclusivamente seus nomes amigáveis destinados ao usuário. "
    "Nunca exponha identificadores técnicos, slugs, nomes de campos ou chaves internas. "
    "Nomes de módulos devem ser escritos como texto comum, sem crases, backticks ou formatação de código Markdown."
)
_STATUS_TOKEN = re.compile(r"[A-Z0-9_]{1,40}")


class GeminiNaoConfigurada(RuntimeError):
    def __init__(self):
        super().__init__(MENSAGEM_NAO_CONFIGURADA)


class GeminiErro(RuntimeError):
    """Message is already safe to show to an administrator."""


class _TextoModelo(str):
    """Summary text that also records the model which produced it."""

    def __new__(cls, texto, modelo):
        value = str.__new__(cls, texto)
        value.modelo = modelo
        return value


def gemini_disponivel():
    return bool(_api_key())


def resumir_documento_pdf(pdf_bytes, *, operacao="laboratorio_resumo"):
    """Send one PDF and the fixed prompt to Gemini. Nothing is stored."""
    document = _validar_pdf(pdf_bytes)
    raw, modelo = _consultar(
        document, PROMPT_RESUMO, "laboratório de IA", operacao=operacao
    )
    return _TextoModelo(_texto_resposta(raw), modelo)


def extrair_dados_oficio_pdf(pdf_bytes):
    """Read one received-ofício PDF and return validated form fields.

    Nothing is stored. Administrative fields are not part of the result.
    """
    document = _validar_pdf(pdf_bytes)
    raw, _modelo = _consultar(
        document,
        PROMPT_EXTRACAO_OFICIO,
        "extração de ofício",
        _OFICIO_SCHEMA,
        operacao="oficio_extracao",
    )
    return _dados_oficio(_texto_resposta(raw))


def analisar_periodo_agenda(contexto):
    """Turn already calculated period facts into a short briefing. Nothing is stored."""
    if not isinstance(contexto, dict):
        raise GeminiErro("Não foi possível preparar a análise do período.")
    text = json.dumps(
        contexto, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    if len(text.encode("utf-8")) > MAX_ANALISE_AGENDA_BYTES:
        raise GeminiErro("Não foi possível preparar a análise do período.")
    raw, _modelo = _executar(
        lambda key, model: _request_texto(text, key, PROMPT_ANALISE_AGENDA, model),
        "análise da agenda",
        operacao="agenda_analise",
    )
    return _texto_resposta(raw)


def analisar_tarefas_ativas(contexto):
    """Turn pre-filtered active-task facts into a briefing without persistence."""
    if not isinstance(contexto, dict) or not isinstance(contexto.get("tarefas"), list):
        raise GeminiErro("Não foi possível preparar a análise das tarefas.")
    if not contexto["tarefas"]:
        raise GeminiErro("Não há tarefas ativas para análise no momento.")
    text = json.dumps(
        contexto, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    if len(text.encode("utf-8")) > MAX_ANALISE_TAREFAS_BYTES:
        raise GeminiErro("Há muitas informações para analisar de uma só vez.")
    raw, _modelo = _executar(
        lambda key, model: _request_texto(text, key, PROMPT_ANALISE_TAREFAS, model),
        "análise das tarefas",
        operacao="tarefas_analise",
    )
    return _texto_resposta(raw)


def _consultar(document, prompt, rotulo, schema=None, operacao="laboratorio_resumo"):
    return _executar(
        lambda key, model: _request(document, key, prompt, schema, model),
        rotulo,
        operacao=operacao,
    )


def _executar(montar, rotulo, operacao):
    inicio = time.perf_counter()
    estado = {
        "operacao": operacao,
        "tentativas": 0,
        "fallback": False,
        "via_quota": False,
        "via_503": False,
        "http_status": None,
        "viu_429": False,
        "viu_500": False,
        "viu_503": False,
        "modelo": GEMINI_MODEL,
    }
    key = _api_key()
    if not key:
        _registrar_operacao(inicio, estado, False, "nao_configurada")
        raise GeminiNaoConfigurada()
    modelo = GEMINI_MODEL
    attempt = 1
    while True:
        estado["tentativas"] += 1
        estado["modelo"] = modelo
        try:
            raw = _post(montar(key, modelo))
        except TimeoutError:
            # The call already waited TIMEOUT_SECONDS. Another round could
            # hold the page for several minutes, so this failure is final.
            LOGGER.warning("Chamada ao %s expirou.", rotulo)
            _registrar_operacao(inicio, estado, False, "timeout")
            raise GeminiErro("O serviço de IA não respondeu a tempo.") from None
        except urllib.error.HTTPError as exc:
            code, status, retry_after, cota_diaria = _detalhe_http(exc)
            estado["http_status"] = code
            if code == 429:
                estado["viu_429"] = True
            elif code == 500:
                estado["viu_500"] = True
            elif code == 503:
                estado["viu_503"] = True
            if cota_diaria and modelo == GEMINI_MODEL and not estado["via_quota"]:
                LOGGER.warning(
                    "Gemini 3.5 Flash-Lite: cota diária esgotada. "
                    "Tentando modelo reserva."
                )
                LOGGER.warning("Fallback Gemini utilizado: %s.", GEMINI_FALLBACK_MODEL)
                modelo = GEMINI_FALLBACK_MODEL
                estado["fallback"] = True
                estado["via_quota"] = True
                attempt = 1
                continue
            if cota_diaria:
                LOGGER.warning("Cota diária esgotada no modelo %s.", modelo)
                _registrar_operacao(inicio, estado, False, "quota_diaria")
                raise GeminiErro(MENSAGEM_COTA_DIARIA) from None
            if _deve_repetir(code, status, attempt, estado):
                LOGGER.warning(
                    "Tentativa %s falhou com HTTP %s. Nova tentativa será realizada.",
                    attempt,
                    code,
                )
                _sleep(_espera(attempt, retry_after, servidor=_erro_servidor(code)))
                attempt += 1
                continue
            if _fallback_503(code, modelo, estado):
                LOGGER.warning(
                    "HTTP 503 persistente no modelo principal. "
                    "Uma tentativa usará o modelo reserva."
                )
                LOGGER.warning("Fallback Gemini utilizado: %s.", GEMINI_FALLBACK_MODEL)
                modelo = GEMINI_FALLBACK_MODEL
                estado["fallback"] = True
                estado["via_503"] = True
                attempt = 1
                continue
            LOGGER.warning(
                "Chamada ao %s falhou (HTTP %s, %s).",
                rotulo,
                code,
                status or "sem_status",
            )
            _registrar_operacao(
                inicio, estado, False, _categoria_http(code, cota_diaria)
            )
            raise GeminiErro(_mensagem_http(code, status)) from None
        except urllib.error.URLError as exc:
            if isinstance(getattr(exc, "reason", None), TimeoutError):
                LOGGER.warning("Chamada ao %s expirou.", rotulo)
                _registrar_operacao(inicio, estado, False, "timeout")
                raise GeminiErro("O serviço de IA não respondeu a tempo.") from None
            if attempt < MAX_ATTEMPTS and not estado["via_503"]:
                LOGGER.warning(
                    "Tentativa %s falhou sem conexão. Nova tentativa será realizada.",
                    attempt,
                )
                _sleep(_espera(attempt, None))
                attempt += 1
                continue
            LOGGER.warning("Chamada ao %s sem conexão.", rotulo)
            _registrar_operacao(inicio, estado, False, "conexao")
            raise GeminiErro("Não foi possível conectar ao serviço de IA.") from None
        _registrar_operacao(inicio, estado, True, "")
        return raw, modelo


def _api_key():
    try:
        import streamlit as st

        value = st.secrets.get("GEMINI_API_KEY")
    except Exception:
        return ""
    if not isinstance(value, str):
        return ""
    return value.strip()


def _validar_pdf(pdf_bytes):
    if not isinstance(pdf_bytes, (bytes, bytearray)):
        raise GeminiErro("O arquivo enviado não é um PDF válido.")
    document = bytes(pdf_bytes)
    if not document:
        raise GeminiErro("O arquivo PDF está vazio.")
    if len(document) > MAX_PDF_BYTES:
        raise GeminiErro(
            "O PDF excede o limite máximo de 10 MB para processamento por IA."
        )
    if b"%PDF" not in document[:1024]:
        raise GeminiErro("O arquivo enviado não é um PDF válido.")
    return document


def _request(document, key, prompt, schema=None, model=GEMINI_MODEL):
    payload = {
        "contents": [
            {
                "role": "user",
                "parts": [
                    {"text": prompt},
                    {
                        "inlineData": {
                            "mimeType": "application/pdf",
                            "data": base64.b64encode(document).decode("ascii"),
                        }
                    },
                ],
            }
        ]
    }
    if schema is not None:
        payload["generationConfig"] = {
            "responseMimeType": "application/json",
            "responseSchema": schema,
        }
    return urllib.request.Request(
        _endpoint(model),
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "x-goog-api-key": key,
        },
        method="POST",
    )


def _request_texto(texto, key, prompt, model=GEMINI_MODEL):
    payload = {
        "contents": [
            {
                "role": "user",
                "parts": [{"text": prompt + "\n\nDados:\n" + texto}],
            }
        ]
    }
    return urllib.request.Request(
        _endpoint(model),
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "x-goog-api-key": key,
        },
        method="POST",
    )


def _sleep(seconds):
    time.sleep(seconds)


def _jitter(base):
    """Small spread so retries do not land on the same provider instant."""
    if not base:
        return 0.0
    return random.uniform(0, JITTER_MAX_SECONDS)


def _espera(attempt, retry_after, *, servidor=False):
    if retry_after is not None:
        return retry_after
    if servidor:
        base = SERVER_RETRY_DELAYS_SECONDS[attempt - 1]
        return base + _jitter(base)
    return RETRY_DELAYS_SECONDS[attempt - 1]


def _erro_servidor(code):
    return code in {500, 502, 503}


def _deve_repetir(code, status, attempt, estado):
    if not _repetir_http(code, status):
        return False
    if estado["via_503"]:
        return False
    if _erro_servidor(code) and not estado["via_quota"]:
        return attempt < SERVER_ATTEMPTS
    return attempt < MAX_ATTEMPTS


def _fallback_503(code, modelo, estado):
    return (
        code == 503
        and modelo == GEMINI_MODEL
        and not estado["via_quota"]
        and not estado["via_503"]
    )


def _categoria_http(code, cota_diaria):
    if cota_diaria:
        return "quota_diaria"
    if code in {400, 401, 403, 404, 408, 413, 429, 500, 502, 503, 504}:
        return "http_" + str(code)
    return "outro"


def _registrar_operacao(inicio, estado, sucesso, categoria):
    duracao_ms = int((time.perf_counter() - inicio) * 1000)
    operacao = estado["operacao"]
    evento = {
        "modulo": _MODULO_OPERACAO.get(operacao, "desconhecido"),
        "operacao": operacao,
        "modelo_principal": GEMINI_MODEL,
        "modelo_final": estado["modelo"],
        "tentativas": estado["tentativas"],
        "retry": estado["tentativas"] > 1,
        "fallback": estado["fallback"],
        "sucesso": sucesso,
        "http_status": 200 if sucesso else estado["http_status"],
        "categoria_erro": "" if sucesso else categoria,
        "duracao_ms": duracao_ms,
        "viu_429": estado["viu_429"],
        "viu_500": estado["viu_500"],
        "viu_503": estado["viu_503"],
    }
    try:
        _gravar_telemetria(evento)
    except Exception as exc:
        LOGGER.warning("Telemetria de IA não foi gravada (%s).", type(exc).__name__)


def _gravar_telemetria(evento):
    try:
        from database.ia_telemetria import registrar
        from database.store import Store

        registrar(Store(), evento)
    except Exception as exc:
        LOGGER.warning("Telemetria de IA não foi gravada (%s).", type(exc).__name__)


def _repetir_http(code, status):
    if code in {400, 401, 403, 404, 408, 413, 504}:
        return False
    if code == 429 or status == "RESOURCE_EXHAUSTED":
        return True
    return code in {500, 502, 503}


def _detalhe_http(exc):
    code = int(getattr(exc, "code", 0) or 0)
    error = _corpo_erro(exc)
    status = ""
    if isinstance(error, dict):
        raw_status = str(error.get("status") or "")
        if _STATUS_TOKEN.fullmatch(raw_status):
            status = raw_status
    retry_after = None
    if code == 429 or status == "RESOURCE_EXHAUSTED":
        retry_after = _retry_after_seconds(exc)
    return code, status, retry_after, _cota_diaria(error)


def _retry_after_seconds(exc):
    headers = getattr(exc, "headers", None)
    if not headers:
        return None
    try:
        raw = headers.get("Retry-After")
    except Exception:
        return None
    if raw is None:
        return None
    text = str(raw).strip()
    if not text.isdigit():
        return None
    seconds = int(text)
    if seconds > RETRY_AFTER_MAX_SECONDS:
        return None
    return seconds


def _post(request):
    with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
        return response.read(MAX_RESPONSE_BYTES + 1)


def _texto_resposta(raw):
    if not raw or len(raw) > MAX_RESPONSE_BYTES:
        LOGGER.warning("Resposta do laboratório de IA vazia ou longa demais.")
        raise GeminiErro("Não foi possível interpretar a resposta do serviço de IA.")
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        LOGGER.warning("Resposta do laboratório de IA não era JSON.")
        raise GeminiErro(
            "Não foi possível interpretar a resposta do serviço de IA."
        ) from None
    if not isinstance(data, dict):
        raise GeminiErro("Não foi possível interpretar a resposta do serviço de IA.")
    candidates = data.get("candidates") or []
    if not candidates:
        LOGGER.warning("Resposta do laboratório de IA sem candidatos.")
        if (data.get("promptFeedback") or {}).get("blockReason"):
            raise GeminiErro("A análise foi recusada pelo serviço de IA.")
        raise GeminiErro("A IA não retornou conteúdo.")
    parts = (candidates[0].get("content") or {}).get("parts") or []
    texts = []
    for part in parts:
        if not isinstance(part, dict) or part.get("thought"):
            continue
        text = str(part.get("text") or "").strip()
        if text:
            texts.append(text)
    summary = "\n".join(texts).strip()
    if not summary:
        LOGGER.warning("Resposta do laboratório de IA sem texto.")
        raise GeminiErro("A IA não retornou conteúdo.")
    return summary


def _dados_oficio(text):
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.I)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        LOGGER.warning("Extração de ofício retornou estrutura inválida.")
        raise GeminiErro(
            "Não foi possível interpretar a resposta do serviço de IA."
        ) from None
    if not isinstance(data, dict):
        LOGGER.warning("Extração de ofício retornou estrutura inválida.")
        raise GeminiErro("Não foi possível interpretar a resposta do serviço de IA.")
    result = dict(OFICIO_EXTRAIDO_VAZIO)
    for field in (
        "numero_externo",
        "remetente",
        "cargo_remetente",
        "instituicao",
        "assunto",
        "processo",
    ):
        result[field] = _texto_oficio(data.get(field), _OFICIO_TEXTO)
    for field in ("data", "prazo"):
        result[field] = _data_oficio(data.get(field))
    result["providencias_sugeridas"] = _providencias_oficio(
        data.get("providencias_sugeridas")
    )
    return result


def _texto_oficio(value, limit):
    if not isinstance(value, str):
        return ""
    return " ".join(value.split())[:limit]


def _data_oficio(value):
    text = _texto_oficio(value, 32)
    if not text:
        return ""
    for pattern in ("%Y-%m-%d", "%d/%m/%Y"):
        try:
            parsed = datetime.strptime(text, pattern).date()
        except ValueError:
            continue
        if 1000 <= parsed.year <= 2100:
            return parsed.isoformat()
    return ""


def _providencias_oficio(value):
    if not isinstance(value, list):
        return []
    found = []
    for item in value:
        text = _texto_oficio(item, _OFICIO_PROVIDENCIA)
        if not text or _providencia_vazia(text):
            continue
        found.append(text)
        if len(found) == 2:
            break
    return found


def _providencia_vazia(text):
    folded = unicodedata.normalize("NFKD", text)
    plain = "".join(ch for ch in folded if not unicodedata.combining(ch)).lower()
    return plain.startswith("nenhuma providencia especifica")


def _corpo_erro(exc):
    try:
        raw = exc.read(16384)
    except Exception:
        return {}
    if not raw:
        return {}
    try:
        payload = json.loads(raw.decode("utf-8", errors="replace"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {}
    error = payload.get("error") if isinstance(payload, dict) else None
    if not isinstance(error, dict):
        return {}
    return error


def _cota_diaria(error):
    """True only when structured quota metadata identifies a daily request cap."""
    if not isinstance(error, dict):
        return False
    details = error.get("details")
    if not isinstance(details, list):
        return False
    for detail in details:
        if not isinstance(detail, dict):
            continue
        kind = str(detail.get("@type") or "")
        if kind.endswith("QuotaFailure"):
            violations = detail.get("violations")
            if not isinstance(violations, list):
                continue
            for violation in violations:
                if isinstance(violation, dict) and _identificador_rpd(
                    violation.get("quotaId"), violation.get("quotaMetric")
                ):
                    return True
        elif kind.endswith("ErrorInfo"):
            meta = detail.get("metadata")
            if not isinstance(meta, dict):
                continue
            if _identificador_rpd(
                meta.get("quota_limit") or meta.get("quotaId"),
                meta.get("quota_metric") or meta.get("quotaMetric"),
            ):
                return True
    return False


def _identificador_rpd(quota_id, quota_metric):
    ident = re.sub(r"[^A-Za-z0-9]", "", str(quota_id or ""))
    if not ident or not re.search(r"perday", ident, re.I):
        return False
    if re.search(r"minute|second|hour|token", ident, re.I):
        return False
    if not re.search(r"request", ident, re.I):
        return False
    metric = str(quota_metric or "").strip()
    if not metric:
        return True
    compact = re.sub(r"[^A-Za-z0-9]", "", metric)
    if re.search(r"token", compact, re.I):
        return False
    return re.search(r"request", compact, re.I) is not None


def _mensagem_http(code, status):
    if code == 429 or status == "RESOURCE_EXHAUSTED":
        return MENSAGEM_LIMITE_TEMPORARIO
    if code == 404 or status in {"NOT_FOUND", "UNIMPLEMENTED"}:
        return "O modelo de IA não está disponível neste ambiente."
    if code in {401, 403} or status in {"UNAUTHENTICATED", "PERMISSION_DENIED"}:
        return "A Gemini API recusou a credencial deste ambiente."
    if code in {400, 413} or status == "INVALID_ARGUMENT":
        return "O serviço de IA não conseguiu ler o PDF enviado."
    if code in {408, 504} or status == "DEADLINE_EXCEEDED":
        return "O serviço de IA não respondeu a tempo."
    if code == 503 or status == "UNAVAILABLE":
        return MENSAGEM_SOBRECARGA
    if code in {500, 502} or status == "INTERNAL":
        return "O serviço de IA está indisponível no momento."
    return "O serviço de IA recusou a solicitação."
