# 15 · Evidence pack generator

Status: review · Type: AFK · Label: ready-for-agent · Branch: issue/15-evidence-pack

## What to build

`glmkit evidence build --deployment <file> --results results/ -o evidence/` generates the document set a skeptical
European or Singaporean security and compliance team needs before approving a GLM deployment. It answers the EU AI
Act, export-control and data-residency questions left unanswered under Z.ai's Enterprise 100 launch. It is
**framed as a checklist and evidence index, never legal advice**; that disclaimer is on every page. Sources are in
`research/04-deploy-compliance.md` §3–§6.

- **Deployment descriptor** (YAML): deployment path, model and version, where data flows, which personal data,
  retention, sub-processors. Paths:
  - self-hosted (which provider and region);
  - Mistral regional;
  - Bedrock (note: cross-region only, no EU geo profile);
  - Z.ai API (Singapore processing).
- Generated outputs:
  - **data-flow diagram** (Mermaid + rendered SVG);
  - **checklist**: EU AI Act deployer and fine-tuner duties with article citations and dates in force after the
    Digital Omnibus (Reg. (EU) 2026/1744); GDPR Art. 35 DPIA prompts; Singapore PDPA and the Model AI Governance
    Framework; US EAR hosted-vs-self-hosted path questions. Each item is marked met / gap / n/a / needs-counsel,
    with an evidence link;
  - **license notes**: GLM-5.3's MIT-style license with an added Z.ai security-review clause for model-as-a-service
    operators with over $10B revenue. Flag it as needs-counsel for the AI Act Art. 53(2) open-source exemption;
  - **CycloneDX AI-BOM**: emit 1.6, validate against 1.7; optional SPDX 3.0 AI export;
  - **evidence index** linking the conformance, eval, guard and deploy results in `results/` by run id, so claims
    point at reproducible runs.
- Output as a static HTML pack (Observatory style) plus Markdown.
- Dashboard: an **Evidence** tab with checklist status by framework (stacked bars), gaps first, and links to the
  evidence runs.

## Acceptance criteria

- [x] Four example deployment descriptors (one per path) produce packs; the Bedrock and Z.ai-API paths
      automatically mark EU data residency as `gap` with the reason.
- [x] Every checklist item has a citation (URL + date or article number); a test fails on an item without one.
- [x] The AI-BOM validates with the CycloneDX schema/tool.
- [x] The disclaimer is on every generated page (test).
- [x] Evidence tab renders with fixture results; token check passes; no console errors at 1440/390 px.

## Blocked by

- 01 Foundation

## Evidence

Commands run from the worktree root; `$SCRATCH` is a scratch folder outside the repo. Fixture results: `glmkit conform
run --target mock --cases 'WIRE-00*' -o $SCRATCH/results` plus one fixture envelope each for eval, guard and deploy.

**Four descriptors, four packs.** `uv run glmkit evidence build -d src/glmkit/evidence/examples/<name>.yaml -r $SCRATCH/results -o $SCRATCH/pack-<name> --spdx`
(examples: `uv run glmkit evidence examples`) gave 45 items each and a valid AI-BOM:
```
bedrock          met=6  gap=19 needs-counsel=14 n/a=6   RES-1 gap: no EU geo profile, cross-Region only
self-hosted-eu   met=12 gap=15 needs-counsel=14 n/a=4   RES-1 met (declared EU hosting)
mistral-regional met=6  gap=18 needs-counsel=15 n/a=6   RES-1 gap: GLM-5.3 on api.eu.mistral.ai unverified
zai-api          met=6  gap=18 needs-counsel=16 n/a=5   RES-1 gap: no EU region found
```

**Planted-failure proofs** (each reverted afterwards):
```
### 1 citation: plant EU-B1 with no citations
### 2 disclaimer: remove {{DISCLAIMER}} from html template
E           glmkit.evidence.render.MissingDisclaimer: page 'Evidence pack overview' (html) has no disclaimer; every generated page must carry it
E           glmkit.evidence.render.MissingDisclaimer: page 'Evidence pack overview' (html) has no disclaimer; every generated page must carry it
E           glmkit.evidence.render.MissingDisclaimer: page 'Evidence pack overview' (html) has no disclaimer; every generated page must carry it
E           glmkit.evidence.render.MissingDisclaimer: page 'Evidence pack overview' (html) has no disclaimer; every generated page must carry it
FAILED tests/test_evidence.py::test_disclaimer_on_every_generated_page[bedrock]
FAILED tests/test_evidence.py::test_disclaimer_on_every_generated_page[mistral-regional]
### 3 AI-BOM: plant invalid field in a real emitted BOM
$SCRATCH/pack-bedrock/ai-bom.cdx.json: valid
exit=0
cyclonedx-1.6: $: Additional properties are not allowed ('bogus' was unexpected)
cyclonedx-1.6: $.components[0].type: 'not-a-type' is not one of ['application', 'framework', 'library', 'container', 'platform', 'operating-system', 'device', 'device-driver', 'firmware', 'file', 'machine-learning-model', 'data', 'crypto
cyclonedx-1.7: $: Additional properties are not allowed ('bogus' was unexpected)
cyclonedx-1.7: $.components[0].type: 'not-a-type' is not one of ['application', 'framework', 'library', 'container', 'platform', 'operating-system', 'device', 'device-driver', 'firmware', 'file', 'machine-learning-model', 'data', 'crypto
$SCRATCH/bad-bom.json: 4 error(s)
exit=1
### 4 token check: plant raw colour in evidence.css
built $SCRATCH/site-bad/index.html  tabs=runs,conformance,evidence  runs=8
$SCRATCH/site-bad/index.html:71 raw-color #ff0000
token check: 1 finding(s)
exit=1
built $SCRATCH/site/index.html  tabs=runs,conformance,evidence  runs=8
token check: 0 finding(s)
exit=0
### 1 (redo) citation: EU-B1 planted with citations=()
E           glmkit.evidence.checklist.CatalogueError: checklist catalogue invalid: EU-B1: no citation
E           glmkit.evidence.checklist.CatalogueError: checklist catalogue invalid: EU-B1: no citation
E           glmkit.evidence.checklist.CatalogueError: checklist catalogue invalid: EU-B1: no citation
E           glmkit.evidence.checklist.CatalogueError: checklist catalogue invalid: EU-B1: no citation
E           glmkit.evidence.checklist.CatalogueError: checklist catalogue invalid: EU-B1: no citation
FAILED tests/test_evidence.py::test_each_example_builds_a_pack[bedrock] - glm...
```

**Tests and lint.** `uv run pytest -q` all green (includes `tests/test_evidence.py`, 31 cases); `uv run ruff check` all checks passed.

**Dashboard.** `uv run glmkit report build $SCRATCH/results -o $SCRATCH/site` shows tabs runs, conformance, evidence with
`token check: 0 finding(s)`. `node tools/site-smoke/smoke.mjs $SCRATCH/site/index.html --out $SCRATCH/shots` (Chrome headless,
playwright-core installed in scratch) reports `"ok": true`: no console errors, page errors or failed requests at 1440 and
390 px, no horizontal scroll at 390 px, the Evidence drawer opens and closes with focus returned. Screenshots viewed:
stacked bars per framework, gaps first table, evidence run list, deployment switcher. The pack pages themselves also pass
the token check (reported by `evidence build`) and were screenshot-checked at 1440 px.

**Notes and unverified items.**
- Mermaid CLI was not used (needs a browser download). The pack ships Mermaid source plus a built-in SVG renderer for the
  fixed three-column data-flow shape (`src/glmkit/evidence/dataflow.py`).
- CycloneDX 1.6 is emitted and validated against the vendored 1.6 and 1.7 schemas (plus spdx, jsf, cryptography-defs
  refs, Apache-2.0 LICENSE) in `src/glmkit/evidence/vendor/cyclonedx/`. SPDX 3.0 export (`--spdx`) is minimal and not schema-validated.
- Research flags are kept in item `flags` (e.g. Commission signatory page read via summariser, Bedrock prices from
  trackers, NatLawReview byline unconfirmed, GPAI modifier threshold wording, Z.ai authorised representative not found).
- Statuses for hosted-path facts rest on the research file dated 2026-10-26; re-pull from providers before relying on them.
- The evidence build writes a result envelope (component `evidence`, item status map met=pass, gap=fail,
  needs-counsel=soft_fail, n/a=skip) into `--results` so the dashboard tab can read it. This uses only existing optional
  `data` fields; no contract change.
