"""Read-only adapter. No personal cabinet sessions or electronic signatures."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import hashlib
import ipaddress
import json
from pathlib import Path
import socket
import tempfile
import time
from typing import Any, Iterator
from urllib.parse import urljoin, urlsplit

import httpx

BASE_URL = "https://zakup.gov.kz"
PUBLIC_PATH = "/api/core/api/public/"


class SourceError(RuntimeError):
    """A source failure, distinct from a successful empty search."""


class AccessDenied(SourceError):
    pass


class SchemaError(SourceError):
    pass


@dataclass(frozen=True)
class Page:
    count: int
    results: list[dict[str, Any]]
    next_url: str | None


@dataclass(frozen=True)
class Document:
    id: str
    name: str
    url: str
    announcement_id: int
    lot_id: int | None
    group_name: str | None


def utc_datetime(value: Any) -> datetime | None:
    """Convert source ISO dates and Unix seconds without guessing naive zones."""
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return datetime.fromtimestamp(value, tz=timezone.utc)
    result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise SchemaError("Дата источника не содержит часовой пояс")
    return result.astimezone(timezone.utc)


def positive_id(value: Any) -> int:
    if isinstance(value, bool) or not str(value).isdigit() or int(value) <= 0:
        raise ValueError("Идентификатор должен быть положительным целым числом")
    return int(value)


class ZakupClient:
    def __init__(
        self,
        *,
        transport: httpx.BaseTransport | None = None,
        interval: float = 1.0,
        attempts: int = 3,
        sleep=time.sleep,
        clock=time.monotonic,
        gate=None,
    ):
        self.http = httpx.Client(
            base_url=BASE_URL,
            timeout=httpx.Timeout(30, connect=10),
            follow_redirects=False,
            transport=transport,
            headers={"User-Agent": "TenderSearch/0.1", "Accept": "application/json"},
        )
        self.interval = max(0, interval)
        self.attempts = max(1, attempts)
        self.sleep, self.clock = sleep, clock
        self._next_request = 0.0
        self.gate = gate

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.http.close()

    def _pace(self):
        if self.gate is not None:
            self.gate.acquire()
        delay = self._next_request - self.clock()
        if delay > 0:
            self.sleep(delay)
        self._next_request = self.clock() + self.interval

    @staticmethod
    def _api_url(path: str) -> str:
        url = urljoin(BASE_URL, path)
        parts = urlsplit(url)
        if (
            parts.scheme != "https"
            or parts.netloc != "zakup.gov.kz"
            or not parts.path.startswith((PUBLIC_PATH, "/api/core/api/dicts/"))
            or ".." in parts.path.split("/")
            or parts.fragment
        ):
            raise SourceError("Небезопасный адрес страницы API")
        return url

    def _delay(self, response: httpx.Response, attempt: int) -> float:
        header = response.headers.get("Retry-After", "")
        try:
            seconds = float(header)
        except ValueError:
            try:
                seconds = (parsedate_to_datetime(header) - datetime.now(timezone.utc)).total_seconds()
            except (ValueError, TypeError, OverflowError):
                seconds = 2 ** attempt
        # Do not retry earlier than a long server-requested pause.
        if seconds > 60:
            raise SourceError(f"Источник просит повторить запрос через {seconds:.0f} секунд")
        return max(0, seconds)

    def get_json(self, path: str, params: dict | None = None) -> Any:
        url = self._api_url(path)
        for attempt in range(self.attempts):
            self._pace()
            try:
                response = self.http.get(url, params=params)
            except httpx.TransportError as exc:
                if attempt + 1 == self.attempts:
                    raise SourceError("Сетевая ошибка при обращении к zakup.gov.kz") from exc
                self.sleep(2 ** attempt)
                continue
            if response.status_code in (401, 403):
                raise AccessDenied(f"Источник ограничил доступ: HTTP {response.status_code}")
            if response.status_code == 429 or response.status_code >= 500:
                if attempt + 1 == self.attempts:
                    raise SourceError(f"Источник недоступен: HTTP {response.status_code}")
                self.sleep(self._delay(response, attempt))
                continue
            if response.status_code != 200:
                raise SourceError(f"Ошибка источника: HTTP {response.status_code}")
            try:
                return response.json()
            except (ValueError, json.JSONDecodeError) as exc:
                raise SchemaError("Источник вернул ответ не в формате JSON") from exc
        raise SourceError("Запрос не выполнен")

    def search(self, resource: str = "lots", **params) -> Page:
        if resource not in ("lots", "announcements", "plan-items"):
            raise ValueError("Неизвестный реестр")
        limit, offset = int(params.get("limit", 20)), int(params.get("offset", 0))
        if not 1 <= limit <= 100 or not 0 <= offset or offset + limit > 10_000:
            raise ValueError("limit: 1–100; offset + limit не больше 10000")
        params.update(limit=limit, offset=offset)
        return self._page(self.get_json(f"{PUBLIC_PATH}{resource}/", params))

    def _page(self, data: Any) -> Page:
        if not isinstance(data, dict) or not isinstance(data.get("results"), list):
            raise SchemaError("Изменился формат списка источника")
        if any(not isinstance(item, dict) or "id" not in item for item in data["results"]):
            raise SchemaError("Некорректные записи списка")
        count = data.get("count")
        if isinstance(count, bool) or not isinstance(count, int) or count < 0:
            raise SchemaError("Некорректное количество записей")
        next_url = data.get("next")
        if next_url is not None:
            if not isinstance(next_url, str):
                raise SchemaError("Некорректная ссылка следующей страницы")
            next_url = self._api_url(next_url)
        return Page(count, data["results"], next_url)

    def pages(self, resource: str = "lots", *, max_pages: int = 2, **params) -> Iterator[Page]:
        if max_pages < 1:
            raise ValueError("max_pages должен быть положительным")
        page = self.search(resource, **params)
        seen: set[str] = set()
        resource_path = f"{PUBLIC_PATH}{resource}/"
        for index in range(max_pages):
            yield page
            if page.next_url is None or index + 1 == max_pages:
                return
            if page.next_url in seen or urlsplit(page.next_url).path != resource_path:
                raise SchemaError("Цикл пагинации или смена реестра источником")
            seen.add(page.next_url)
            page = self._page(self.get_json(page.next_url))

    def announcement(self, identifier: int) -> dict:
        data = self.get_json(f"{PUBLIC_PATH}announcements/{positive_id(identifier)}/")
        if not isinstance(data, dict) or data.get("id") != int(identifier):
            raise SchemaError("Карточка не соответствует запрошенному объявлению")
        return data

    def reference(self, name: str, *, limit: int = 20, offset: int = 0) -> Page:
        if name not in {"lot-statuses", "purchase-methods", "systems", "katos"}:
            raise ValueError("Неизвестный справочник")
        if not 1 <= limit <= 100 or offset < 0:
            raise ValueError("Некорректная страница справочника")
        data = self.get_json(f"/api/core/api/dicts/{name}/", {"limit": limit, "offset": offset})
        if isinstance(data, list):
            data = {"results": data, "count": len(data), "next": None}
        elif isinstance(data, dict) and "result" in data and "results" not in data:
            data = {**data, "results": data["result"]}
        return self._page(data)

    def announcement_lots(self, identifier: int) -> list[dict]:
        data = self.get_json(f"{PUBLIC_PATH}announcements/{positive_id(identifier)}/lots/")
        if not isinstance(data, list) or any(not isinstance(item, dict) or "id" not in item for item in data):
            raise SchemaError("Изменился формат лотов объявления")
        return data

    def documents(self, identifier: int) -> list[Document]:
        identifier = positive_id(identifier)
        groups = self.get_json(f"{PUBLIC_PATH}announcements/{identifier}/general-files/")
        if not isinstance(groups, list):
            raise SchemaError("Изменился формат списка документов")
        result: dict[tuple[str, str], Document] = {}
        for group in groups:
            if not isinstance(group, dict) or (group.get("files") is not None and not isinstance(group["files"], list)):
                raise SchemaError("Некорректная группа документов")
            for entry in group.get("files") or []:
                if not isinstance(entry, dict):
                    raise SchemaError("Некорректный документ")
                file = entry.get("file") or {}
                if not isinstance(file, dict):
                    raise SchemaError("Некорректные сведения о файле")
                url = file.get("file_url")
                if not url:
                    continue  # The group may contain a requirement, not a downloadable file.
                lot = entry.get("lot")
                lot_id = lot.get("id") if isinstance(lot, dict) else lot
                doc = Document(
                    id=str(entry.get("id", file.get("uuid", ""))),
                    name=file.get("filename") or entry.get("original_name") or "document",
                    url=url,
                    announcement_id=identifier,
                    lot_id=int(lot_id) if lot_id is not None else None,
                    group_name=(group.get("group") or {}).get("name"),
                )
                result[(doc.id, url)] = doc
        return list(result.values())

    @staticmethod
    def _file_url(url: str, resolver=socket.getaddrinfo) -> str:
        parts = urlsplit(url)
        if (
            parts.scheme != "https"
            or parts.hostname not in {"zakup.gov.kz", "v3bl.goszakup.gov.kz"}
            or parts.username or parts.password
            or parts.port not in (None, 443)
        ):
            raise SourceError("Хост файла не входит в список разрешённых")
        try:
            addresses = resolver(parts.hostname, 443, type=socket.SOCK_STREAM)
            if not addresses or any(not ipaddress.ip_address(item[4][0]).is_global for item in addresses):
                raise SourceError("Файл ссылается на непубличный сетевой адрес")
        except OSError as exc:
            raise SourceError("Не удалось разрешить адрес файла") from exc
        return url

    def download(self, document: Document, directory: Path, *, max_bytes: int = 50 * 1024 * 1024) -> dict:
        directory.mkdir(parents=True, exist_ok=True)
        url = document.url
        for _ in range(4):
            self._file_url(url)
            self._pace()
            try:
                with self.http.stream("GET", url, headers={"Accept": "*/*"}) as response:
                    if response.status_code in (301, 302, 303, 307, 308):
                        location = response.headers.get("Location")
                        if not location:
                            raise SourceError("Перенаправление файла без адреса")
                        url = urljoin(url, location)
                        continue
                    if response.status_code != 200:
                        raise SourceError(f"Загрузка файла: HTTP {response.status_code}")
                    size_header = response.headers.get("Content-Length")
                    if size_header and int(size_header) > max_bytes:
                        raise SourceError("Файл превышает лимит размера")
                    digest, size = hashlib.sha256(), 0
                    temp_path = None
                    try:
                        with tempfile.NamedTemporaryFile(dir=directory, delete=False) as stream:
                            temp_path = Path(stream.name)
                            for chunk in response.iter_bytes():
                                size += len(chunk)
                                if size > max_bytes:
                                    raise SourceError("Файл превышает лимит размера")
                                digest.update(chunk)
                                stream.write(chunk)
                        if size == 0:
                            raise SourceError("Источник вернул пустой файл")
                        # Use content hash, never a remote filename as a local path.
                        suffix = Path(document.name).suffix.lower()
                        suffix = suffix if suffix in {".pdf", ".docx", ".doc", ".xlsx", ".zip"} else ".bin"
                        target = directory / (digest.hexdigest() + suffix)
                        temp_path.replace(target)
                    finally:
                        if temp_path and temp_path.exists():
                            temp_path.unlink()
                    return {"path": str(target), "sha256": digest.hexdigest(), "size": size}
            except httpx.TransportError as exc:
                raise SourceError("Сетевая ошибка загрузки файла") from exc
        raise SourceError("Слишком много перенаправлений файла")
