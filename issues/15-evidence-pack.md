# 15 · Evidence pack generator

Status: in-progress · Type: AFK · Label: ready-for-agent · Branch: issue/15-evidence-pack

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

- [ ] Four example deployment descriptors (one per path) produce packs; the Bedrock and Z.ai-API paths
      automatically mark EU data residency as `gap` with the reason.
- [ ] Every checklist item has a citation (URL + date or article number); a test fails on an item without one.
- [ ] The AI-BOM validates with the CycloneDX schema/tool.
- [ ] The disclaimer is on every generated page (test).
- [ ] Evidence tab renders with fixture results; token check passes; no console errors at 1440/390 px.

## Blocked by

- 01 Foundation

## Evidence
