"""AppTest helper: three received details in one rerun. Not a user-facing probe."""

from services.oficios_ui import details


class _Store:
    backend = "sqlite"

    def catalog(self, name):
        return []


class _Service:
    store = _Store()

    def files(self, identifier):
        return []

    def list(self, **kwargs):
        return []

    def movements(self, identifier):
        return [
            {
                "instante": "2026-01-01 10:00",
                "anterior": "Recebido",
                "novo": "Em análise",
                "observacao": "<b>x</b>",
            }
        ]


def _record(identifier):
    return {
        "id": identifier,
        "direcao": "RECEBIDO",
        "status": "Recebido",
        "data": "2026-01-15",
        "signatario": None,
        "remetente": None,
        "destinatario": None,
        "instituicao": None,
        "processo": None,
        "procedimento": None,
        "referencia": None,
        "observacoes": None,
        "membros": None,
        "corpo": None,
        "responde_a": None,
        "prazo": None,
        "serie": "PROGE",
        "numero": 1,
        "numero_externo": None,
        "ano": 2026,
        "assunto": "Oficio de teste",
    }


service = _Service()
details(service, _record("id-alpha"))
details(service, _record("id-beta"))
details(service, _record("id-gamma"))
