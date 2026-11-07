"""Keep the deliberately buggy shop's browser state separate from other shops."""
from experiment_settings import *  # noqa: F403

SESSION_COOKIE_NAME = "oscar_buggy_sessionid"
CSRF_COOKIE_NAME = "oscar_buggy_csrftoken"
