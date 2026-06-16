# Prime
> Read-only orientation: understand the codebase and where you are, then
> summarize. Do NOT switch branches, pull, or create a worktree while priming —
> just orient. (See CLAUDE.md § Conventions for when to branch / worktrees.)

## Run
git ls-files
git status -sb          # current branch + ahead/behind + dirty state
git worktree list       # sibling worktrees other agents may be using

## Read
CLAUDE.md
README.md
docs/users-guide.md
docs/prm-feature-spec.md
docs/roadmap.md
plans/v0.1-implementation-plan.md
cli/prm_import.py
core/*
