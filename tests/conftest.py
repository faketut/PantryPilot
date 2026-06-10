"""Shared pytest fixtures.

Several modules import ``app.config`` at import time, which calls ``_require``
for ``GOOGLE_API_KEY`` and ``MDB_MCP_CONNECTION_STRING``. Tests should never
touch real credentials, so we inject test values **before** any app module is
imported by setting them as environment variables in this module.
"""
import os

os.environ.setdefault("GOOGLE_API_KEY", "test-google-key")
os.environ.setdefault(
    "MDB_MCP_CONNECTION_STRING",
    "mongodb://test:test@localhost:27017/test",
)
os.environ.setdefault("PLAN_VIA_MCP", "false")
