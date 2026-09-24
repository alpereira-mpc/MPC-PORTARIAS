from datetime import datetime
from io import BytesIO

from pypdf import PdfReader

from document_generator.agenda_pdf import generate_agenda_pdf
from services.agenda_ui import agenda_pdf_filename, all_upcoming


def _text(content):
    return "\n".join(page.extract_text() or "" for page in PdfReader(BytesIO(content)).pages)


def test_pdf_contains_commitments_and_leaves_without_technical_ids():
    records = [
        {
            "id": "internal-commitment-id",
            "inicio": "2026-09-24T14:30:00",
            "fim": None,
            "tipo": "EVENTO",
            "titulo": "Reuniao institucional",
            "sem_hora": False,
            "situacao": "Agendado",
            "procuradores": [1],
            "local": "Sede",
        },
        {
            "id": "internal-leave-id",
            "inicio": "2026-09-25T00:00:00",
            "data_inicio": "2026-09-25",
            "data_fim": "2026-10-02",
            "afastamento": True,
            "procurador_id": 2,
            "motivo": "Ferias",
            "status": "AGENDADO",
        },
    ]

    content = generate_agenda_pdf(
        records,
        {1: "Dra. Elvira", 2: "Dr. Bradson"},
        ["Periodo: 24/09/2026 a 02/10/2026"],
        generated_at=datetime(2026, 9, 24, 16, 5),
    )
    text = _text(content)

    assert content.startswith(b"%PDF-")
    assert "COMPROMISSO" in text and "AFASTAMENTO" in text
    assert "Dra. Elvira" in text and "Dr. Bradson" in text
    assert "24/09/2026" in text
    assert "25/09/2026 a 02/10/2026" in text
    assert "14h30" in text
    assert "internal-commitment-id" not in text
    assert "internal-leave-id" not in text


def test_empty_pdf_is_valid_and_filename_uses_brazilian_period():
    content = generate_agenda_pdf([], {}, ["Nenhum registro"])

    assert len(PdfReader(BytesIO(content)).pages) == 1
    assert agenda_pdf_filename(
        datetime(2026, 10, 1).date(), datetime(2026, 10, 31).date()
    ) == "Agenda_MPC-PB_01-10-2026_a_31-10-2026.pdf"


def test_all_upcoming_uses_every_filtered_page():
    class Agenda:
        def __init__(self):
            self.calls = []

        def active_leaves(self, start, end, member, *, upcoming, limit, offset):
            self.calls.append((start, member, upcoming, limit, offset))
            count = 31 if offset == 0 else 2
            return [{"id": f"leave-{offset}-{index}"} for index in range(count)]

    agenda = Agenda()
    calls = []

    def paged_records(_agenda, start, end, member, kind, status, *, offset, active):
        calls.append((start, member, kind, status, offset, active))
        count = 31 if offset == 0 else 3
        return [{"id": f"appointment-{offset}-{index}"} for index in range(count)]

    from services import agenda_ui

    original = agenda_ui.records
    agenda_ui.records = paged_records
    try:
        appointments, leaves = all_upcoming(
            agenda,
            "2026-09-24",
            7,
            "EVENTO",
            "Agendado",
            include_appointments=True,
            include_leaves=True,
        )
    finally:
        agenda_ui.records = original

    assert len(appointments) == 33
    assert len(leaves) == 32
    assert [call[4] for call in calls] == [0, 30]
    assert [call[4] for call in agenda.calls] == [0, 30]
    assert all(call[1:4] == (7, "EVENTO", "Agendado") for call in calls)
