"""Shared test-only credentials.

Generated per run so the public repository never contains password or
secret literals that could be mistaken for real credentials or reused.
Override with TEST_PASSWORD / TEST_BOT_KEY / TEST_TIMER_KEY / TEST_JWT_SECRET.
"""

import os
import secrets

TEST_PASSWORD = os.environ.get("TEST_PASSWORD") or secrets.token_urlsafe(18)
BOT_KEY = os.environ.get("TEST_BOT_KEY") or secrets.token_urlsafe(24)
TIMER_KEY = os.environ.get("TEST_TIMER_KEY") or secrets.token_urlsafe(24)
JWT_SECRET = os.environ.get("TEST_JWT_SECRET") or secrets.token_urlsafe(48)
