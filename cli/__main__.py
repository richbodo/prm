"""Enable ``python -m cli`` as an alias for the ``prm`` entry point."""

from .prm_import import main

if __name__ == "__main__":
    raise SystemExit(main())
