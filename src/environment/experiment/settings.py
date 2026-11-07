"""Isolate the experiment browser session from both existing shops."""
from baseline_settings import *  # noqa: F403

SESSION_COOKIE_NAME = "oscar_experiment_sessionid"
CSRF_COOKIE_NAME = "oscar_experiment_csrftoken"
