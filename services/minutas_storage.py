"""Private Supabase Storage adapter for Minutas IA documents."""

import json
import re
import uuid
from pathlib import PurePath
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlparse
from urllib.request import Request, urlopen


DEFAULT_BUCKET = "minutas-referencias"
ALLOWED_OBJECT_SUFFIXES = {".pdf", ".docx"}


class StorageConfigurationError(RuntimeError):
    pass


class StorageOperationError(RuntimeError):
    pass


_HTTP_CATEGORIES = {
    400: "solicitação rejeitada",
    401: "autorização",
    403: "autorização",
    404: "objeto não encontrado",
    409: "conflito",
    412: "pré-condição",
    413: "tamanho",
    429: "limite",
}


def acervo_object_path(name):
    """Caminho físico novo: só UUID e extensão. O nome original fica nos metadados."""
    suffix = PurePath(str(name or "")).suffix.lower()
    if suffix not in ALLOWED_OBJECT_SUFFIXES:
        raise StorageOperationError("Extensão não permitida no armazenamento.")
    return f"acervo/{uuid.uuid4().hex}/{uuid.uuid4().hex}{suffix}"


def modelo_object_path(name):
    """Modelo visual novo, fora do acervo de processos. O nome original fica nos metadados."""
    suffix = PurePath(str(name or "")).suffix.lower()
    if suffix != ".docx":
        raise StorageOperationError("O modelo visual aceita apenas DOCX.")
    return f"modelos/{uuid.uuid4().hex}/{uuid.uuid4().hex}.docx"


def _http_detail(payload):
    if not isinstance(payload, dict):
        return ""
    for key in ("error", "errorCode", "statusCode"):
        value = payload.get(key)
        if isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", value):
            return ", " + value
        if isinstance(value, int) and 100 <= value <= 599:
            return f", {value}"
    return ""


def _operation_error(exc):
    if isinstance(exc, HTTPError):
        code = int(exc.code or 0)
        category = "serviço" if code >= 500 else _HTTP_CATEGORIES.get(code, "http")
        detail = ""
        try:
            body = exc.read() or b""
            detail = _http_detail(json.loads(body.decode("utf-8", errors="replace")))
        except Exception:
            detail = ""
        return StorageOperationError(
            f"Falha no armazenamento privado (HTTP {code}, {category}{detail})."
        )
    if isinstance(exc, TimeoutError):
        return StorageOperationError("Falha no armazenamento privado (rede, tempo esgotado).")
        return StorageOperationError("Falha no armazenamento privado (rede).")


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
            raise _operation_error(exc) from exc

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
