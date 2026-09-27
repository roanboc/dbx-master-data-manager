"""The steward workbench: a Dash application over the hub's services (initiative 3, decision 18).

`mdm ui` serves it locally; `app.py` serves it as a Databricks App. It reaches the hub only through
the services (`mdm.services`), renders what they return, already masked by role, and keeps no personal
value in a URL, an ID, a store in the browser, a tooltip or a log line.
"""

from mdm.ui.app import create_app

__all__ = ["create_app"]
