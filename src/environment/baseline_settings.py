"""Keep browser sessions separate from the development shop on port 8080."""
from settings import *  # noqa: F403

SESSION_COOKIE_NAME = "oscar_baseline_sessionid"
CSRF_COOKIE_NAME = "oscar_baseline_csrftoken"
