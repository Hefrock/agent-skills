#!/usr/bin/env python3
"""CI guard for repo metadata that no skill's own test suite covers.

Fails (exit 1) when:
  - a SKILL.md or agents/*.md frontmatter is missing or is not strict YAML
    (an unquoted ": " inside a description is the usual cause -- lenient
    loaders then drop every field, including the trigger description);
  - a skill's `name` does not match its folder, or `description` is empty;
  - a skill folder is not registered in .claude-plugin/marketplace.json, or
    the marketplace lists a skill/agent path that does not exist;
  - a skill description is longer than the Agent Skills standard's
    1024-character limit;
  - a skills/**/test_*.py file is not run by .github/workflows/ci.yml;
  - a ${CLAUDE_SKILL_DIR}/... or ${CLAUDE_PLUGIN_ROOT}/... reference in a
    skill or agent file does not resolve to a real path. SKILL_DIR is the
    skill's own folder; PLUGIN_ROOT is the shared skills/ folder (every
    plugin's "source"), so it needs the skill's folder name in the path.
    ${CLAUDE_SKILL_DIR} in an agents/*.md file is also an error, since it
    is only substituted in skill content.

Needs PyYAML. Run from anywhere: paths resolve from the repo root.
"""
import glob
import json
import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
MAX_DESCRIPTION = 1024
PATH_REF = re.compile(r"\$\{(CLAUDE_SKILL_DIR|CLAUDE_PLUGIN_ROOT)\}/([^\s`)\"',]+)")


def check_path_refs(md: Path, skill: Path, skills_dir: Path, root: Path):
    """Yield an error for each substituted-path reference that cannot resolve."""
    is_agent = md.parent.name == "agents"
    for lineno, line in enumerate(md.read_text(encoding="utf-8").split("\n"), 1):
        for var, rest in PATH_REF.findall(line):
            where = f"{md.relative_to(root)}:{lineno}"
            if var == "CLAUDE_SKILL_DIR" and is_agent:
                yield f"{where}: ${{CLAUDE_SKILL_DIR}} is not substituted in agent files; use ${{CLAUDE_PLUGIN_ROOT}}/{skill.name}/..."
                continue
            rest = rest.rstrip(".:;")
            base = skill if var == "CLAUDE_SKILL_DIR" else skills_dir
            target = base / rest
            if "<" in rest:  # a placeholder such as <persona-slug>.md: check its folder
                ok = (base / rest.split("<", 1)[0]).parent.exists() if not rest.split("<", 1)[0].endswith("/") else (base / rest.split("<", 1)[0]).exists()
            elif "*" in rest:
                ok = bool(glob.glob(str(target)))
            else:
                ok = target.exists()
            if not ok:
                yield f"{where}: ${{{var}}}/{rest} does not resolve (looked in {base.relative_to(root)}/)"


def read_frontmatter(path: Path):
    """Return (mapping, None) or (None, error message)."""
    lines = path.read_text(encoding="utf-8").split("\n")
    if not lines or lines[0].strip() != "---":
        return None, "first line is not '---'"
    try:
        end = next(i for i, line in enumerate(lines[1:], 1) if line.strip() == "---")
    except StopIteration:
        return None, "no closing '---'"
    try:
        data = yaml.safe_load("\n".join(lines[1:end]))
    except yaml.YAMLError as exc:
        return None, "invalid YAML: " + " ".join(str(exc).split())
    if not isinstance(data, dict):
        return None, "frontmatter is not a mapping"
    return data, None


def check(root: Path = ROOT):
    errors, warnings = [], []
    skills_dir = root / "skills"
    skill_dirs = sorted(p for p in skills_dir.iterdir() if p.is_dir())

    for skill in skill_dirs:
        skill_md = skill / "SKILL.md"
        rel = skill_md.relative_to(root)
        if not skill_md.is_file():
            errors.append(f"{rel}: missing")
            continue
        for md in [skill_md, *sorted(skill.glob("agents/*.md"))]:
            mrel = md.relative_to(root)
            fm, err = read_frontmatter(md)
            if err:
                errors.append(f"{mrel}: {err}")
                continue
            desc = fm.get("description")
            if not isinstance(desc, str) or not desc.strip():
                errors.append(f"{mrel}: description missing or empty")
            elif md == skill_md and len(desc) > MAX_DESCRIPTION:
                errors.append(
                    f"{mrel}: description is {len(desc)} chars (standard limit {MAX_DESCRIPTION})"
                )
            if md == skill_md and fm.get("name") != skill.name:
                errors.append(f"{mrel}: name {fm.get('name')!r} does not match folder {skill.name!r}")

    for skill in skill_dirs:
        for md in sorted(skill.rglob("*.md")):
            errors.extend(check_path_refs(md, skill, skills_dir, root))

    market = json.loads((root / ".claude-plugin" / "marketplace.json").read_text(encoding="utf-8"))
    registered = set()
    for plugin in market.get("plugins", []):
        source = root / plugin.get("source", ".")
        for s in plugin.get("skills", []):
            target = source / s
            if not (target / "SKILL.md").is_file():
                errors.append(f"marketplace.json: plugin {plugin.get('name')!r} lists missing skill {s}")
            else:
                registered.add(target.resolve())
        for a in plugin.get("agents", []):
            if not (source / a).is_file():
                errors.append(f"marketplace.json: plugin {plugin.get('name')!r} lists missing agent {a}")
    for skill in skill_dirs:
        if skill.resolve() not in registered:
            errors.append(f"skills/{skill.name}: not registered in marketplace.json")

    ci = (root / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    for test in sorted(skills_dir.rglob("test_*.py")):
        rel = test.relative_to(root).as_posix()
        if rel not in ci:
            errors.append(f"{rel}: test suite is not run by .github/workflows/ci.yml")

    return errors, warnings


def main() -> int:
    errors, warnings = check()
    for w in warnings:
        print(f"::warning::{w}")
    for e in errors:
        print(f"::error::{e}")
    if errors:
        print(f"FAILED: {len(errors)} metadata problem(s)")
        return 1
    print(f"OK: skill frontmatter, path references, marketplace registration and CI test wiring ({len(warnings)} warning(s))")
    return 0


if __name__ == "__main__":
    sys.exit(main())
