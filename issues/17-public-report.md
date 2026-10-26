# 17 · Public readiness report + launch

Status: blocked · Type: HITL · Label: needs-human · Branch: issue/17-public-report

## What to build

The artefact that gets Z.ai's attention: a "GLM-5.3 Production Readiness Report" on a real, public workload, plus a
launch-ready repo.

**Needs the owner:**
- choice of workload (e.g. opencode + GitHub MCP, or a demo golden set);
- approval before anything is published, pushed, or filed upstream;
- the outreach messages.

- Run conformance, the bridge-on vs bridge-off comparison, migration eval vs an incumbent, cost forensics and an
  evidence pack on the chosen workload. Publish the dashboard and a written report.
- Prepare minimal reproductions for each confirmed upstream bug as ready-to-file issue drafts, for GLM-5,
  zai-coding-plugins, vLLM, SGLang and harnesses. Filing waits for the owner.
- Repo polish: README with a 2-minute quickstart, architecture page, contribution guide, demo GIF/screenshots,
  release tag. Pushing and making the repo public wait for the owner.
- Draft a three-line message to Carol Lin and a scorecard post for Zixuan Li, for the owner to send.

## Acceptance criteria

- [ ] Report numbers each link to a run id in the published results.
- [ ] Upstream issue drafts each contain a minimal repro that fails against the real endpoint (from slice 16 runs).
- [ ] The owner has approved publish, push and outreach; nothing went out without it.

## Blocked by

- All other issues

## Evidence
