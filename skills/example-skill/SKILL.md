---
name: example-skill
description: Placeholder skill template. Replace it with one focused project workflow before relying on it; do not invoke it for real work.
---

# Example Skill

This is a placeholder skill that ships with the template. Replace this
file (and the directory name `example-skill`) with a project-specific
skill before relying on it.

## What goes here

A skill is a directory containing `SKILL.md` with required `name` and
`description` frontmatter followed by instructions an agent follows when the
skill is invoked. Add `scripts/`, `references/`, or `assets/` only when the
workflow needs them.

The `description` field is used for discovery. Keep it concise and specific
about the requests the skill handles.

## How to add a new skill

1. Create `skills/<skill-name>/SKILL.md` with frontmatter and body.
2. Add discovery symlinks for the supported agents:

   ```bash
   ln -s ../../skills/<skill-name> .agents/skills/<skill-name>
   ln -s ../../skills/<skill-name> .claude/skills/<skill-name>
   ```
3. If the skill should ship through the Claude plugin, add its canonical
   `./skills/<skill-name>` path to `.claude-plugin/marketplace.json`.
