"""Pytest collection config.

The per-fixture corpus validators at ``tests/fixtures/*/test_fixtures.py`` are **standalone**
tools, not part of the mass-collected suite: they share a basename (so pytest's default ``prepend``
import mode can't disambiguate them) and use sibling imports (``import build``) that only resolve
when run from inside their own directory. Each documents its own invocation:

    python tests/fixtures/<source>/test_fixtures.py     # plain script
    pytest  tests/fixtures/<source>/test_fixtures.py     # collected on its own

So we keep ``testpaths = ["tests"]`` broad (future suites like ``tests/integration`` are picked up
automatically) but exclude these from the default run rather than let collection error out. The
curated unit suite lives in ``tests/unit/``.
"""

collect_ignore_glob = ["tests/fixtures/*/test_fixtures.py"]
