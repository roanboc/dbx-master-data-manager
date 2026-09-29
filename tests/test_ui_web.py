"""The workbench's web safety and logging, with the Flask test client (plan B.8.2, B.9.2; DuckDB only):
the host allow-list, cross-site posts, the response headers, the plain error page, callback errors and
the log filter. A sentinel stands for a personal value and must never reach a response or a log line.
"""

from __future__ import annotations

import json
import logging
import sys
from collections.abc import Iterator

import pytest
from dash import Dash, Input, Output, html

from mdm.backend.factory import open_store
from mdm.config import Settings
from mdm.services.context import Hub
from mdm.ui import app as app_module
from mdm.ui import context, ids, layout, web
from tests.conftest import base_settings, open_hub

#: stands for a personal value: it must never reach a response or a log line
SENTINEL = "Tamsin Quorrel"
PORT = 8050


@pytest.fixture
def hub() -> Iterator[Hub]:
    settings = base_settings()
    store = open_store(settings)
    store.init_schema(create_landing=True)
    opened = open_hub(settings, store, "duckdb")
    try:
        yield opened
    finally:
        opened.close()
        store.close()


def _build(settings: Settings, hub: Hub, **kwargs) -> Dash:
    built = app_module.create_app(settings, hub=hub, worker=False, **kwargs)
    return built


@pytest.fixture
def app(hub: Hub) -> Iterator[Dash]:
    built = _build(hub.settings, hub, listen=("127.0.0.1", PORT))
    try:
        yield built
    finally:
        built.server.extensions[context.EXTENSION].close()


def _update(client, body: dict, **headers):
    return client.post(
        "/_dash-update-component",
        data=json.dumps(body),
        headers={"Content-Type": "application/json", "Host": f"127.0.0.1:{PORT}", **headers},
    )


# ------------------------------------------------------------------------------------------ hosts and posts


def test_a_local_workbench_answers_loopback_names_on_its_port_only(app: Dash) -> None:
    client = app.server.test_client()
    for host in (f"127.0.0.1:{PORT}", f"localhost:{PORT}", f"[::1]:{PORT}"):
        assert client.get("/", headers={"Host": host}).status_code == 200, host
    for host in ("evil.example", f"evil.example:{PORT}", "127.0.0.1:9999", "127.0.0.1", "10.0.0.8:8050"):
        answer = client.get("/", headers={"Host": host})
        assert answer.status_code == 400, host
        assert answer.get_data(as_text=True) == web.HOST_REFUSED


def test_inside_an_app_the_platform_chooses_the_host(hub: Hub, on_platform: str) -> None:
    settings = Settings.from_env().with_(duckdb_path=":memory:", models_dir=hub.settings.models_dir)
    assert settings.in_databricks_app
    built = _build(settings, hub, listen=("0.0.0.0", 8000))
    try:
        assert built.server.test_client().get("/", headers={"Host": "workbench.example"}).status_code == 200
    finally:
        built.server.extensions[context.EXTENSION].close()


def test_a_post_from_another_site_is_refused(app: Dash) -> None:
    client = app.server.test_client()
    for headers in (
        {"Origin": "http://elsewhere.example"},
        {"Origin": f"http://127.0.0.1:{PORT + 1}"},
        {"Origin": "null"},
        {"Sec-Fetch-Site": "cross-site"},
        {"Sec-Fetch-Site": "same-site"},
    ):
        answer = _update(client, {}, **headers)
        assert answer.status_code == 403, headers
        assert answer.get_data(as_text=True) == web.CROSS_SITE_REFUSED
    for headers in ({"Origin": f"http://127.0.0.1:{PORT}", "Sec-Fetch-Site": "same-origin"}, {}):
        assert _update(client, {}, **headers).status_code != 403, headers


def test_the_host_and_origin_readers() -> None:
    assert web.split_host("127.0.0.1:8050") == ("127.0.0.1", 8050)
    assert web.split_host("[::1]:8050") == ("::1", 8050)
    assert web.split_host("LOCALHOST") == ("localhost", None)
    assert web.host_allowed("localhost:8050", None) and web.host_allowed("localhost", ("127.0.0.1", 80))
    assert not web.host_allowed("localhost", ("127.0.0.1", 8050))


# ------------------------------------------------------------------------------------------ headers


def test_every_response_forbids_framing_and_caching(app: Dash) -> None:
    client = app.server.test_client()
    host = {"Host": f"127.0.0.1:{PORT}"}
    answers = [
        client.get("/", headers=host),
        client.get("/_dash-layout", headers=host),
        client.get("/_dash-dependencies", headers=host),
        client.get("/record/ORG-000001", headers=host),
        _update(client, {}),
    ]
    for answer in answers:
        assert answer.headers["X-Frame-Options"] == "DENY"
        policy = dict(
            part.strip().split(" ", 1) for part in answer.headers["Content-Security-Policy"].split(";")
        )
        assert policy["default-src"] == "'self'" and policy["frame-ancestors"] == "'none'"
        assert policy["script-src"].startswith("'self'") and "unsafe" not in policy["script-src"]
        assert policy["object-src"] == "'none'" and policy["base-uri"] == "'none'"
        assert answer.headers["X-Content-Type-Options"] == "nosniff"
        assert answer.headers["Referrer-Policy"] == "same-origin"
        assert answer.headers["Cache-Control"] == "no-store"
    asset = client.get("/assets/styles.css", headers=host)
    assert asset.status_code == 200 and asset.headers.get("Cache-Control") != "no-store"
    assert asset.headers["X-Frame-Options"] == "DENY"
    # a path that only contains the assets' prefix is not an asset
    assert client.get("/record/x/assets/y", headers=host).headers["Cache-Control"] == "no-store"


# ------------------------------------------------------------------------------------------ errors


def test_a_layout_that_fails_shows_the_plain_sentence(
    app: Dash, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    def broken(*args, **kwargs):
        raise ValueError(SENTINEL)

    monkeypatch.setattr(layout, "shell", broken)
    with caplog.at_level(logging.DEBUG):
        answer = app.server.test_client().get("/_dash-layout", headers={"Host": f"127.0.0.1:{PORT}"})
    assert answer.status_code == 500
    assert answer.get_data(as_text=True) == web.ERROR_SENTENCE
    assert "ValueError" in caplog.text and SENTINEL not in caplog.text


def test_a_callback_that_fails_notifies_by_type_only(
    hub: Hub,
    caplog: pytest.LogCaptureFixture,
) -> None:
    built = _build(hub.settings, hub, listen=("127.0.0.1", PORT))
    built.validation_layout = html.Div(
        [built.validation_layout, html.Div(id="probe-out"), html.Button(id="probe")]
    )

    @built.callback(Output("probe-out", "children"), Input("probe", "n_clicks"), prevent_initial_call=True)
    def probe(clicks):
        raise ValueError(SENTINEL)

    body = {
        "output": "probe-out.children",
        "outputs": {"id": "probe-out", "property": "children"},
        "inputs": [{"id": "probe", "property": "n_clicks", "value": 1}],
        "changedPropIds": ["probe.n_clicks"],
        "state": [],
    }
    try:
        with caplog.at_level(logging.DEBUG):
            answer = _update(built.server.test_client(), body)
    finally:
        built.server.extensions[context.EXTENSION].close()
    text = answer.get_data(as_text=True)
    assert answer.status_code == 200, text[:200]
    assert SENTINEL not in text and web.ERROR_SENTENCE in text
    assert json.loads(text)["sideUpdate"][ids.NOTIFY]["sendNotifications"][0]["color"] == "red"
    assert "ValueError" in caplog.text and SENTINEL not in caplog.text


# ------------------------------------------------------------------------------------------ the log filter


def _record(name: str, msg: str, args=None, exc_info=None) -> logging.LogRecord:
    return logging.LogRecord(name, logging.INFO, __file__, 1, msg, args, exc_info)


def test_a_request_line_keeps_its_path_only_when_it_is_safe_text() -> None:
    kept = _record(
        "werkzeug", '127.0.0.1 - - [x] "%s" %s %s', ("GET /record/ORG-000123 HTTP/1.1", "200", "-")
    )
    web.RedactingFilter().filter(kept)
    assert kept.getMessage().endswith('"GET /record/ORG-000123 HTTP/1.1" 200 -')
    for line in (f"GET /record/{SENTINEL} HTTP/1.1", "GET /record/Tamsin%20Quorrel HTTP/1.1"):
        withheld = _record("werkzeug", '127.0.0.1 - - [x] "%s" %s %s', (line, "404", "-"))
        web.RedactingFilter().filter(withheld)
        message = withheld.getMessage()
        assert "Tamsin" not in message and "Quorrel" not in message
        assert '"GET /<withheld> HTTP/1.1" 404 -' in message
    query = _record("werkzeug", '"%s" %s %s', ("GET /?view=mine&name=Tamsin+Quorrel HTTP/1.1", "200", "-"))
    web.RedactingFilter().filter(query)
    assert query.getMessage() == '"GET /?<withheld> HTTP/1.1" 200 -'


def test_a_name_typed_into_the_address_is_withheld_whatever_its_shape() -> None:
    """A single word passes the safe-text pattern, so the log keeps only the paths the workbench serves,
    with an ID or a source key where a record is named, and codes or a task ID in the query."""
    kept = (
        "GET / HTTP/1.1",
        "GET /record/ORG-000123 HTTP/1.1",
        "GET /record/crm:C000123 HTTP/1.1",
        "GET /source/crm/C000123 HTTP/1.1",
        "GET /?view=team&kind=review&task=TSK-5cfa2e93a4720ad3 HTTP/1.1",
        "GET /assets/styles.css?m=1790524484.46 HTTP/1.1",
        "POST /_dash-update-component HTTP/1.1",
    )
    for line in kept:
        assert web.redact_request_line(line) == line, line
    for line, shown in (
        ("GET /record/Vantrasse HTTP/1.1", "GET /<withheld> HTTP/1.1"),
        ("GET /Vantrasse HTTP/1.1", "GET /<withheld> HTTP/1.1"),
        ("GET /source/crm/Vantrasse%20Olwen HTTP/1.1", "GET /<withheld> HTTP/1.1"),
        ("GET /?task=Olwen HTTP/1.1", "GET /?<withheld> HTTP/1.1"),
        ("GET /?view=team&note=Olwen HTTP/1.1", "GET /?<withheld> HTTP/1.1"),
        ("GET /record/ORG-000123?tab=Olwen+Vantrasse HTTP/1.1", "GET /record/ORG-000123?<withheld> HTTP/1.1"),
    ):
        assert web.redact_request_line(line) == shown, line


def test_the_alike_reviews_and_a_batch_are_logged_and_a_name_in_their_place_is_not() -> None:
    """Story 3.3: `/groups`, `/batch/<ID>` of a batch ID's shape, and a group's key or a batch's ID in the
    inbox's query are logged; a name where a batch ID goes, or a key of another shape, is withheld."""
    batch_id = "BAT-0123456789abcdef0123"
    group = "SIG-0123456789abcdef"
    for line in (
        "GET /groups HTTP/1.1",
        f"GET /batch/{batch_id} HTTP/1.1",
        f"GET /?group={group} HTTP/1.1",
        f"GET /?batch={batch_id}&task=TSK-5cfa2e93a4720ad3 HTTP/1.1",
    ):
        assert web.redact_request_line(line) == line, line
    for line, shown in (
        ("GET /batch/Vantrasse HTTP/1.1", "GET /<withheld> HTTP/1.1"),
        (f"GET /batch/{batch_id}/Olwen HTTP/1.1", "GET /<withheld> HTTP/1.1"),
        ("GET /groups/Olwen HTTP/1.1", "GET /<withheld> HTTP/1.1"),
        ("GET /?group=Olwen HTTP/1.1", "GET /?<withheld> HTTP/1.1"),
        ("GET /?batch=BAT-Vantrasse HTTP/1.1", "GET /?<withheld> HTTP/1.1"),
        (f"GET /?group={batch_id} HTTP/1.1", "GET /?<withheld> HTTP/1.1"),
    ):
        assert web.redact_request_line(line) == shown, line


def test_a_record_with_an_exception_keeps_its_type_only() -> None:
    try:
        raise KeyError(SENTINEL)
    except KeyError:
        record = _record("flask.app", f"Exception on /record/{SENTINEL} [GET]", None, sys.exc_info())
    web.RedactingFilter().filter(record)
    formatted = logging.Formatter().format(record)
    assert formatted == "flask.app: KeyError"
    assert record.exc_info is None and record.exc_text is None and record.args is None


def test_the_filter_sits_on_every_logger_it_names_once(app: Dash) -> None:
    for name in (*web.FILTERED_LOGGERS, app.server.logger.name):
        filters = logging.getLogger(name).filters
        assert filters.count(web.REDACTING_FILTER) == 1, name
