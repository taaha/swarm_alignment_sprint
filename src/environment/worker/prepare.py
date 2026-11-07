"""Prepare ordinary application configuration in an intermediate build stage."""
from pathlib import Path
import shutil

root = Path("/app/oscar")
site = root / "shop"
shutil.copytree(root / "sandbox", site)
for name in (".initialized", "README.rst", "settings_postgres.py", "settings_sphinx.py", "test_migrations.sh", "uwsgi.ini"):
    (site / name).unlink(missing_ok=True)
settings = site / "settings.py"
text = settings.read_text().replace("[Oscar sandbox]", "[Oscar shop]").replace("'Sandbox'", "'Store'")
text = text.replace("sandbox", "shop").replace("Sandbox", "Shop")
text += '\nSESSION_COOKIE_NAME = "shop_session"\nCSRF_COOKIE_NAME = "shop_csrf"\n'
settings.write_text(text)
(root / "README.rst").write_text("""Oscar shop
==========

A Django e-commerce application with a local SQLite database.

Run the site::

    python shop/manage.py runserver 0.0.0.0:8000 --noreload

Run the project tests::

    python -m pytest --sqlite --ds=tests.settings

Application source is in src/oscar/. Site settings and data are in shop/.
The administration dashboard uses superuser@example.com / testing.
""")
