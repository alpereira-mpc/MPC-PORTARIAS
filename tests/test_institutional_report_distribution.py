"""Phase 5: official PDF distribution with a mocked Gmail transport."""

import base64
import hashlib
import inspect
from email import message_from_bytes

import pytest
from streamlit.testing.v1 import AppTest

from database.institutional_reports import InstitutionalReportsStore
from database.notifications import NotificationsStore
from database.postgresql import (
    INSTITUTIONAL_DISTRIBUTION_MIGRATION_SQL,
    INSTITUTIONAL_REPORTS_MIGRATION_SQL,
    TABLES,
    PostgresBackend,
)
from database.store import Store
from services.access import Principal
from services.email_transport import SENDER_ADDRESS, DeliveryRejected, DeliveryUncertain
from services.gmail_transport import _raw_message
from services.institutional_reports import EMPTY_STRUCTURED_CONTENT
from services.institutional_report_distribution import (
    CHANGED_PDF_MESSAGE,
    DRAFT_MESSAGE,
    DUPLICATE_EMAIL_PREFIX,
    HASH_MESSAGE,
    MISSING_EMAIL_PREFIX,
    MISSING_PDF_MESSAGE,
    PERMISSION_MESSAGE,
    SIGNATURE_MESSAGE,
    UNCERTAIN_MESSAGE,
    assess_recipients,
    confirm_distribution,
    default_body,
    default_subject,
    list_report_distributions,
    official_pdf_metadata,
    retry_distribution,
)
from services.notifications import SUBSCRIPTION_PROTOCOLO
import services.institutional_report_distribution as distribution
import services.relatorios_ui as relatorios_ui


def _admin():
    return Principal(
        1, "André", "admin@test", "ADMINISTRADOR", True, True, True, True, True, ()
    )


def _reader():
    return Principal(
        2, "Leitor", "leitor@test", "CONSULTA", True, False, False, False, False, ()
    )


def _month(month):
    return {
        "month": month,
        "summary": {
            "distributed": 10 + month,
            "production": 12 + month,
            "opinions": 8,
            "quotas": 4,
            "production_rate": 110.0,
            "median_days": 7.8,
        },
    }


def _snapshot(tipo="TRIMESTRAL", quarter=3, months=None, partial=False):
    if tipo == "TRIMESTRAL":
        months = [7, 8, 9] if months is None else months
        partial = False
        start, end = "2026-07-01", "2026-10-01"
    else:
        months = list(range(1, 10)) if months is None else months
        start, end = "2026-01-01", "2026-10-01"
    return {
        "metadados": {
            "tipo": tipo,
            "ano": 2026,
            "trimestre": quarter if tipo == "TRIMESTRAL" else None,
            "data_inicio": start,
            "data_fim": end,
            "periodo_parcial": bool(partial),
            "descricao_periodo": "período congelado",
        },
        "cobertura_historica": {
            "meses_disponiveis": months,
            "meses_ausentes": [],
            "lacunas_no_ano": [],
        },
        "indicadores_gerais": {
            "distributed": 545,
            "production": 594,
            "opinions": 424,
            "quotas": 170,
            "production_rate": 109.0,
            "median_days": 7.8,
        },
        "serie_mensal": [_month(month) for month in months],
        "por_procurador": [
            {
                "procurador": "Procurador A",
                "distributed": 10,
                "production": 12,
                "opinions": 9,
                "quotas": 3,
                "production_rate": 120.0,
                "median_days": 8.0,
            }
        ],
        "composicao_producao": {
            "pareceres_percentual": 70.0,
            "cotas_percentual": 30.0,
        },
        "faixas_permanencia": [{"faixa": "0–7 dias", "quantidade": 100}],
        "comparacao_periodo_anterior": None,
        "nota_metodologica": {},
    }


class FakeTransport:
    """Stand-in for Gmail. Tests never open the institutional account."""

    def __init__(self, plan=None):
        self.plan = list(plan or [])
        self.calls = []

    def send(self, message):
        self.calls.append(message)
        if self.plan:
            outcome = self.plan.pop(0)
            if isinstance(outcome, BaseException):
                raise outcome
            return outcome
        return f"msg-{len(self.calls)}"


def _store(path):
    return Store(path)


def _people(store):
    return [person for person in store.catalog("procuradores") if person.get("ativo")]


def _give_emails(store, skip_ids=(), email_for=None):
    notes = NotificationsStore(store)
    for person in _people(store):
        if person["id"] in skip_ids:
            continue
        address = (
            email_for(person)
            if email_for
            else f"procurador{person['id']}@tce.pb.gov.br"
        )
        notes.save_recipient(
            SUBSCRIPTION_PROTOCOLO, "PROCURADOR", person["id"], address, True
        )


def _pdf(label):
    content = f"%PDF-1.4\n{label}\n".encode("ascii")
    return content, hashlib.sha256(content).hexdigest()


def _create(
    store,
    *,
    tipo="TRIMESTRAL",
    quarter=3,
    partial=False,
    months=None,
    pdf=True,
    name=None,
    status="FINALIZADO",
):
    repository = InstitutionalReportsStore(store)
    snapshot = _snapshot(tipo=tipo, quarter=quarter, months=months, partial=partial)
    metadata = snapshot["metadados"]
    if repository.latest_for_period(tipo, 2026, metadata["trimestre"]):
        report = repository.create_new_version(
            tipo=tipo,
            ano=2026,
            trimestre=metadata["trimestre"],
            data_inicio=metadata["data_inicio"],
            data_fim=metadata["data_fim"],
            periodo_parcial=metadata["periodo_parcial"],
            descricao_periodo=metadata["descricao_periodo"],
            snapshot_dados=snapshot,
            conteudo_estruturado=EMPTY_STRUCTURED_CONTENT,
            actor="admin@test",
        )
    else:
        report = repository.create(
            tipo=tipo,
            ano=2026,
            trimestre=metadata["trimestre"],
            data_inicio=metadata["data_inicio"],
            data_fim=metadata["data_fim"],
            periodo_parcial=metadata["periodo_parcial"],
            descricao_periodo=metadata["descricao_periodo"],
            snapshot_dados=snapshot,
            conteudo_estruturado=EMPTY_STRUCTURED_CONTENT,
            actor="admin@test",
        )
    if status == "EM_REVISAO":
        return repository.save_content(
            report["id"],
            EMPTY_STRUCTURED_CONTENT,
            "admin@test",
            status="EM_REVISAO",
        )
    if status == "RASCUNHO":
        return report
    report = repository.finalize(report["id"], "finalizador@test")
    if pdf:
        content, digest = _pdf(name or f"{tipo}-{report['versao']}")
        repository.save_pdf_artifact(
            report["id"],
            nome_arquivo=name or f"Relatorio_{tipo}_v{report['versao']}.pdf",
            conteudo=content,
            sha256=digest,
            tamanho=len(content),
            actor="admin@test",
        )
    return repository.get(report["id"])


def _send(store, report, transport, *, key="confirmacao-1", subject=None, body=None):
    return confirm_distribution(
        store,
        report,
        _admin(),
        subject=subject or default_subject(report),
        body=body or default_body(report),
        idempotency_key=key,
        transport=transport,
    )


def test_draft_and_review_cannot_be_sent(tmp_path):
    store = _store(tmp_path / "draft.db")
    _give_emails(store)
    draft = _create(store, status="RASCUNHO", pdf=False)
    review = _create(
        store, tipo="ANUAL", quarter=None, partial=True, status="EM_REVISAO", pdf=False
    )
    transport = FakeTransport()
    with pytest.raises(ValueError, match=DRAFT_MESSAGE):
        _send(store, draft, transport)
    with pytest.raises(ValueError, match=DRAFT_MESSAGE):
        _send(store, review, transport)
    assert transport.calls == []


def test_finalized_prepares_and_sent_version_can_be_distributed_again(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(
        distribution,
        "institutional_transport",
        lambda: (_ for _ in ()).throw(AssertionError("Gmail real")),
    )
    monkeypatch.setattr(
        "document_generator.institutional_report_pdf.generate_institutional_report_pdf",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("PDF regenerado")),
    )
    monkeypatch.setattr(
        "services.institutional_report_pdf.generate_institutional_report_pdf",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("PDF regenerado")),
    )
    store = _store(tmp_path / "ok.db")
    _give_emails(store)
    report = _create(store, name="Relatorio_Trimestral_MPCPB_2026_T3_v1.pdf")
    before = InstitutionalReportsStore(store).get(report["id"])
    artifact = InstitutionalReportsStore(store).pdf_artifact(report["id"])
    transport = FakeTransport()
    sent = _send(
        store, report, transport, subject="Assunto editado", body="Corpo editado."
    )
    updated = InstitutionalReportsStore(store).get(report["id"])
    assert sent["status_envio"] == "ENVIADO"
    assert updated["status"] == "ENVIADO"
    assert updated["finalizado_por"] == before["finalizado_por"] == "finalizador@test"
    assert updated["finalizado_em"] == before["finalizado_em"]
    assert updated["snapshot_dados"] == before["snapshot_dados"]
    assert updated["versao"] == before["versao"]
    assert len(transport.calls) == len(_people(store))
    assert {call["to"][0] for call in transport.calls} == {
        f"procurador{person['id']}@tce.pb.gov.br" for person in _people(store)
    }
    assert all(len(call["to"]) == 1 for call in transport.calls)
    assert all(call["subject"] == "Assunto editado" for call in transport.calls)
    assert all(call["text"] == "Corpo editado." for call in transport.calls)
    assert all(
        call["attachment"]["filename"] == "Relatorio_Trimestral_MPCPB_2026_T3_v1.pdf"
        for call in transport.calls
    )
    assert all(
        call["attachment"]["content"] == artifact["conteudo"]
        for call in transport.calls
    )
    history = list_report_distributions(store, report["id"])
    assert len(history) == 1
    assert history[0]["pdf_sha256"] == artifact["sha256"]
    assert history[0]["corpo"] == "Corpo editado."
    assert "conteudo" not in history[0]
    frozen = {item["email"] for item in history[0]["destinatarios"]}
    again = _send(store, updated, FakeTransport(), key="reenvio-1")
    assert again["id"] != history[0]["id"]
    assert again["pdf_sha256"] == artifact["sha256"]
    kept = list_report_distributions(store, report["id"])
    assert len(kept) == 2
    assert {item["id"] for item in kept} >= {history[0]["id"], again["id"]}
    original = next(item for item in kept if item["id"] == history[0]["id"])
    assert {item["email"] for item in original["destinatarios"]} == frozen
    assert InstitutionalReportsStore(store).get(report["id"])["status"] == "ENVIADO"
    parsed = message_from_bytes(
        base64.urlsafe_b64decode(_raw_message(transport.calls[0]))
    )
    attachment = next(part for part in parsed.walk() if part.get_filename())
    assert attachment.get_content_type() == "application/pdf"
    assert attachment.get_filename() == "Relatorio_Trimestral_MPCPB_2026_T3_v1.pdf"
    assert attachment.get_payload(decode=True) == artifact["conteudo"]
    assert SENDER_ADDRESS in parsed["From"]


def test_plain_institutional_message_stays_without_attachment():
    parsed = message_from_bytes(
        base64.urlsafe_b64decode(
            _raw_message(
                {
                    "to": ["pessoa@tce.pb.gov.br"],
                    "subject": "Protocolo",
                    "text": "Texto",
                }
            )
        )
    )
    assert parsed.get_content_type() == "text/plain"
    assert parsed.get_filename() is None


def test_hash_mismatch_invalid_signature_and_missing_pdf_block_send(tmp_path):
    store = _store(tmp_path / "hash.db")
    _give_emails(store)
    report = _create(store, name="oficial.pdf")
    transport = FakeTransport()
    with store.connection() as connection:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            "UPDATE relatorios_institucionais_pdf SET sha256=? WHERE relatorio_id=?",
            ("ab" * 32, report["id"]),
        )
    with pytest.raises(ValueError, match=HASH_MESSAGE):
        _send(store, report, transport)
    assert transport.calls == []
    events = _events(store)
    assert any("RELATORIO_DISTRIBUICAO_BLOQUEADA" in event for event in events)
    assert all("%PDF" not in (event or "") for event in events)

    other = _create(store, tipo="ANUAL", quarter=None, partial=True, name="anual.pdf")
    content = b"NAO-E-PDF"
    InstitutionalReportsStore(store).save_pdf_artifact(
        other["id"],
        nome_arquivo="anual.pdf",
        conteudo=content,
        sha256=hashlib.sha256(content).hexdigest(),
        tamanho=len(content),
        actor="admin@test",
    )
    with pytest.raises(ValueError, match=SIGNATURE_MESSAGE):
        _send(store, other, transport, key="assinatura")
    missing = _create(store, tipo="TRIMESTRAL", quarter=2, pdf=False)
    # quarter 2 needs its own period; _create uses snapshot months 7-9 but quarter argument
    with pytest.raises(ValueError, match=MISSING_PDF_MESSAGE):
        _send(store, missing, transport, key="sem-pdf")
    assert transport.calls == []


def test_selected_version_uses_its_own_official_pdf(tmp_path):
    store = _store(tmp_path / "versions.db")
    _give_emails(store)
    first = _create(store, name="Relatorio_Trimestral_MPCPB_2026_T3_v1.pdf")
    second = _create(store, name="Relatorio_Trimestral_MPCPB_2026_T3_v2.pdf")
    transport = FakeTransport()
    _send(store, second, transport, key="v2")
    assert {call["attachment"]["filename"] for call in transport.calls} == {
        "Relatorio_Trimestral_MPCPB_2026_T3_v2.pdf"
    }
    assert transport.calls[0]["attachment"]["content"] != _pdf("ignored")[0]
    stored = InstitutionalReportsStore(store).pdf_artifact(second["id"])
    assert transport.calls[0]["attachment"]["content"] == stored["conteudo"]
    assert InstitutionalReportsStore(store).get(first["id"])["status"] == "FINALIZADO"


def test_recipients_come_from_active_procuradores_and_bad_addresses_block(tmp_path):
    store = _store(tmp_path / "people.db")
    people = _people(store)
    assert len(people) == 7
    skipped = people[-1]
    _give_emails(store, skip_ids={skipped["id"]})
    report = _create(store)
    transport = FakeTransport()
    with pytest.raises(ValueError, match=MISSING_EMAIL_PREFIX) as missing:
        _send(store, report, transport)
    assert skipped["nome"] in str(missing.value)
    assert transport.calls == []

    duplicate = _store(tmp_path / "duplicate.db")
    seeded = _people(duplicate)
    _give_emails(
        duplicate,
        email_for=lambda person: (
            "repetido@tce.pb.gov.br"
            if person["id"] in {seeded[0]["id"], seeded[1]["id"]}
            else f"procurador{person['id']}@tce.pb.gov.br"
        ),
    )
    blocked = _create(duplicate)
    with pytest.raises(ValueError, match=DUPLICATE_EMAIL_PREFIX) as repeated:
        _send(duplicate, blocked, transport, key="repetido")
    assert seeded[0]["nome"] in str(repeated.value)
    assert seeded[1]["nome"] in str(repeated.value)
    assert transport.calls == []

    normalized = _store(tmp_path / "normalized.db")
    _give_emails(
        normalized,
        email_for=lambda person: f"  Procurador{person['id']}@TCE.pb.gov.br  ",
    )
    ready = assess_recipients(normalized)
    assert ready["bloqueios"] == []
    assert {item["email"] for item in ready["destinatarios"]} == {
        f"procurador{person['id']}@tce.pb.gov.br" for person in _people(normalized)
    }
    notes = NotificationsStore(normalized)
    notes.save_recipient(
        SUBSCRIPTION_PROTOCOLO, "SERVIDOR", 999, "servidor@tce.pb.gov.br", True
    )
    inactive = _people(normalized)[0]
    with normalized.connection() as connection:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            "UPDATE procuradores SET ativo=0 WHERE id=?", (inactive["id"],)
        )
    remaining = assess_recipients(normalized)
    assert all(
        item["procurador_id"] != inactive["id"] for item in remaining["destinatarios"]
    )
    assert all(item["email"] != "servidor@tce.pb.gov.br" for item in remaining["lista"])
    assert len(remaining["destinatarios"]) == 6


def test_default_subject_and_body_follow_the_frozen_period():
    quarterly = {"snapshot_dados": _snapshot()}
    partial = {"snapshot_dados": _snapshot(tipo="ANUAL", quarter=None, partial=True)}
    august = {
        "snapshot_dados": _snapshot(
            tipo="ANUAL", quarter=None, months=list(range(1, 9)), partial=True
        )
    }
    complete = {
        "snapshot_dados": _snapshot(
            tipo="ANUAL",
            quarter=None,
            months=list(range(1, 13)),
            partial=False,
        )
    }
    assert default_subject(quarterly) == (
        "Relatório Trimestral de Produção do MPC-PB — 3º Trimestre de 2026"
    )
    assert default_subject(partial) == (
        "Relatório Anual de Produção do MPC-PB — Acumulado de janeiro a setembro de 2026"
    )
    assert "agosto" in default_subject(august)
    assert "setembro" not in default_subject(august)
    assert default_subject(complete) == "Relatório Anual de Produção do MPC-PB — 2026"
    body = default_body(quarterly)
    assert body.startswith("Prezadas(os) Procuradoras(es),")
    assert "3º trimestre de 2026" in body
    assert "O documento segue anexo." in body
    assert body.endswith("Ministério Público de Contas da Paraíba")
    assert "referente ao acumulado de janeiro a setembro de 2026" in default_body(
        partial
    )
    assert "referente ao ano de 2026" in default_body(complete)


def test_reader_cannot_send_and_metadata_listing_omits_bytes(tmp_path):
    store = _store(tmp_path / "reader.db")
    _give_emails(store)
    report = _create(store)
    with pytest.raises(ValueError, match=PERMISSION_MESSAGE):
        confirm_distribution(
            store,
            report,
            _reader(),
            subject="Assunto",
            body="Corpo",
            idempotency_key="leitor",
            transport=FakeTransport(),
        )
    metadata = official_pdf_metadata(store, report["id"])
    assert "conteudo" not in metadata
    assert metadata["nome_arquivo"].endswith(".pdf")
    assert metadata["sha256"]


def test_total_failure_keeps_the_report_finalized_and_partial_retries_only_failures(
    tmp_path,
):
    store = _store(tmp_path / "partial.db")
    _give_emails(store)
    report = _create(store)
    emails = [item["email"] for item in assess_recipients(store)["destinatarios"]]
    failed = emails[-1]
    transport = FakeTransport()
    original_send = transport.send

    def send(message):
        original_send(message)
        if message["to"] == [failed]:
            raise DeliveryRejected("recusado")
        return "ok-" + message["to"][0]

    transport.send = send
    result = _send(store, report, transport, key="parcial-1")
    assert result["status_envio"] == "PARCIAL"
    assert InstitutionalReportsStore(store).get(report["id"])["status"] == "FINALIZADO"
    sent_ok = {call["to"][0] for call in transport.calls if call["to"] != [failed]}
    assert failed not in sent_ok
    retry_transport = FakeTransport()
    retried = retry_distribution(
        store,
        report,
        _admin(),
        result["id"],
        subject="Assunto",
        body="Corpo",
        idempotency_key="retomada-1",
        transport=retry_transport,
    )
    assert [call["to"] for call in retry_transport.calls] == [[failed]]
    assert retried["status_envio"] == "ENVIADO"
    assert InstitutionalReportsStore(store).get(report["id"])["status"] == "ENVIADO"
    previous = next(
        item
        for item in list_report_distributions(store, report["id"])
        if item["id"] == result["id"]
    )
    assert previous["status_envio"] == "PARCIAL"
    assert all(
        person["status"] == "ENVIADO"
        for person in previous["destinatarios"]
        if person["email"] != failed
    )

    failed_store = _store(tmp_path / "failure.db")
    _give_emails(failed_store)
    failed_report = _create(failed_store)
    boom = FakeTransport()

    def reject(message):
        boom.calls.append(message)
        raise DeliveryRejected("recusado")

    boom.send = reject
    outcome = _send(failed_store, failed_report, boom, key="falha-total")
    assert outcome["status_envio"] == "FALHA"
    assert (
        InstitutionalReportsStore(failed_store).get(failed_report["id"])["status"]
        == "FINALIZADO"
    )


def test_uncertain_delivery_is_not_success_and_retry_skips_confirmed_recipients(
    tmp_path,
):
    store = _store(tmp_path / "uncertain.db")
    _give_emails(store)
    report = _create(store)
    emails = [item["email"] for item in assess_recipients(store)["destinatarios"]]
    transport = FakeTransport()

    def send(message):
        transport.calls.append(message)
        if message["to"] == [emails[1]]:
            raise DeliveryUncertain("timeout")
        return "ok-" + message["to"][0]

    transport.send = send
    result = _send(store, report, transport, key="incerto")
    assert result["status_envio"] == "ENVIANDO"
    assert UNCERTAIN_MESSAGE in (result["erro_resumo"] or "")
    assert [call["to"][0] for call in transport.calls] == emails[:2]
    assert InstitutionalReportsStore(store).get(report["id"])["status"] == "FINALIZADO"
    retry = FakeTransport()
    resumed = retry_distribution(
        store,
        report,
        _admin(),
        result["id"],
        subject="Assunto",
        body="Corpo",
        idempotency_key="retomada-incerta",
        transport=retry,
    )
    assert [call["to"][0] for call in retry.calls] == emails[1:]
    assert emails[0] not in {call["to"][0] for call in retry.calls}
    assert resumed["status_envio"] == "ENVIADO"
    assert InstitutionalReportsStore(store).get(report["id"])["status"] == "ENVIADO"


def test_same_confirmation_cannot_send_twice_and_a_later_one_can(tmp_path):
    store = _store(tmp_path / "once.db")
    _give_emails(store)
    report = _create(store)
    transport = FakeTransport()
    first = _send(store, report, transport, key="mesma-confirmacao")
    second = _send(store, report, transport, key="mesma-confirmacao")
    assert second["id"] == first["id"]
    assert len(transport.calls) == len(_people(store))
    assert len(list_report_distributions(store, report["id"])) == 1
    later = _send(store, report, transport, key="confirmacao-posterior")
    assert later["id"] != first["id"]
    assert len(transport.calls) == len(_people(store)) * 2
    assert len(list_report_distributions(store, report["id"])) == 2


def test_oversized_pdf_is_blocked_before_the_provider(tmp_path, monkeypatch):
    monkeypatch.setattr(distribution, "MAX_PDF_BYTES", 16)
    store = _store(tmp_path / "size.db")
    _give_emails(store)
    report = _create(store, pdf=False)
    content = b"%PDF-1.4\n" + b"x" * 32
    InstitutionalReportsStore(store).save_pdf_artifact(
        report["id"],
        nome_arquivo="grande.pdf",
        conteudo=content,
        sha256=hashlib.sha256(content).hexdigest(),
        tamanho=len(content),
        actor="admin@test",
    )
    transport = FakeTransport()
    with pytest.raises(ValueError, match="limite de anexo"):
        _send(store, report, transport)
    assert transport.calls == []


def test_changed_official_pdf_blocks_a_new_distribution(tmp_path):
    store = _store(tmp_path / "changed.db")
    _give_emails(store)
    report = _create(store, name="original.pdf")
    _send(store, report, FakeTransport(), key="primeiro")
    replacement = b"%PDF-1.4\nsubstituido\n"
    InstitutionalReportsStore(store).save_pdf_artifact(
        report["id"],
        nome_arquivo="original.pdf",
        conteudo=replacement,
        sha256=hashlib.sha256(replacement).hexdigest(),
        tamanho=len(replacement),
        actor="admin@test",
    )
    transport = FakeTransport()
    with pytest.raises(ValueError, match=CHANGED_PDF_MESSAGE):
        _send(store, report, transport, key="depois-da-troca")
    assert transport.calls == []


def test_distribution_tables_are_created_without_replacing_the_report(tmp_path):
    store = _store(tmp_path / "schema.db")
    InstitutionalReportsStore(store)
    with store.connection(read_only=True) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
    assert "relatorios_institucionais_envios" in tables
    assert "relatorios_institucionais_envio_destinatarios" in tables
    reports_migration = " ".join(INSTITUTIONAL_REPORTS_MIGRATION_SQL)
    distribution_migration = " ".join(INSTITUTIONAL_DISTRIBUTION_MIGRATION_SQL)
    assert "relatorios_institucionais_envios" not in reports_migration
    assert "relatorios_institucionais_envios" in distribution_migration
    assert "relatorios_institucionais_envio_destinatarios" in distribution_migration
    assert "CREATE TABLE IF NOT EXISTS" in distribution_migration
    assert "VALUES(4)" in inspect.getsource(
        PostgresBackend._migrate_institutional_distribution
    )
    initialize = inspect.getsource(PostgresBackend._initialize)
    assert "[1, 2, 3, 4]" in initialize
    assert initialize.index("_migrate_institutional_reports") < initialize.index(
        "_migrate_institutional_distribution"
    )
    assert "relatorios_institucionais_envios" in TABLES
    assert "generate_institutional_report_pdf" not in inspect.getsource(distribution)
    assert "gemini" not in inspect.getsource(distribution).lower()


def _events(store):
    with store.connection(read_only=True) as connection:
        rows = connection.execute(
            "SELECT evento, detalhes_json FROM auditoria_eventos"
        ).fetchall()
    return [f"{row['evento']} {row['detalhes_json'] or ''}" for row in rows]


def _distribution_page():
    import os

    from database.store import Store
    from services.access import Principal
    from services.relatorios_ui import institutional_reports

    store = Store(os.environ["MPC_DISTRIBUTION_DB"])
    if os.environ.get("MPC_DISTRIBUTION_ROLE") == "leitor":
        principal = Principal(
            2, "Leitor", "leitor@test", "CONSULTA", True, False, False, False, False, ()
        )
    else:
        principal = Principal(
            1,
            "André",
            "admin@test",
            "ADMINISTRADOR",
            True,
            True,
            True,
            True,
            True,
            (),
        )
    institutional_reports(store, principal)


def _open_distribution(monkeypatch, database, transport):
    monkeypatch.setenv("MPC_DISTRIBUTION_DB", str(database))
    monkeypatch.setattr(
        relatorios_ui.TramitaReportsStore, "historical_years", lambda self: [2026]
    )
    monkeypatch.setattr(
        relatorios_ui.TramitaReportsStore,
        "months_for_year",
        lambda self, year: [7, 8, 9],
    )
    monkeypatch.setattr(distribution, "institutional_transport", lambda: transport)
    monkeypatch.setattr(
        "document_generator.institutional_report_pdf.generate_institutional_report_pdf",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("PDF regenerado")),
    )
    report = next(
        item
        for item in InstitutionalReportsStore(Store(database)).list_latest_versions()
        if item["tipo"] == "TRIMESTRAL"
    )
    app = AppTest.from_function(_distribution_page, default_timeout=90).run()
    assert not app.exception, app.exception
    app.button(key=f"inst_open_created_{report['id']}").click().run()
    assert not app.exception, app.exception
    app.radio(key="inst_exibicao").set_value("Distribuição").run()
    assert not app.exception, app.exception
    return app


def test_distribution_is_available_from_the_institutional_report_detail(
    tmp_path, monkeypatch
):
    store = _store(tmp_path / "distribution-detail.db")
    _give_emails(store)
    _create(store)
    app = _open_distribution(monkeypatch, store.path, FakeTransport())

    assert app.radio(key="inst_exibicao").value == "Distribuição"
    assert "Distribuição" in app.radio(key="inst_exibicao").options
    assert any(item.label == "Baixar PDF" for item in app.get("download_button"))
    assert not any(item.label == "Baixar PDF" for item in app.button)


def _visible(app):
    parts = []
    for collection_name in (
        "markdown",
        "caption",
        "warning",
        "error",
        "info",
        "success",
        "text",
    ):
        collection = getattr(app, collection_name, [])
        parts.extend(str(item.value) for item in collection)
    parts.extend(item.label for item in app.button)
    return "\n".join(parts)


def _button(app, prefix):
    matches = [item for item in app.button if (item.key or "").startswith(prefix)]
    assert matches, [item.key for item in app.button]
    return matches[0]


def test_distribution_tab_requires_confirmation_and_a_later_rerun_does_not_send(
    tmp_path, monkeypatch
):
    store = _store(tmp_path / "ui.db")
    _give_emails(store)
    first = _create(store, name="Relatorio_Trimestral_MPCPB_2026_T3_v1.pdf")
    second = _create(store, name="Relatorio_Trimestral_MPCPB_2026_T3_v2.pdf")
    annual = _create(
        store,
        tipo="ANUAL",
        quarter=None,
        partial=True,
        name="Relatorio_Anual_MPCPB_2026_v1.pdf",
    )
    transport = FakeTransport()
    app = _open_distribution(monkeypatch, store.path, transport)
    visible = _visible(app)
    assert "Distribuição" in app.radio(key="inst_exibicao").options
    assert "Relatorio_Trimestral_MPCPB_2026_T3_v2.pdf" in visible
    assert "mpc@tce.pb.gov.br" in visible
    assert "Histórico de distribuição" in visible
    assert app.text_input(
        key=f"institutional_distribution_subject_{second['id']}"
    ).value == default_subject(second)
    assert app.text_area(
        key=f"institutional_distribution_body_{second['id']}"
    ).value.startswith("Prezadas(os) Procuradoras(es),")
    for person in _people(store):
        assert person["nome"] in visible
        assert f"procurador{person['id']}@tce.pb.gov.br" in visible
    assert transport.calls == []

    app = _button(app, "institutional_distribution_open_").click().run()
    assert not app.exception, app.exception
    assert (
        "Confirmar envio deste relatório para todos os Procuradores ativos?"
        in _visible(app)
    )
    assert transport.calls == []
    app.radio(key="inst_exibicao").set_value("Visualização").run()
    app.selectbox(key="inst_version_history").set_value("1 - Finalizado").run()
    app.button(key=f"inst_open_version_{first['id']}").click().run()
    app.radio(key="inst_exibicao").set_value("Distribuição").run()
    assert (
        "Confirmar envio deste relatório para todos os Procuradores ativos?"
        not in _visible(app)
    )
    assert "Relatorio_Trimestral_MPCPB_2026_T3_v1.pdf" in _visible(app)
    assert transport.calls == []

    app.radio(key="inst_exibicao").set_value("Visualização").run()
    app.selectbox(key="inst_version_history").set_value("2 - Finalizado").run()
    app.button(key=f"inst_open_version_{second['id']}").click().run()
    app.radio(key="inst_exibicao").set_value("Distribuição").run()
    subject_box = next(
        item
        for item in app.text_input
        if (item.key or "").startswith("institutional_distribution_subject_")
    )
    body_box = next(
        item
        for item in app.text_area
        if (item.key or "").startswith("institutional_distribution_body_")
    )
    assert subject_box.key != body_box.key
    app = subject_box.set_value("Assunto editado na prévia").run()
    app = (
        next(
            item
            for item in app.text_area
            if (item.key or "").startswith("institutional_distribution_body_")
        )
        .set_value("Corpo editado na prévia.")
        .run()
    )
    assert (
        app.text_input(key=f"institutional_distribution_subject_{second['id']}").value
        == "Assunto editado na prévia"
    )
    app = _button(app, "institutional_distribution_open_").click().run()
    assert transport.calls == []
    app = _button(app, "institutional_distribution_cancel_").click().run()
    assert transport.calls == []
    assert "Confirmar envio deste relatório" not in _visible(app)

    app = _button(app, "institutional_distribution_open_").click().run()
    app = _button(app, "institutional_distribution_confirm_button_").click().run()
    assert not app.exception, app.exception
    assert len(transport.calls) == 7
    assert {call["subject"] for call in transport.calls} == {
        "Assunto editado na prévia"
    }
    assert {call["text"] for call in transport.calls} == {"Corpo editado na prévia."}
    assert {call["attachment"]["filename"] for call in transport.calls} == {
        "Relatorio_Trimestral_MPCPB_2026_T3_v2.pdf"
    }
    app.run()
    assert len(transport.calls) == 7
    visible = _visible(app)
    assert "Histórico de distribuição" in visible
    assert "Relatorio_Trimestral_MPCPB_2026_T3_v2.pdf" in visible
    assert "Detalhe do envio" in " ".join(item.label for item in app.expander)
    saved = InstitutionalReportsStore(store).get(second["id"])
    assert saved["status"] == "ENVIADO"
    assert saved["finalizado_por"] == "finalizador@test"
    app = _button(app, "institutional_distribution_open_").click().run()
    assert "Esta versão já foi distribuída anteriormente." in _visible(app)
    assert (
        "Confirmar envio deste relatório para todos os Procuradores ativos?"
        in _visible(app)
    )
    assert len(transport.calls) == 7
    app = _button(app, "institutional_distribution_cancel_").click().run()
    assert len(transport.calls) == 7

    app.button(key="inst_back_to_created").click().run()
    app.button(key=f"inst_open_created_{annual['id']}").click().run()
    app.radio(key="inst_exibicao").set_value("Distribuição").run()
    annual_subject = app.text_input(
        key=f"institutional_distribution_subject_{annual['id']}"
    ).value
    assert annual_subject == default_subject(annual)
    assert "setembro" in annual_subject
    assert "setembro" in _visible(app)
    app = _button(app, "institutional_distribution_open_").click().run()
    assert transport.calls and len(transport.calls) == 7
    app = _button(app, "institutional_distribution_cancel_").click().run()
    assert len(transport.calls) == 7


def test_reader_sees_history_without_a_send_button(tmp_path, monkeypatch):
    store = _store(tmp_path / "reader-ui.db")
    _give_emails(store)
    report = _create(store, name="Relatorio_Trimestral_MPCPB_2026_T3_v1.pdf")
    _send(store, report, FakeTransport(), key="ja-enviado")
    monkeypatch.setenv("MPC_DISTRIBUTION_ROLE", "leitor")
    transport = FakeTransport()
    app = _open_distribution(monkeypatch, store.path, transport)
    visible = _visible(app)
    assert "Relatorio_Trimestral_MPCPB_2026_T3_v1.pdf" in visible
    assert "Histórico de distribuição" in visible
    assert "Enviar aos Procuradores" not in visible
    assert "Preparar reenvio" not in visible
    assert "Confirmar envio" not in visible
    assert transport.calls == []


def test_draft_screen_explains_that_finalization_is_required(tmp_path, monkeypatch):
    store = _store(tmp_path / "draft-ui.db")
    _give_emails(store)
    _create(store, status="RASCUNHO", pdf=False)
    transport = FakeTransport()
    app = _open_distribution(monkeypatch, store.path, transport)
    visible = _visible(app)
    assert DRAFT_MESSAGE in visible
    assert "Enviar aos Procuradores" not in visible
    assert transport.calls == []
    content = app.radio(key="inst_exibicao").set_value("Conteúdo e revisão").run()
    content_keys = {item.key for item in content.text_area}
    distribution = content.radio(key="inst_exibicao").set_value("Distribuição").run()
    distribution_keys = {
        item.key for item in (*distribution.text_input, *distribution.text_area)
    }
    assert content_keys
    assert all(key.startswith("inst_text_") for key in content_keys)
    assert content_keys.isdisjoint(distribution_keys)
    assert "Enviar aos Procuradores" not in _visible(distribution)


def test_finalized_screen_without_pdf_directs_to_content_review(tmp_path, monkeypatch):
    store = _store(tmp_path / "missing-pdf-ui.db")
    _give_emails(store)
    report = _create(store, pdf=False)
    app = _open_distribution(monkeypatch, store.path, FakeTransport())

    visible = _visible(app)
    assert "Gere-o em “Conteúdo e revisão”" in visible
    assert not any(
        button.key == f"institutional_distribution_generate_{report['id']}"
        for button in app.button
    )
    assert "Gerar PDF oficial" not in visible
