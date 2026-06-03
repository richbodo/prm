# incoming/ — drop real exports here (never committed)

This directory is a staging area for **real** contact exports — Google Takeout zips, vendor CSVs,
`.vcf` files from iCloud/Fastmail/Outlook, etc. Use them to understand a source format's real-world
quirks so you can build faithful **synthetic** fixtures elsewhere under `tests/fixtures/`.

**Nothing in here is committed.** The `.gitignore` in this directory ignores everything except
itself and this README, so personal contact data cannot be committed by accident (this protects
INV-1: private data stays on the device). Do not remove that `.gitignore`.

Workflow: drop a real export here → inspect it → encode its quirks in a generator that emits
synthetic fixtures → leave the real export here (untracked) or delete it.
