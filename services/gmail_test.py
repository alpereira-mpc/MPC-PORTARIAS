"""One explicit administrator test message. It does not touch representações."""

from datetime import datetime

from services.access import require_permission
from services.audit import INSTITUTIONAL_TZ, registrar_evento
from services.email_transport import (
    NOT_CONFIGURED,
    SENDER_ADDRESS,
    DeliveryRejected,
    DeliveryUncertain,
    NotConfigured,
    institutional_transport,
)
from services.notifications import email_valid, normalize_email

TEST_SUBJECT = "Teste de envio institucional — Ferramentas MPC-PB"


def institutional_recipient(value):
    email = normalize_email(value)
    if not email_valid(email) or not email.endswith("@tce.pb.gov.br"):
        raise ValueError("O teste aceita somente um endereço @tce.pb.gov.br.")
    return email


def test_message(recipient, when=None):
    moment = when or datetime.now(INSTITUTIONAL_TZ)
    stamp = moment.strftime("%d/%m/%Y às %H:%M")
    text = (
        "Este é um teste de envio institucional realizado pelo sistema Ferramentas MPC-PB.\n\n"
        "Remetente: " + SENDER_ADDRESS + "\n"
        "Data/hora: " + stamp + "\n\n"
        "Nenhuma ação é necessária.\n"
    )
    return {
        "to": [recipient],
        "subject": TEST_SUBJECT,
        "text": text,
        "html": "",
    }


def send_institutional_test(store, principal, recipient, transport=None):
    require_permission(principal, "admin")
    require_permission(principal, "comunicacoes_enviar_teste")
    address = institutional_recipient(recipient)
    if transport is None:
        try:
            transport = institutional_transport()
        except NotConfigured as exc:
            _audit(store, principal, address, resultado="ERRO", extra={"motivo": "nao_configurado"})
            raise NotConfigured(str(exc) or NOT_CONFIGURED) from None
    message = test_message(address)
    try:
        provider_id = transport.send(message)
    except DeliveryUncertain:
        _audit(store, principal, address, resultado="ATENCAO", extra={"motivo": "incerto"})
        raise ValueError(
            "Não foi possível confirmar se o provedor aceitou a mensagem de teste."
        ) from None
    except DeliveryRejected as exc:
        _audit(
            store,
            principal,
            address,
            resultado="ERRO",
            extra={
                "http_status": exc.http_status,
                "codigo": exc.error_code,
                "descricao": exc.detail,
            },
        )
        raise
    _audit(
        store,
        principal,
        address,
        extra={"provedor_mensagem_id": provider_id or ""},
    )
    return provider_id


def _audit(store, principal, recipient, *, resultado="OK", extra=None):
    detalhes = {"destinatario": recipient, "remetente": SENDER_ADDRESS}
    if extra:
        detalhes.update({key: value for key, value in extra.items() if value not in (None, "")})
    registrar_evento(
        store,
        evento="GMAIL_TESTE_ENVIO",
        modulo="admin",
        acao="ENVIAR",
        resultado=resultado,
        principal=principal,
        entidade_tipo="gmail_teste",
        detalhes=detalhes,
    )
