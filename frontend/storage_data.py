from __future__ import annotations

import gzip
import io
import json
import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

import requests


INDEX_OBJECT = "index.json.gz"
RAW_PAGES_OBJECT = "raw_pages.json.gz"
DOWNLOAD_TIMEOUT = (10, 120)


class StorageDataError(RuntimeError):
    pass


@dataclass(frozen=True)
class StorageSettings:
    url: str
    secret_key: str
    bucket: str

    @property
    def configured(self) -> bool:
        return bool(self.url and self.secret_key and self.bucket)


def read_storage_settings(secrets: object | None = None) -> StorageSettings:
    values = {
        "SUPABASE_URL": os.getenv("SUPABASE_URL", "").strip(),
        "SUPABASE_SECRET_KEY": os.getenv("SUPABASE_SECRET_KEY", "").strip(),
        "SUPABASE_BUCKET": os.getenv("SUPABASE_BUCKET", "").strip(),
    }

    if secrets is not None:
        try:
            for key in values:
                values[key] = values[key] or str(secrets.get(key, "")).strip()
        except Exception:
            pass

    return StorageSettings(
        url=values["SUPABASE_URL"].rstrip("/"),
        secret_key=values["SUPABASE_SECRET_KEY"],
        bucket=values["SUPABASE_BUCKET"],
    )


def _read_json_stream(stream: io.BufferedIOBase, compressed: bool) -> dict:
    binary_stream = gzip.GzipFile(fileobj=stream) if compressed else stream
    with io.TextIOWrapper(binary_stream, encoding="utf-8") as text_stream:
        payload = json.load(text_stream)
    if not isinstance(payload, dict):
        raise StorageDataError("Stored JSON must contain a top-level object.")
    return payload


def _read_local_json(path: Path) -> dict:
    compressed = path.suffix == ".gz"
    with path.open("rb") as handle:
        return _read_json_stream(handle, compressed)


def _download_json(settings: StorageSettings, object_name: str) -> dict:
    bucket = quote(settings.bucket, safe="")
    object_path = quote(object_name, safe="/")
    url = f"{settings.url}/storage/v1/object/authenticated/{bucket}/{object_path}"
    headers = {
        "apikey": settings.secret_key,
        "Authorization": f"Bearer {settings.secret_key}",
        "User-Agent": "MSE-Tuebingen-Search/1.0",
    }

    try:
        response = requests.get(url, headers=headers, timeout=DOWNLOAD_TIMEOUT)
    except requests.RequestException as exc:
        raise StorageDataError(f"Could not reach Supabase Storage for {object_name}.") from exc

    if response.status_code != 200:
        raise StorageDataError(
            f"Supabase Storage could not load {object_name} (HTTP {response.status_code})."
        )

    try:
        return _read_json_stream(io.BytesIO(response.content), object_name.endswith(".gz"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise StorageDataError(f"Stored object {object_name} is not valid JSON data.") from exc


def load_json_source(
    local_path: Path,
    remote_object: str,
    secrets: object | None,
) -> tuple[dict, str, str]:
    """Load Supabase data first, with the local JSON file as a development fallback."""
    settings = read_storage_settings(secrets)
    remote_warning = ""

    if settings.configured:
        try:
            return _download_json(settings, remote_object), "Supabase Storage", ""
        except StorageDataError as exc:
            remote_warning = str(exc)

    for candidate in (local_path, Path(f"{local_path}.gz")):
        if candidate.exists():
            source = f"local {candidate.name}"
            warning = f"{remote_warning} Using {source} instead." if remote_warning else ""
            return _read_local_json(candidate), source, warning

    if remote_warning:
        raise StorageDataError(remote_warning)
    raise StorageDataError(
        f"No local {local_path.name} found and Supabase Storage is not configured."
    )
