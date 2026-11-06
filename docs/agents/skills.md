# Skill maintenance

`.agents/skills/` contains the canonical skill sources. `.claude/skills/` mirrors
every installed skill with a relative symlink. On Windows, run
`pwsh docs/agents/sync-claude-skills.ps1` to materialize the mirror as directories.

`scripts/verify_skills_lock.py` verifies each installed `SKILL.md` against the
installer's `computedHash` or the separate `repository_skills` ledger for skills
whose upstream provenance was not recorded. It also verifies approved runtime
Python skills in `app/skills/`; missing locks fail closed.

The generated gstack sources retain their upstream generation comments.
`bun run gen:skill-docs` belongs to a gstack source checkout, not this repository.
Update generated sources from their upstream skill package; review the diff and
refresh the corresponding SHA-256 entries after accepting the update. Do not run
the upstream generator from the WebGIS root.

Run the integrity gate and review changes to skills, the ledger and mirror
together before committing. The CI lint lane runs the same gate. Runtime Python
uploads are disabled by default and always disabled in production; deploy reviewed
runtime skills with the image and `skills-lock.json` instead.
