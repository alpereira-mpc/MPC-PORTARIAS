import json

import pytest

from services.minutas_storage import (
    StorageConfigurationError,
    SupabaseMinutasStorage,
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
