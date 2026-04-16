# AGENTS.md

This document defines the shared working rules for Blender add-on development.

## 1. Roles

- `AGENTS.md` defines the behavioral and workflow rules for development work.
- `SPEC.md` defines the shared specifications across Blender add-ons.
- `EACH_SPEC.md` defines the per-add-on specifications.

## 2. Priority

If rules or specifications conflict, apply them in the following order:

1. The user's explicit instructions in the current task
2. `EACH_SPEC.md`
3. `SPEC.md`
4. `AGENTS.md`

## 3. Core policy

- Do not change existing UI or behavior unless explicitly requested.
- Make the smallest safe change that satisfies the request.
- Do not refactor unrelated areas without explicit need.
- Do not change existing labels, button names, section names, or other visible wording unless explicitly requested.
- Do not remove existing convenience features unless explicitly requested.
- Before making changes, review both `SPEC.md` and `EACH_SPEC.md` for shared and per-add-on requirements.

## 4. Versioning rules

- Bump the version by `+0.001` for every code change.
- Keep version strings synchronized everywhere they are shown.
- At minimum, update the version in `bl_info`.
- If the UI displays the version, update those visible version strings as well.
- If you are only fixing a missed version label update after the fact, do not bump the version number again.

## 5. Packaging rules

- After each update, deliver the add-on as a ZIP package that can be installed directly into Blender.
- Keep the add-on in an installable and registerable state after changes.
- Do not deliver the add-on in a state where `register` fails.

## 6. Checks before editing

- Read `SPEC.md`.
- If `EACH_SPEC.md` exists, read it.
- If `EACH_SPEC.md` has not been created yet, first organize the design policy, UI structure, and core features.
- Once the design is settled, output it as `EACH_SPEC.md`.
- Do not start implementation before the per-add-on specification is defined.
- Inspect the current implementation before making changes.
- Prefer root-cause fixes whenever reasonably possible instead of temporary symptom-level fixes.

## 7. Checks after editing

At minimum, verify the following:

- The add-on registers without syntax errors.
- The target panels and primary UI are displayed.
- Version labels are updated wherever required.
- The main functionality still works.
- The existing workflow has not been changed unnecessarily.

## 8. Delivery response rules

- Whenever the add-on is updated, output it as a ZIP package.
- Briefly explain what was changed.
- State the new version clearly.
- Report implemented changes with numbered items.  
  Example: `1. Added the XXX button`, `2. Adjusted the YYY section`
- Report user test points with alphabetic labels.  
  Example: `A. Check whether the XXX button works`, `B. Check whether the UI layout is intact`
- Do not propose broad redesigns unless requested.
- Preserve the current workflow.
