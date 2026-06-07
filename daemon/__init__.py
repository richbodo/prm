"""The PRM local daemon.

A small stdlib ``http.server`` that serves the vanilla-JS workspace and a JSON API. In v0.1 M2 it is
**read-only** over ``shared.db`` (INV-2); the apply / proposal-review surface — where the daemon
becomes the *only* writer of canonical private data — arrives with M3. See
``plans/v0.1-implementation-plan.md`` §3 and §8.
"""

__all__ = ["server"]
