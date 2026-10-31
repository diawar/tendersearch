from datetime import timezone

import httpx
import pytest

from tender_search.client import (
    AccessDenied, Document, NotFound, SchemaError, SourceError, ZakupClient, utc_datetime,
)


def client(handler, **kwargs):
    return ZakupClient(transport=httpx.MockTransport(handler), interval=0, **kwargs)


def page(items=None, next_url=None):
    return {"count": 2, "results": items or [], "next": next_url}


def test_404_is_distinct_from_an_empty_lot_list():
    with client(lambda _: httpx.Response(404)) as api:
        with pytest.raises(NotFound):
            api.announcement_lots(100)


def test_pagination_preserves_source_query_and_does_not_overfetch():
    requests = []

    def handler(request):
        requests.append(request)
        if len(requests) == 1:
            return httpx.Response(200, json=page([{"id": 1}], "/api/core/api/public/lots/?q=телефон&offset=1&limit=1"))
        return httpx.Response(200, json=page([{"id": 2}], "/api/core/api/public/lots/?q=телефон&offset=2&limit=1"))

    with client(handler) as api:
        pages = list(api.pages(q="телефон", limit=1, max_pages=2))
    assert len(requests) == 2
    assert requests[1].url.params["q"] == "телефон"
    assert [row["id"] for p in pages for row in p.results] == [1, 2]
    assert pages[-1].next_url  # The bounded result must not claim completeness.


@pytest.mark.parametrize("address", [
    "https://evil.example/api/core/api/public/lots/",
    "http://zakup.gov.kz/api/core/api/public/lots/",
    "https://zakup.gov.kz/api/core/api/auth/users/",
])
def test_untrusted_pagination_url_is_rejected(address):
    with client(lambda _: httpx.Response(200, json=page(next_url=address))) as api:
        with pytest.raises(SourceError):
            api.search()


def test_429_retry_after_and_server_failure_are_retried():
    sleeps, calls = [], []

    def handler(request):
        calls.append(request)
        return [httpx.Response(429, headers={"Retry-After": "3"}),
                httpx.Response(503), httpx.Response(200, json=page())][len(calls) - 1]

    with client(handler, sleep=sleeps.append) as api:
        assert api.search().results == []
    assert sleeps == [3, 2]


def test_access_denied_is_not_empty_result_and_not_retried():
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(403)

    with client(handler) as api:
        with pytest.raises(AccessDenied):
            api.search()
    assert len(calls) == 1


def test_malformed_schema_is_not_empty_result():
    with client(lambda _: httpx.Response(200, json={"items": []})) as api:
        with pytest.raises(SchemaError):
            api.search()


def test_network_failure_exhausts_bounded_retries():
    def handler(request):
        raise httpx.ConnectTimeout("timeout", request=request)

    sleeps = []
    with client(handler, sleep=sleeps.append) as api:
        with pytest.raises(SourceError):
            api.search()
    assert sleeps == [1, 2]


def test_documents_keep_lot_association_and_deduplicate():
    entry = {"id": 9, "lot": {"id": 7}, "file": {
        "filename": "spec.pdf", "file_url": "https://zakup.gov.kz/files/spec.pdf"}}
    data = [{"group": {"name": "Техническая спецификация"}, "files": [entry, entry, {"id": 10}]},
            {"group": {"name": "Требования"}, "files": None}]
    with client(lambda _: httpx.Response(200, json=data)) as api:
        docs = api.documents(123)
    assert len(docs) == 1
    assert docs[0].lot_id == 7 and docs[0].announcement_id == 123


def test_dates_support_iso_and_unix_without_guessing_timezone():
    assert utc_datetime("2026-10-27T15:00:00+05:00").hour == 10
    assert utc_datetime(0).tzinfo == timezone.utc
    assert utc_datetime(None) is None
    with pytest.raises(SchemaError):
        utc_datetime("2026-10-27T15:00:00")


@pytest.mark.parametrize("url", ["https://127.0.0.1/a", "https://evil.example/a", "http://zakup.gov.kz/a"])
def test_document_host_allowlist(url):
    with pytest.raises(SourceError):
        ZakupClient._file_url(url)


def test_private_dns_address_is_rejected():
    with pytest.raises(SourceError):
        ZakupClient._file_url("https://zakup.gov.kz/a", resolver=lambda *a, **kw: [(2, 1, 6, "", ("10.0.0.1", 443))])


def test_download_limit_cleans_partial_file_and_ignores_remote_filename(tmp_path, monkeypatch):
    monkeypatch.setattr(ZakupClient, "_file_url", staticmethod(lambda url: url))
    doc = Document("1", "../../bad.pdf", "https://zakup.gov.kz/file", 3, None, None)
    with client(lambda _: httpx.Response(200, content=b"123456")) as api:
        with pytest.raises(SourceError):
            api.download(doc, tmp_path, max_bytes=3)
        assert list(tmp_path.iterdir()) == []
        downloaded = api.download(doc, tmp_path)
    assert downloaded["size"] == 6
    assert downloaded["path"].endswith(".pdf")
    assert len(list(tmp_path.iterdir())) == 1


def test_redirect_to_untrusted_host_is_rejected(tmp_path, monkeypatch):
    original = ZakupClient._file_url
    public_dns = lambda *a, **kw: [(2, 1, 6, "", ("8.8.8.8", 443))]
    monkeypatch.setattr(ZakupClient, "_file_url", staticmethod(lambda url: original(url, resolver=public_dns)))
    doc = Document("1", "x.pdf", "https://zakup.gov.kz/file", 3, None, None)
    with client(lambda _: httpx.Response(302, headers={"Location": "https://evil.example/file"})) as api:
        with pytest.raises(SourceError):
            api.download(doc, tmp_path)


def test_api_pagination_cycle_is_detected():
    with client(lambda _: httpx.Response(200, json=page([{"id": 1}], "/api/core/api/public/lots/?offset=1"))) as api:
        with pytest.raises(SchemaError):
            list(api.pages(max_pages=4))


@pytest.mark.parametrize("data", [
    [{"id": 1, "name": "Регион"}],
    {"count": 1, "result": [{"id": 1, "name": "Регион"}], "next": None},
])
def test_reference_normalizes_both_response_formats(data):
    with client(lambda _: httpx.Response(200, json=data)) as api:
        result = api.reference("katos")
    assert result.count == 1 and result.results[0]["id"] == 1


def test_long_retry_after_is_not_ignored():
    sleeps = []
    with client(lambda _: httpx.Response(429, headers={"Retry-After": "120"}), sleep=sleeps.append) as api:
        with pytest.raises(SourceError, match="120"):
            api.search()
    assert sleeps == []
