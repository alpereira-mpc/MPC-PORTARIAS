from email.message import Message
from io import BytesIO
import json
from urllib.error import HTTPError

import pytest

from services.minutas_storage import (
    StorageConfigurationError,
    StorageOperationError,
    SupabaseMinutasStorage,
    acervo_object_path,
)


class Response:
    def __init__(self, content=b"ok"):
        self.content = content

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def read(self):
        return self.content


@pytest.mark.parametrize(
    "key,expected_authorization",
    (
        ("sb_secret_current", None),
        (
            "eyJhbGciOiJIUzI1NiJ9.legacy.signature",
            "Bearer eyJhbGciOiJIUzI1NiJ9.legacy.signature",
        ),
    ),
)
def test_storage_uses_key_specific_headers_on_private_endpoints(
    key, expected_authorization
):
    requests = []

    def opener(request, timeout):
        requests.append((request, timeout))
        return Response(b"document")

    storage = SupabaseMinutasStorage(
        "https://project.supabase.co", key, opener=opener
    )
    storage.upload("referencias/id/audit/file.pdf", b"%PDF", "application/pdf")
    assert storage.download("referencias/id/audit/file.pdf") == b"document"
    storage.delete("referencias/id/audit/file.pdf")

    upload, download, delete = [item[0] for item in requests]
    assert upload.method == "POST"
    assert download.method == "GET"
    assert delete.method == "DELETE"
    for request in (upload, download, delete):
        assert request.get_header("Apikey") == key
        assert request.get_header("Authorization") == expected_authorization
    assert upload.get_header("X-upsert") == "false"
    assert "/storage/v1/object/minutas-referencias/referencias/" in upload.full_url
    assert json.loads(delete.data) == {"prefixes": ["referencias/id/audit/file.pdf"]}
    assert all(timeout == 30 for _, timeout in requests)


def test_storage_configuration_prefers_current_secret_key():
    storage = SupabaseMinutasStorage.from_secrets(
        {
            "SUPABASE_URL": "https://project.supabase.co",
            "SUPABASE_SECRET_KEY": "sb_secret_current",
            "SUPABASE_SERVICE_ROLE_KEY": "eyJ.legacy.signature",
        }
    )

    assert storage.key == "sb_secret_current"


@pytest.mark.parametrize(
    "url,key,bucket",
    (
        ("", "secret", "minutas-referencias"),
        ("http://project.supabase.co", "secret", "minutas-referencias"),
        ("https://project.supabase.co", "", "minutas-referencias"),
        ("https://project.supabase.co", "secret", "../invalid"),
    ),
)
def test_invalid_storage_configuration_fails_closed(url, key, bucket):
    with pytest.raises(StorageConfigurationError):
        SupabaseMinutasStorage(url, key, bucket)


def test_new_acervo_object_path_uses_uuid_and_keeps_only_the_extension():
    path = acervo_object_path("proc_01653_26_parecer - instituto taperoá.pdf")
    assert path.startswith("acervo/")
    assert path.endswith(".pdf")
    assert " " not in path and "á" not in path and "taperoá" not in path
    folder, name = path.split("/")[1:]
    assert len(folder) == 32 and folder.isalnum()
    assert len(name) == 36 and name[:-4].isalnum()
    assert acervo_object_path("modelo decisão.docx").endswith(".docx")
    with pytest.raises(StorageOperationError, match="Extensão"):
        acervo_object_path("arquivo.exe")


def test_storage_http_error_reports_code_and_category_without_secrets():
    def opener(request, timeout):
        raise HTTPError(
            request.full_url,
            400,
            "Bad Request",
            Message(),
            BytesIO(b'{"statusCode":"400","error":"InvalidKey","message":"secret=sb_secret_x"}'),
        )

    storage = SupabaseMinutasStorage(
        "https://project.supabase.co", "sb_secret_current", opener=opener
    )
    with pytest.raises(StorageOperationError, match=r"HTTP 400, solicitação rejeitada, InvalidKey") as caught:
        storage.upload("acervo/abc/def.pdf", b"%PDF", "application/pdf")
    assert "sb_secret" not in str(caught.value)
    assert "Bearer" not in str(caught.value)
