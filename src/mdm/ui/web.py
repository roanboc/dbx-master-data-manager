"""Local web safety and logging: the host allow-list, cross-site posts, response headers, the plain
error page, callback errors and the log filter that keeps values out of every log line.

`install(app, settings, listen)` wires it into the Flask server under Dash:
- outside a Databricks App, `request.host` must be a loopback name (with the served port), else 400;
- a POST whose `Sec-Fetch-Site` is present and not `same-origin` or `none`, or whose `Origin` names
  another host and port, is refused with 403;
- every response carries `X-Frame-Options: DENY`, a Content-Security-Policy that loads scripts only from
  the workbench itself and the inline scripts Dash writes (by their hashes), and never frames it,
  `X-Content-Type-Options: nosniff`, `Referrer-Policy: same-origin`, and `Cache-Control: no-store`
  except the assets and Dash's component bundles;
- an unhandled exception returns a plain 500 sentence and is logged as its type only;
- `RedactingFilter` on `dash`, `dash.dash`, `flask.app`, `werkzeug`, `mdm.ui` and the Flask app's own
  logger: a request line keeps its path only when it is one the workbench serves, with an ID or a source
  key where a record is named, and a query value only for a code or a task ID; anything else a person
  could have typed is withheld.

UI modules log through the logger `mdm.ui` (a filter on a logger does not reach its children).

Owner: SHELL (B.8.2).
"""

from __future__ import annotations

import logging
import re
from urllib.parse import parse_qsl, unquote, urlsplit

from dash import Dash, set_props
from flask import Response, request
from werkzeug.exceptions import HTTPException

from mdm.config import Settings
from mdm.models.safety import MASTER_ID_RE, SAFE_TEXT_RE, SOURCE_KEY_RE, TASK_ID_RE
from mdm.ui import ids
from mdm.ui.components import common

logger = logging.getLogger("mdm.ui")

#: the sentence of the plain error page and of a callback that failed unexpectedly
ERROR_SENTENCE = "Something went wrong. The details are not shown, because they could hold a personal value."
#: the plain answers of the host and cross-site checks
HOST_REFUSED = "This address is not served."
CROSS_SITE_REFUSED = "This request came from another site, so it was refused."
#: the loggers the redacting filter is installed on
FILTERED_LOGGERS = ("dash", "dash.dash", "flask.app", "werkzeug", "mdm.ui")
#: the host names a local workbench answers to
LOOPBACK_NAMES = frozenset({"127.0.0.1", "localhost", "::1"})
#: `Sec-Fetch-Site` values a post may carry: the page itself, or the steward typing the address
SAME_SITE = frozenset({"same-origin", "none"})
#: the paths that may be cached: the workbench's own assets and Dash's component bundles
CACHEABLE = ("/assets/", "/_dash-component-suites/")
#: what every response carries (the Content-Security-Policy is built per app: `content_security_policy`)
SECURITY_HEADERS = {
    "X-Frame-Options": "DENY",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "same-origin",
}
#: the policy's parts after `script-src`; styles may be inline (the component library writes them), and
#: images and masks may be `data:` URIs (the icons)
CSP_REST = (
    "style-src 'self' 'unsafe-inline'",
    "img-src 'self' data:",
    "font-src 'self' data:",
    "connect-src 'self'",
    "object-src 'none'",
    "base-uri 'none'",
    "form-action 'self'",
    "frame-ancestors 'none'",
)
#: the paths whose request line is logged whole: the page, Dash's own routes and the assets
LOGGED_PREFIXES = ("/assets/", "/_dash-", "/_favicon")
#: the query keys a logged request line may keep, with the shape of the value each takes
LOGGED_QUERY = {
    "view": re.compile(r"^[a-z_]{1,40}\Z"),
    "kind": re.compile(r"^[a-z_]{1,40}\Z"),
    "tab": re.compile(r"^[a-z_]{1,40}\Z"),
    "entity": re.compile(r"^[a-z_]{1,40}\Z"),
    "task": TASK_ID_RE,
    "m": re.compile(r"^[0-9.]{1,40}\Z"),  # the assets' cache-busting stamp
}
_REQUEST_LINE = re.compile(
    r"^(?P<pre>(?:\x1b\[[0-9;]*m)*)(?P<method>[A-Z]{3,8}) (?P<target>.*) (?P<proto>HTTP/[0-9.]{1,3})"
    r"(?P<post>(?:\x1b\[[0-9;]*m)*)\Z",
    re.S,
)


def split_host(host: str) -> tuple[str, int | None]:
    """ "127.0.0.1:8050" → ("127.0.0.1", 8050); "[::1]:8050" → ("::1", 8050); no port → None."""
    host = (host or "").strip().lower()
    if host.startswith("["):
        name, _, rest = host[1:].partition("]")
        port_text = rest[1:] if rest.startswith(":") else ""
    else:
        name, colon, port_text = host.rpartition(":")
        if not colon:
            name, port_text = host, ""
    port = int(port_text) if port_text.isdigit() else None
    return name, port


def host_allowed(host: str, listen: tuple[str, int] | None) -> bool:
    """Whether a local workbench answers `host`: a loopback name, on the served port when it is known."""
    name, port = split_host(host)
    if name not in LOOPBACK_NAMES:
        return False
    if listen is None:
        return True
    return (port if port is not None else 80) == listen[1]


def cross_site(headers, host: str) -> bool:
    """Whether a post comes from another site: `Sec-Fetch-Site` other than same-origin or none, or an
    `Origin` whose host and port are not this request's."""
    fetch_site = headers.get("Sec-Fetch-Site")
    if fetch_site is not None and fetch_site.strip().lower() not in SAME_SITE:
        return True
    origin = headers.get("Origin")
    if origin is None:
        return False
    parts = urlsplit(origin.strip())
    if not parts.netloc:
        return True  # "null" or garbage
    return split_host(parts.netloc) != split_host(host)


def content_security_policy(app: Dash) -> str:
    """The policy: everything from the workbench itself; scripts also from the inline scripts Dash writes
    into the page, each by its hash (`Dash.csp_hashes`), and nothing else; never framed."""
    try:
        hashes = list(app.csp_hashes())  # each already quoted: "'sha256-…'"
    except Exception as error:  # noqa: BLE001 - without the hashes the page keeps to its own files
        logger.warning("csp hashes unavailable type=%s", type(error).__name__)
        hashes = []
    script = " ".join(["script-src 'self'", *hashes])
    return "; ".join(["default-src 'self'", script, *CSP_REST])


def install(app: Dash, settings: Settings, listen: tuple[str, int] | None) -> None:
    """Installs the host allow-list, the cross-site check, the response headers, the error handler and
    the log filter on `app.server`; `listen` is the (host, port) served, when known."""
    server = app.server
    check_host = not settings.in_databricks_app
    policy: list[str] = []  # built at the first response, once every script Dash writes is known

    @server.before_request
    def _refuse_strangers():
        if check_host and not host_allowed(request.host, listen):
            logger.warning("refused host")
            return Response(HOST_REFUSED, status=400, mimetype="text/plain")
        if request.method == "POST" and cross_site(request.headers, request.host):
            logger.warning("refused cross-site post")
            return Response(CROSS_SITE_REFUSED, status=403, mimetype="text/plain")
        return None

    @server.after_request
    def _secure(response: Response) -> Response:
        for name, value in SECURITY_HEADERS.items():
            response.headers[name] = value
        if not policy:
            policy.append(content_security_policy(app))
        response.headers["Content-Security-Policy"] = policy[0]
        if not request.path.startswith(CACHEABLE):
            response.headers["Cache-Control"] = "no-store"
            response.headers.pop("ETag", None)
        return response

    def _plain_error(error: Exception):
        if isinstance(error, HTTPException):
            return error
        logger.error("request failed type=%s", type(error).__name__)
        return Response(ERROR_SENTENCE, status=500, mimetype="text/plain")

    server.register_error_handler(Exception, _plain_error)
    install_log_filter(server.logger.name)


def on_callback_error(error: Exception) -> None:
    """Given to `Dash(on_error=…)`: logs the exception's type only and sends ERROR_SENTENCE as a
    notification through `set_props(ids.NOTIFY, …)`. Every output of the failed callback stays as it was."""
    logger.error("callback failed type=%s", type(error).__name__)
    set_props(ids.NOTIFY, {"sendNotifications": common.notify(ERROR_SENTENCE, "red")})


def safe_path(path: str) -> bool:
    """Whether every segment of a request's path is safe text (`mdm.models.safety.SAFE_TEXT_RE`)."""
    return all(SAFE_TEXT_RE.match(segment) for segment in path.split("/") if segment)


def safe_query(query: str) -> bool:
    """Whether every key and value of a request's query is safe text."""
    if not query:
        return True
    try:
        pairs = parse_qsl(query, keep_blank_values=True, strict_parsing=True)
    except ValueError:
        return False
    return all(SAFE_TEXT_RE.match(key) and SAFE_TEXT_RE.match(value) for key, value in pairs)


def logged_path(path: str) -> bool:
    """Whether a path may be logged: `/`, Dash's routes and the assets (safe text), `/record/<ref>` with a
    master ID or a source key, or `/source/<system>/<key>` of a source key. Anything else could be a name
    typed into the address bar."""
    decoded = unquote(path)
    if decoded == "/":
        return True
    if decoded.startswith(LOGGED_PREFIXES):
        return safe_path(decoded)
    segments = [segment for segment in decoded.split("/") if segment]
    if len(segments) == 2 and segments[0] == "record":
        return bool(MASTER_ID_RE.match(segments[1]) or SOURCE_KEY_RE.match(segments[1]))
    if len(segments) == 3 and segments[0] == "source":
        return bool(SOURCE_KEY_RE.match(f"{segments[1]}:{segments[2]}"))
    return False


def logged_query(query: str) -> bool:
    """Whether a query may be logged: every key one of LOGGED_QUERY's, its value of that key's shape."""
    if not query:
        return True
    try:
        pairs = parse_qsl(query, keep_blank_values=True, strict_parsing=True)
    except ValueError:
        return False
    return all(key in LOGGED_QUERY and LOGGED_QUERY[key].match(value) for key, value in pairs)


def safe_target(target: str) -> bool:
    """Whether a request target (path and query) may be written to a log whole."""
    parts = urlsplit(target)
    return logged_path(parts.path) and logged_query(parts.query) and not parts.fragment


def redact_request_line(line: str) -> str:
    """ "GET /record/ORG-000123 HTTP/1.1" as it is; the path as `/<withheld>` unless it is one the workbench
    serves with an ID or a source key where it names a record (`logged_path`), and the query as
    `<withheld>` unless every value is a code or a task ID (`logged_query`); any other text as
    `<withheld>`."""
    match = _REQUEST_LINE.match(line)
    if match is None:
        return "<withheld>"
    parts = urlsplit(match["target"])
    shown = parts.path if logged_path(parts.path) else "/<withheld>"
    if parts.query:
        shown += "?" + (parts.query if logged_query(parts.query) else "<withheld>")
    return f"{match['pre']}{match['method']} {shown} {match['proto']}{match['post']}"


class RedactingFilter(logging.Filter):
    """Rewrites a record with `exc_info` to "<logger>: <ExceptionType>", clearing `exc_info`, `exc_text`,
    `stack_info` and `args`; a werkzeug request line keeps its method and status, and its path only when
    every segment and query value is safe text (`mdm.models.safety`), else `/<withheld>`."""

    def filter(self, record: logging.LogRecord) -> bool:
        if record.exc_info:
            error = record.exc_info[1]
            kind = type(error).__name__ if error is not None else "Exception"
            record.msg = f"{record.name}: {kind}"
            record.args = None
            record.exc_info = None
            record.exc_text = None
            record.stack_info = None
            return True
        if record.name == "werkzeug" and isinstance(record.args, tuple) and len(record.args) == 3:
            line, code, size = record.args
            if isinstance(line, str):
                record.args = (redact_request_line(line), code, size)
        return True


#: the one filter, so installing it again for another app adds nothing
REDACTING_FILTER = RedactingFilter()


def install_log_filter(*extra: str) -> None:
    """Adds REDACTING_FILTER to FILTERED_LOGGERS, to `extra` and to every `mdm.ui.*` logger that exists,
    once each."""
    names = set(FILTERED_LOGGERS) | set(extra)
    names |= {name for name in logging.root.manager.loggerDict if name.startswith("mdm.ui.")}
    for name in sorted(names):
        target = logging.getLogger(name)
        if REDACTING_FILTER not in target.filters:
            target.addFilter(REDACTING_FILTER)
