"""Serving the workbench: `serve` for `mdm ui`, `main` for the platform's `app.py`.

It listens on a loopback address unless a Databricks App runs it (decision 21); `mdm ui` refuses any
other host before it gets here. Dash's development tools stay off unless `dev`, and the reloader is
always off, since the DuckDB file takes one writer.

Owner: SHELL (the skeleton wrote it; B.8.1).
"""

from __future__ import annotations

from mdm.config import Settings

#: the loopback address the workbench listens on outside a Databricks App
LOOPBACK = "127.0.0.1"
#: every address, inside a Databricks App only (the platform's proxy is the one caller)
ANY_ADDRESS = "0.0.0.0"  # chosen only when settings.in_databricks_app


def serve(settings: Settings, *, host: str, port: int, dev: bool = False, worker: bool | None = None) -> None:
    """Builds the app for `settings` and serves it on `host:port` until stopped (threaded, no reloader); on a
    shared store only with the matcher's checkpoint in force, since its tray commits the stewards' decisions."""
    from mdm.ui.app import create_app

    settings.validate_checkpoint_in_force()
    app = create_app(settings, worker=worker, listen=(host, port))
    app.run(
        host=host,
        port=port,
        debug=dev,
        use_debugger=False,  # Werkzeug's console would sit outside the host and cross-site checks
        use_reloader=False,
        threaded=True,
        dev_tools_ui=dev,
        dev_tools_props_check=dev,
        dev_tools_hot_reload=False,
    )


def main() -> None:
    """The platform's entry (`app.py`): settings from the environment, every address inside a Databricks
    App and loopback elsewhere, the platform's port when it names one."""
    settings = Settings.from_env()
    host = ANY_ADDRESS if settings.in_databricks_app else LOOPBACK
    serve(settings, host=host, port=settings.app_port or settings.ui_port)
