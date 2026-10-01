#!/bin/sh
# Container entrypoint. The work is in entrypoint.py (testable); this only
# guarantees the image's python runs it, whatever PATH compose passed.
exec /usr/local/bin/python3 /opt/corral-light/container/entrypoint.py "$@"
