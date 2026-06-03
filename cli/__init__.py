"""PRM command-line ingestor (v0.1).

The ingestor is the *only* writer of the raw Shared DB. Its pipeline is three layers:

    parsers/  — source bytes -> source-native ``ParsedRecord``s (one per record, library-agnostic).
    normalize — ``ParsedRecord`` -> canonical jCard (RFC 7095) + per-field provenance + stable id.
    (load)    — canonical contacts -> shared.db  (a later milestone; see the v0.1 plan §11).

This package is the parser + normalize layer. See ``cli/README.md`` and
``plans/v0.1-implementation-plan.md`` §5.
"""

__all__ = ["jcard", "provenance", "identity", "normalize", "parsers"]
