"""Private Supabase Storage adapter for Minutas IA documents."""

import json
import re
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlparse
from urllib.request import Request, urlopen


DEFAULT_BUCKET = "minutas-referencias"


class StorageConfigurationError(RuntimeError):
    pass


class StorageOperationError(RuntimeError):
    pass


def _secret(secrets, name):
    try:
        value = secrets.get(name)
    except Exception:
        value = None
    return value.strip() if isinstance(value, str) else ""


class SupabaseMinutasStorage:
    def __init__(self, url, service_role_key, bucket=DEFAULT_BUCKET, opener=urlopen):
        parsed = urlparse(str(url or "").strip())
        if parsed.scheme != "https" or not parsed.netloc:
            raise StorageConfigurationError("Storage privado não configurado.")
        if not isinstance(service_role_key, str) or not service_role_key.strip():
            raise StorageConfigurationError("Storage privado não configurado.")
        if not re.fullmatch(r"[a-z0-9][a-z0-9.-]{2,62}", str(bucket or "")):
            raise StorageConfigurationError("Storage privado não configurado.")
        self.url = url.rstrip("/")
        self.key = service_role_key.strip()
        self._uses_secret_key = self.key.startswith("sb_secret_")
        self.bucket = bucket
        self._opener = opener

    @classmethod
    def from_secrets(cls, secrets):
        secret_key = _secret(secrets, "SUPABASE_SECRET_KEY") or _secret(
            secrets, "SUPABASE_SERVICE_ROLE_KEY"
        )
        return cls(
            _secret(secrets, "SUPABASE_URL"),
            secret_key,
            _secret(secrets, "MINUTAS_STORAGE_BUCKET") or DEFAULT_BUCKET,
        )

    def _request(self, method, endpoint, *, data=None, content_type=None, headers=None):
        request_headers = {"apikey": self.key}
        if not self._uses_secret_key:
            request_headers["Authorization"] = "Bearer " + self.key
        if content_type:
            request_headers["Content-Type"] = content_type
        request_headers.update(headers or {})
        request = Request(
            self.url + "/storage/v1" + endpoint,
            data=data,
            headers=request_headers,
            method=method,
        )
        try:
            with self._opener(request, timeout=30) as response:
                return response.read()
        except (HTTPError, URLError, TimeoutError) as exc:
            raise StorageOperationError("Falha no armazenamento privado.") from exc

    def upload(self, path, content, mime):
        endpoint = "/object/" + quote(self.bucket + "/" + path, safe="/")
        self._request(
            "POST",
            endpoint,
            data=content,
            content_type=mime,
            headers={"x-upsert": "false"},
        )

    def download(self, path):
        endpoint = "/object/" + quote(self.bucket + "/" + path, safe="/")
        return self._request("GET", endpoint)

    def delete(self, path):
        payload = json.dumps({"prefixes": [path]}).encode("utf-8")
        endpoint = "/object/" + quote(self.bucket, safe="")
        self._request(
            "DELETE", endpoint, data=payload, content_type="application/json"
        )
