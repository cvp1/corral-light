"""Shared test helpers.

Importing anything under testkit first drops the deployed hub's own settings
from this process's environment. A suite run from inside a Corral pane
inherits them from the hub that spawned the pane, and a test hub that read
them would bind the tailnet login, the Serve origin or the real port, and
refuse or answer differently from CI (2026-11-22: three modules failed only
when run from a pane). Tests that need one set it themselves.
"""
import os

HUB_DEPLOYMENT_ENV = ("CORRAL_LIGHT_SERVE_HOST", "CORRAL_TAILSCALE_LOGIN",
                      "CORRAL_LIGHT_BIND", "CORRAL_LIGHT_PORT")

for _k in HUB_DEPLOYMENT_ENV:
    os.environ.pop(_k, None)
