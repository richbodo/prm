"""Core stores and substrate for PRM.

`shared_db` is the raw mirrored-contacts store (the only thing the ingester writes). `lock` is the
single-instance advisory file-lock (AC-PRM-C) guarding the canonical writers. The private store,
proposals, snapshots, and audit log arrive with later milestones (plan §11 M3+).
"""

__all__ = ["shared_db", "lock"]
