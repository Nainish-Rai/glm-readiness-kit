# ruff: noqa: E501
"""The checklist catalogue and its resolution against a deployment descriptor.

Rules this module enforces (and tests prove):
* every item carries at least one valid citation (article number, or URL + date);
* an item that depends on legal interpretation is never ``met`` without counsel review (``needs-counsel``);
* ``met`` always has an evidence link (a run id, a descriptor field, an attestation reference or a generated file);
* facts the research settles (Bedrock has no EU geo profile; the Z.ai API is not EU-resident) are ``gap`` and
  cannot be overridden by an attestation.
Sources: research/04-deploy-compliance.md and zai-research/sources/enterprise.md. Where the research flags a
point as unverified, the item keeps that flag in ``flags``.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from glmkit.evidence.descriptor import ChecklistStatus, Deployment
from glmkit.evidence.index import IndexedRun, runs_for

R = "2026-10-26"  # research / retrieval date
OMNIBUS = "Reg. (EU) 2026/1744 (Digital Omnibus on AI; published 24 Jul 2026, in force 27 Jul 2026)"
ART = "https://artificialintelligenceact.eu/article/{}/"
OMNI_URL = "https://www.whitecase.com/insight-alert/eu-ai-omnibus-enters-force-amending-ai-act"
EURLEX = "https://eur-lex.europa.eu/ (OJ:L_202601744)"
NATLAW = "https://natlawreview.com/article/choosing-between-us-and-chinese-ai-models-export-control-risks-both-sides"
HF_LICENSE = "https://huggingface.co/zai-org/GLM-5.3/raw/main/LICENSE"
AWS_CARD = "https://docs.aws.amazon.com/bedrock/latest/userguide/model-card-zai-glm-5-3.html"
MISTRAL_REGIONAL = "https://docs.mistral.ai/inference/regional-inference"
GPAI_GUIDE = "https://artificialintelligenceact.eu/gpai-guidelines-overview/"
IMDA_MGF = "https://singaporelegaladvice.com/singapore-new-agentic-ai-governance-framework/"
PDPC = "https://www.allenandgledhill.com/sg/publication/articles/33390/pdpc-issues-advisory-guidelines-on-use-of-personal-data-in-generative-ai"
OMNIBUS_CAVEAT = (
    "Article text was confirmed at artificialintelligenceact.eu, which may predate the Omnibus amendments; "
    f"verify amended wording on EUR-Lex ({EURLEX})."
)

FRAMEWORKS: dict[str, dict[str, str]] = {
    "eu-ai-act": {"title": "EU AI Act", "jurisdiction": "EU"},
    "gdpr": {"title": "GDPR (DPIA and related)", "jurisdiction": "EU"},
    "residency": {"title": "Data residency", "jurisdiction": "EU"},
    "singapore": {"title": "Singapore (PDPA, Model AI Governance Framework)", "jurisdiction": "SG"},
    "us-ear": {"title": "US export controls (EAR)", "jurisdiction": "US"},
    "license": {"title": "GLM-5.3 license", "jurisdiction": ""},
    "governance": {"title": "Governance artifacts (kit-generated)", "jurisdiction": ""},
}
STATUS_ORDER = ("gap", "needs-counsel", "met", "n/a")  # gaps first


class CatalogueError(ValueError):
    pass


@dataclass(frozen=True)
class Citation:
    label: str
    article: str | None = None
    url: str | None = None
    date: str | None = None
    reliability: str = "S"  # P primary read, S secondary, U unverified (research evidence labels)

    @property
    def valid(self) -> bool:
        return bool(self.article) or bool(self.url and self.date)

    def as_dict(self) -> dict[str, Any]:
        return {k: v for k, v in self.__dict__.items() if v is not None}


@dataclass
class EvidenceLink:
    kind: str  # run | descriptor | attestation | generated
    ref: str
    label: str = ""

    def as_dict(self) -> dict[str, str]:
        return self.__dict__.copy()


@dataclass
class Res:
    status: ChecklistStatus
    reason: str
    evidence: list[EvidenceLink] = field(default_factory=list)
    locked: bool = False  # settled by research facts: an attestation cannot change it


@dataclass
class Ctx:
    dep: Deployment
    runs: list[IndexedRun]
    generated: dict[str, str]  # artifact id -> file name, for kit-generated evidence
    bom_valid: bool = False


@dataclass(frozen=True)
class Item:
    id: str
    framework: str
    title: str
    prompts: tuple[str, ...]
    citations: tuple[Citation, ...]
    default: ChecklistStatus = "gap"
    default_reason: str = "No evidence recorded."
    interpretive: bool = False
    in_force: str | None = None
    evidence_components: tuple[str, ...] = ()
    flags: tuple[str, ...] = ()
    rule: Callable[[Ctx], Res | None] | None = None


@dataclass
class Resolved:
    id: str
    framework: str
    title: str
    prompts: list[str]
    citations: list[dict[str, Any]]
    in_force: str | None
    interpretive: bool
    flags: list[str]
    status: ChecklistStatus
    reason: str
    evidence: list[EvidenceLink]
    attested: bool = False

    def as_dict(self) -> dict[str, Any]:
        d = dict(self.__dict__)
        d["evidence"] = [e.as_dict() for e in self.evidence]
        return d


def _dc(path: str, label: str = "") -> EvidenceLink:
    return EvidenceLink("descriptor", f"deployment.yaml#{path}", label or path)


def _art(n: str, title: str = "") -> Citation:
    return Citation(
        label=f"Reg. (EU) 2024/1689 Art. {n}" + (f" ({title})" if title else ""),
        article=f"Art. {n}",
        url=ART.format(n),
        date=R,
        reliability="P",
    )


# ---------------------------------------------------------------------------------------------- rules


def _is_ft(c: Ctx) -> bool:
    return c.dep.fine_tuning.performed


def _c1(c: Ctx) -> Res | None:
    if not _is_ft(c):
        return Res("n/a", "No fine-tuning or other modification is declared (fine_tuning.performed is false).")
    return None


def _c2(c: Ctx) -> Res | None:
    ft = c.dep.fine_tuning
    if not ft.performed:
        return Res("n/a", "No fine-tuning declared.")
    est = (
        f" Estimate 6 x P_act x tokens = {6 * c.dep.model.active_params * ft.tokens:.2e} FLOP (heuristic, not measured)."
        if ft.tokens
        else ""
    )
    if ft.gpu_hours is not None or ft.tokens:
        return Res(
            "met",
            f"Fine-tuning compute recorded: gpu_hours={ft.gpu_hours}, tokens={ft.tokens}.{est}",
            [_dc("fine_tuning", "fine-tuning record")],
        )
    return Res("gap", "Fine-tuning declared but neither gpu_hours nor tokens recorded.")


def _res1(c: Ctx) -> Res | None:
    d = c.dep
    if not d.requirements.eu_data_residency:
        return Res(
            "n/a", "The deployment does not require EU data residency (requirements.eu_data_residency is false)."
        )
    h = d.hosting
    if d.path == "bedrock":
        return Res(
            "gap",
            "Bedrock GLM 5.3 is cross-Region inference only: geo profile us.zai.glm-5.3 (US source Regions) and "
            "global profile global.zai.glm-5.3 (callable from 31 Regions including EU Regions); there is no EU geo "
            "profile and no in-Region option, so EU-only processing cannot be met today "
            f"(AWS model card, read {R}; descriptor profile: {h.bedrock_profile}).",
            [_dc("hosting.bedrock_profile")],
            locked=True,
        )
    if d.path == "zai-api":
        return Res(
            "gap",
            "Z.ai first-party API: no EU region found; research 04 §3 describes it as China-operated while the "
            "enterprise research reports Singapore processing per Z.ai's DPA (a source discrepancy; the DPA text "
            "was not read). Neither is EU-only processing.",
            [_dc("hosting.region")],
            locked=True,
        )
    if d.path == "mistral-regional":
        if h.mistral_endpoint != "eu":
            return Res(
                "gap",
                f"Endpoint '{h.mistral_endpoint}' is not the EU regional endpoint.",
                [_dc("hosting.mistral_endpoint")],
                locked=True,
            )
        if not h.glm_on_endpoint_verified:
            return Res(
                "gap",
                "Unverified: Mistral serves GLM-5.3 (GA 2026-09-15) but regional endpoints serve only models "
                "hosted in that region and the pages read do not state GLM-5.3 is on api.eu.mistral.ai. Run "
                "models.list against api.eu.mistral.ai and set hosting.glm_on_endpoint_verified.",
                [_dc("hosting.glm_on_endpoint_verified")],
            )
        return Res(
            "met",
            "EU regional endpoint declared and GLM-5.3 verified in models.list (declared in descriptor; "
            "retain the models.list output).",
            [_dc("hosting.glm_on_endpoint_verified")],
        )
    if h.jurisdiction == "EU":
        return Res(
            "met",
            f"Inference runs on infrastructure declared in the EU ({h.provider}, {h.region}). "
            "Declared in the descriptor; verify against the provider contract.",
            [_dc("hosting.jurisdiction")],
        )
    return Res("gap", f"Declared hosting jurisdiction is {h.jurisdiction}, not EU.", [_dc("hosting.jurisdiction")])


def _res2(c: Ctx) -> Res | None:
    d = c.dep
    if not d.requirements.eu_data_residency:
        return Res("n/a", "The deployment does not require EU data residency.")
    if d.path == "mistral-regional":
        return Res(
            "needs-counsel",
            "Mistral: the control plane (billing, keys, metadata) is not regional, and limited safeguarded "
            "transfers to sub-processors outside the region may occur. Whether that fits the residency "
            "commitment is for counsel.",
            [_dc("sub_processors")],
        )
    outside = [s.name for s in d.sub_processors if s.outside_eea]
    unknown = [s.name for s in d.sub_processors if s.outside_eea is None]
    if outside:
        return Res("gap", f"Sub-processors declared outside the EEA: {', '.join(outside)}.", [_dc("sub_processors")])
    if unknown:
        return Res(
            "needs-counsel",
            f"Location of sub-processors not established: {', '.join(unknown)}.",
            [_dc("sub_processors")],
        )
    if not d.sub_processors:
        return Res("gap", "No sub-processors recorded; record them, or attest that there are none.")
    return Res("met", "All declared sub-processors are inside the EEA.", [_dc("sub_processors")])


def _res3(c: Ctx) -> Res | None:
    d = c.dep
    if not d.requirements.sg_processing:
        return Res("n/a", "Singapore processing is not a requirement of this deployment (requirements.sg_processing).")
    h = d.hosting
    if d.path == "zai-api":
        return Res(
            "needs-counsel",
            "Z.ai's own API is reported as Singapore-processed per its DPA (TechRepublic, 2026-06/07, via "
            "enterprise research); the DPA text was not read, and the operator group's IP owner is "
            "Entity-Listed. Verify the DPA.",
            [_dc("hosting.region")],
        )
    if d.path == "bedrock":
        return Res(
            "gap",
            "The global profile can be called from ap-southeast-1, but processing is not pinned to "
            "Singapore; no in-Region option.",
            [_dc("hosting.bedrock_profile")],
            locked=True,
        )
    if d.path == "mistral-regional":
        return Res(
            "gap",
            "Mistral offers EU, US and global endpoints; no Singapore endpoint found in the sources.",
            [_dc("hosting.mistral_endpoint")],
            locked=True,
        )
    if h.jurisdiction == "SG":
        return Res("met", "Inference declared on infrastructure in Singapore.", [_dc("hosting.jurisdiction")])
    return Res("gap", f"Declared hosting jurisdiction is {h.jurisdiction}, not SG.", [_dc("hosting.jurisdiction")])


def _gdpr4(c: Ctx) -> Res | None:
    if c.dep.path == "self-hosted" and not c.dep.sub_processors:
        return Res("n/a", "Self-hosted with no external processor declared.")
    return None


def _gdpr5(c: Ctx) -> Res | None:
    d = c.dep
    outside = [s.name for s in d.sub_processors if s.outside_eea is not False]
    if d.path == "self-hosted" and not outside:
        return Res("n/a", "Self-hosted; no sub-processor outside the EEA declared.")
    names = ", ".join(outside) or d.hosting.provider
    return Res(
        "needs-counsel",
        f"Transfer mechanism (GDPR Chapter V) for: {names}. Whether a transfer occurs and "
        "which mechanism applies is a legal determination.",
        [_dc("sub_processors")],
    )


def _lic2(c: Ctx) -> Res | None:
    d = c.dep
    if d.path != "self-hosted":
        return Res(
            "needs-counsel",
            "The clause binds a licensee running a MaaS business. Whether the operator "
            "holds the weights, or only the host does, depends on the contract.",
        )
    m = d.operator.maas_over_10b_revenue
    if m is False:
        return Res(
            "met",
            "Operator declares no MaaS business over US$10B revenue (with affiliates, 12 months). Self-declared.",
            [_dc("operator.maas_over_10b_revenue")],
        )
    if m is True:
        return Res(
            "gap",
            "Operator declares a MaaS business over US$10B: a Z.AI security review is required "
            "before commercial use (glmlicense@z.ai). No review recorded.",
            [_dc("operator.maas_over_10b_revenue")],
        )
    return Res("gap", "Operator MaaS status not declared (operator.maas_over_10b_revenue).")


def _ear2(c: Ctx) -> Res | None:
    if c.dep.path != "zai-api":
        return Res("n/a", "Not routed through the Z.ai first-party API.")
    return Res(
        "needs-counsel",
        "Hosted via the Z.ai first-party API: the counterparty is Entity-Listed (added "
        "16 Jan 2025, 90 FR 4619). Stop and escalate before any controlled technology can reach this endpoint.",
        [_dc("path")],
    )


def _ear3(c: Ctx) -> Res | None:
    if c.dep.path not in ("bedrock", "mistral-regional"):
        return Res("n/a", "Not routed through a US/EU cloud or reseller.")
    return None


def _ear4(c: Ctx) -> Res | None:
    d = c.dep
    if d.path != "self-hosted":
        return Res("n/a", "Weights are not self-hosted on this path.")
    missing = [
        n
        for n, v in (
            ("weights.source_url", d.weights.source_url),
            ("weights.sha256", d.weights.sha256),
            ("serving.gpu_vendor", d.serving.gpu_vendor),
        )
        if not v
    ]
    if d.weights.outbound_telemetry is not False:
        missing.append("weights.outbound_telemetry=false")
    if missing:
        return Res("gap", f"Not recorded: {', '.join(missing)}.")
    gv = (d.serving.gpu_vendor or "").lower()
    if "huawei" in gv or "ascend" in gv:
        return Res(
            "needs-counsel",
            "Huawei Ascend hardware is a separate presumptive-violation topic (BIS May 2026 "
            "guidance, per NatLawReview); no use without review.",
            [_dc("serving.gpu_vendor")],
        )
    return Res(
        "met",
        "Download source, SHA-256, license text (see license notes), GPU vendor and no-outbound-telemetry "
        "are recorded.",
        [_dc("weights"), _dc("serving.gpu_vendor")],
    )


def _ear6(c: Ctx) -> Res | None:
    if c.dep.path != "self-hosted":
        return Res("n/a", "Weights are not self-hosted on this path.")
    return None


def _gov2(c: Ctx) -> Res | None:
    conf = runs_for(c.runs, ("conform",))
    ev = runs_for(c.runs, ("eval",))
    links = [EvidenceLink("run", r.run_id, f"{r.component} · {r.verdict}") for r in [*conf, *ev]]
    if not conf:
        return Res("gap", "No conformance run found in results/.", links)
    if conf[0].verdict in ("fail", "error"):
        return Res("gap", f"The latest conformance run {conf[0].run_id} has verdict {conf[0].verdict}.", links)
    extra = "" if ev else " No eval run found."
    return Res("met", f"Latest conformance run {conf[0].run_id} has verdict {conf[0].verdict}.{extra}", links)


def _gov3(c: Ctx) -> Res | None:
    g = runs_for(c.runs, ("guard",))
    d = runs_for(c.runs, ("deploy",))
    links = [EvidenceLink("run", r.run_id, f"{r.component} · {r.verdict}") for r in [*g, *d]]
    if g and d:
        return Res("met", f"Guard run {g[0].run_id} and deploy run {d[0].run_id} are linked.", links)
    missing = [n for n, x in (("guard", g), ("deploy", d)) if not x]
    return Res("gap", f"No {' or '.join(missing)} run found in results/.", links)


def _gen(key: str, what: str) -> Callable[[Ctx], Res | None]:
    def rule(c: Ctx) -> Res | None:
        f = c.generated.get(key)
        return Res("met", what, [EvidenceLink("generated", f, key)]) if f else None

    return rule


def _gov_bom(c: Ctx) -> Res | None:
    f = c.generated.get("ai-bom")
    if f and c.bom_valid:
        return Res(
            "met",
            "CycloneDX 1.6 AI-BOM generated and validated against the vendored 1.6 and 1.7 schemas.",
            [EvidenceLink("generated", f, "ai-bom")],
        )
    return Res("gap", "AI-BOM missing or failed validation.")


# ---------------------------------------------------------------------------------------------- catalogue

_GPAI_CARVEOUT = (
    "Commission GPAI guidelines (2025-07-18) as summarised at artificialintelligenceact.eu; the primary guideline "
    "text was not re-read and search snippets disagree on 'exceeds' vs 'at least'."
)

CATALOGUE: tuple[Item, ...] = (
    # ------------------------------------------------------------------ EU AI Act
    Item(
        "EU-A1",
        "eu-ai-act",
        "Role determination (deployer, system provider, GPAI model provider)",
        (
            "Which roles apply: deployer; provider of an AI system (GLM integrated into a system placed on the EU market "
            "under our name); provider of a GPAI model (only if we modify enough, see C1)?",
            "Art. 2 scope includes non-EU providers and deployers where the output is used in the EU.",
        ),
        (_art("3", "definitions"), _art("2", "scope")),
        "needs-counsel",
        "Role depends on how the system is placed on the market; decide with counsel.",
        True,
        f"As amended by {OMNIBUS}",
        flags=(OMNIBUS_CAVEAT,),
    ),
    Item(
        "EU-A2",
        "eu-ai-act",
        "Annex III and Art. 5 screening of the use case",
        (
            "Is the use case prohibited under Art. 5 (as amended by Reg. 2026/1744) or listed in Annex III?",
            "If Annex III: plan for Art. 26 deployer duties, Art. 27 FRIA (where applicable) and Art. 73 incident "
            "reporting; Annex III obligations apply from 2 Dec 2027, not now.",
        ),
        (
            _art("5", "prohibited practices"),
            Citation("Annex III", article="Annex III", url=OMNI_URL, date="2026-08-04", reliability="P"),
            _art("26", "deployer obligations"),
            _art("27", "FRIA"),
            _art("73", "serious incidents"),
        ),
        "needs-counsel",
        "Classification is a legal determination.",
        True,
        "Annex III stand-alone high-risk obligations: 2 Dec 2027; Annex I embedded: 2 Aug 2028",
        flags=(OMNIBUS_CAVEAT,),
    ),
    Item(
        "EU-A3",
        "eu-ai-act",
        "Art. 25: do we become the provider of a high-risk system?",
        (
            "Do we put our name on a high-risk system, make a substantial modification, or change the intended purpose "
            "to a high-risk one?",
        ),
        (_art("25", "responsibilities along the value chain"),),
        "needs-counsel",
        "Depends on the use case and on Annex III classification.",
        True,
    ),
    Item(
        "EU-B1",
        "eu-ai-act",
        "Art. 4 AI literacy measures for staff",
        (
            "Which measures support AI literacy of staff using the system? (Post-Omnibus wording is softened from "
            "'ensure' to 'take measures to support'; the Commission and Member States facilitate.)",
        ),
        (
            _art("4", "AI literacy"),
            Citation("Digital Omnibus summary", url=OMNI_URL, date="2026-08-04", reliability="P"),
        ),
        in_force="Art. 4 applies since 2 Feb 2025; wording changed by Reg. (EU) 2026/1744",
        flags=(OMNIBUS_CAVEAT,),
    ),
    Item(
        "EU-B2",
        "eu-ai-act",
        "Art. 5 prohibited practices and the CSAM / NCII output ban",
        (
            "Confirm no prohibited practice. Add output filters for the new ban on AI generating child sexual abuse "
            "material and non-consensual intimate imagery (from 2 Dec 2026).",
        ),
        (
            _art("5", "prohibited practices"),
            Citation("White & Case, Omnibus alert", url=OMNI_URL, date="2026-08-04", reliability="P"),
        ),
        in_force="Art. 5 since 2 Feb 2025; new CSAM/NCII ban from 2 Dec 2026",
    ),
    Item(
        "EU-B3",
        "eu-ai-act",
        "Art. 50 transparency",
        (
            "Are users told they interact with AI (50(1), provider duty)? Is synthetic text/audio/image/video marked "
            "machine-readably (50(2))? Deployer duties for deepfakes and AI-generated text published to inform the "
            "public, and for emotion recognition or biometric categorisation (50(3)-(4))?",
        ),
        (_art("50", "transparency obligations"),),
        "needs-counsel",
        "Which Art. 50 limbs apply to this system is an interpretation question.",
        True,
        "Art. 50 applies from 2 Aug 2026; watermarking grace for systems placed on the market before 2 Aug 2026 "
        "ends 2 Dec 2026",
        flags=(OMNIBUS_CAVEAT,),
    ),
    Item(
        "EU-B4",
        "eu-ai-act",
        "Upstream documentation from the model provider and host",
        (
            "Record what Z.ai (and Mistral or AWS as hosts) supplies. Art. 53(1)(b) documentation to downstream "
            "providers is owed by the GPAI model provider; the open-source exemption (Art. 53(2)) may remove it.",
        ),
        (_art("53", "obligations for GPAI providers"),),
        "gap",
        "No upstream documentation recorded.",
    ),
    Item(
        "EU-B5",
        "eu-ai-act",
        "Logging and monitoring we run ourselves",
        (
            "Hosted: what do the provider logs contain and what do we log? Self-hosted: our logs, retention, access "
            "control. Mistral's own audit advice: log hostname, model ID and request ID per call.",
        ),
        (
            Citation("Mistral regional inference", url=MISTRAL_REGIONAL, date=R, reliability="P"),
            _art("26", "deployer obligations"),
        ),
        "gap",
        "No logging evidence linked; link a guard or deploy run, or attest with a reference.",
        evidence_components=("guard", "deploy"),
    ),
    Item(
        "EU-C1",
        "eu-ai-act",
        "Fine-tuning: GPAI provider-status test",
        (
            "A downstream modifier becomes a GPAI model provider if the modification compute exceeds one-third of the "
            "original model's training compute (fallbacks: one-third of 10^23 FLOP, or of 10^25 FLOP for systemic-risk "
            "models). Minor changes typically do not. Obligations then cover only the modification.",
        ),
        (
            Citation("Commission GPAI guidelines overview", url=GPAI_GUIDE, date=R, reliability="S"),
            _art("51", "classification"),
        ),
        "needs-counsel",
        "Depends on measured compute and on the threshold wording.",
        True,
        flags=(_GPAI_CARVEOUT,),
        rule=_c1,
    ),
    Item(
        "EU-C2",
        "eu-ai-act",
        "Fine-tuning record: compute, dataset provenance, copyright policy",
        (
            "Record fine-tuning FLOP (estimate 6 x P_act x tokens, plus measured GPU-hours), dataset provenance and "
            "the copyright policy.",
        ),
        (
            Citation("Commission GPAI guidelines overview", url=GPAI_GUIDE, date=R),
            _art("53", "obligations for GPAI providers"),
        ),
        flags=("The FLOP formula is a rough estimate (heuristic), not a measurement.",),
        rule=_c2,
    ),
    Item(
        "EU-C3",
        "eu-ai-act",
        "Is the base model a systemic-risk GPAI model?",
        (
            "Presumption at 10^25 FLOP (Art. 51). The research's own back-of-envelope for GLM-5 (40B active x 28.5T "
            "tokens x 6, about 6.8e24 before post-training) is below 1e25, but it is an unverified estimate. Ask Z.ai "
            "or check whether it notified the Commission; no notification evidence was found.",
        ),
        (_art("51", "classification of GPAI models with systemic risk"),),
        "needs-counsel",
        "Classification is the Commission's and the provider's determination.",
        True,
        flags=("UNVERIFIED: compute estimate is the research author's, not a determination.",),
    ),
    Item(
        "EU-C4",
        "eu-ai-act",
        "Art. 54 authorised representative of the model provider",
        (
            "Third-country GPAI providers must appoint an authorised representative in the Union unless exempt. "
            "Check whether Z.ai has one.",
        ),
        (_art("54", "authorised representatives"),),
        "needs-counsel",
        "Exemption analysis is legal; no evidence of a Z.ai representative was found in the sources.",
        True,
        flags=("UNVERIFIED: nothing found on a Z.ai authorised representative.",),
    ),
    Item(
        "EU-C5",
        "eu-ai-act",
        "Art. 53(2) open-source exemption and the GLM-5.3 license",
        (
            "The exemption requires a free and open-source license permitting use, access, modification and "
            "redistribution without monetisation. The GLM-5.3 license adds a Model-as-a-Service clause (over US$10B "
            "revenue needs a Z.AI security review) and 'comply with applicable laws'. Whether that defeats the "
            "exemption is a question for counsel. See the license notes page.",
        ),
        (
            _art("53", "paragraph 2, open-source exemption"),
            Citation("GLM-5.3 LICENSE", url=HF_LICENSE, date=R, reliability="P"),
        ),
        "needs-counsel",
        "Open question 6 of the research; decide with counsel.",
        True,
    ),
    Item(
        "EU-D1",
        "eu-ai-act",
        "Upstream signatory status: GPAI Code of Practice",
        (
            "Z.ai / Zhipu is not on the Commission's signatory list (update of 7 Oct 2026, 22 signatories). "
            "Non-signatories must independently demonstrate compliance and expect more AI Office scrutiny. Mistral and "
            "Amazon signed, but they are hosts, not the model provider.",
        ),
        (
            Citation(
                "Commission Code of Practice page",
                url="https://digital-strategy.ec.europa.eu/en/policies/contents-code-gpai",
                date="2026-10-07",
                reliability="U",
            ),
        ),
        "gap",
        "Z.ai not listed as a signatory; independent evidence of compliance not recorded.",
        flags=(
            "UNVERIFIED: the Commission page returned 403 to direct fetch; the list was read through a summariser.",
        ),
    ),
    Item(
        "EU-D2",
        "eu-ai-act",
        "Model card, training-data summary and copyright policy from the provider",
        ("Retrieve from the Hugging Face card or the Z.ai site and attach; if absent, record as a gap.",),
        (
            _art("53", "obligations for GPAI providers"),
            Citation("GLM-5.3 model card", url="https://huggingface.co/zai-org/GLM-5.3", date=R, reliability="P"),
        ),
    ),
    # ------------------------------------------------------------------ GDPR
    Item(
        "GDPR-1",
        "gdpr",
        "Art. 35 DPIA screening",
        (
            "Is the processing likely to result in high risk? Art. 35(3): (a) systematic and extensive automated "
            "evaluation incl. profiling with significant effects; (b) large-scale special-category or criminal-offence "
            "data; (c) large-scale systematic monitoring of public areas.",
        ),
        (
            Citation(
                "GDPR Art. 35(1), 35(3)",
                article="Art. 35",
                url="https://gdpr-info.eu/art-35-gdpr/",
                date=R,
                reliability="P",
            ),
        ),
        "needs-counsel",
        "Whether a DPIA is required is a determination for the DPO / counsel.",
        True,
    ),
    Item(
        "GDPR-2",
        "gdpr",
        "Art. 35(7) DPIA contents",
        (
            "A systematic description of the processing and its purposes; an assessment of necessity and "
            "proportionality; a risk assessment; the measures to address the risks.",
            "Prompt: did we assess whether the upstream model was developed lawfully (see GDPR-3)?",
        ),
        (
            Citation(
                "GDPR Art. 35(7)",
                article="Art. 35(7)",
                url="https://gdpr-info.eu/art-35-gdpr/",
                date=R,
                reliability="P",
            ),
        ),
        "gap",
        "No DPIA record referenced. Attest with an evidence_ref once the DPIA exists.",
    ),
    Item(
        "GDPR-3",
        "gdpr",
        "EDPB Opinion 28/2024: lawfulness of the upstream model's development",
        (
            "Anonymity of a model is case-by-case; legitimate interest (Art. 6(1)(f)) cannot be the default basis and "
            "needs the three-step test; unlawful development-phase processing can affect lawfulness of deployment.",
        ),
        (
            Citation(
                "EDPB Opinion 28/2024 (adopted 17 Dec 2024), key takeaways",
                url="https://cms.law/en/int/legal-updates/edpb-opinion-28-2024-key-takeaways-on-processing-personal-data-in-the-context-of-ai-models",
                date=R,
                reliability="S",
            ),
        ),
        "needs-counsel",
        "Deployer duty wording to be verified in the primary opinion.",
        True,
        flags=("UNVERIFIED: deployer-duty wording taken from a secondary summary; check the primary opinion.",),
    ),
    Item(
        "GDPR-4",
        "gdpr",
        "Art. 28 processor agreement",
        (
            "Hosted path: processor agreement with the host. Self-hosted on rented GPUs: with the infrastructure provider.",
        ),
        (
            Citation(
                "GDPR Art. 28", article="Art. 28", url="https://gdpr-info.eu/art-28-gdpr/", date=R, reliability="S"
            ),
        ),
        "gap",
        "No processor agreement recorded.",
        rule=_gdpr4,
    ),
    Item(
        "GDPR-5",
        "gdpr",
        "Chapter V transfer mechanism for non-EEA sub-processors",
        (
            "Transfer mechanism for Chinese or US sub-processors; sub-processor list (Mistral states its control plane "
            "and some sub-processors are outside the region).",
        ),
        (Citation("GDPR Chapter V", article="Chapter V (Arts. 44-50)", url=MISTRAL_REGIONAL, date=R, reliability="S"),),
        "needs-counsel",
        "Transfer analysis is legal.",
        True,
        rule=_gdpr5,
    ),
    # ------------------------------------------------------------------ residency
    Item(
        "RES-1",
        "residency",
        "EU-only processing of prompts and outputs",
        ("Where does inference run? Can the provider pin processing to the EU?",),
        (
            Citation("Amazon Bedrock model card, GLM 5.3", url=AWS_CARD, date=R, reliability="P"),
            Citation("Mistral regional inference", url=MISTRAL_REGIONAL, date=R, reliability="P"),
        ),
        "gap",
        "Residency not established.",
        rule=_res1,
        flags=("Provider prices and Region lists change weekly; re-pull from the provider before relying on this.",),
    ),
    Item(
        "RES-2",
        "residency",
        "Control plane and sub-processor locations",
        (
            "Billing, keys and metadata may sit outside the inference region. List every sub-processor and its location.",
        ),
        (Citation("Mistral regional inference", url=MISTRAL_REGIONAL, date=R, reliability="P"),),
        "gap",
        "Sub-processors not established.",
        rule=_res2,
    ),
    Item(
        "RES-3",
        "residency",
        "Singapore-resident processing",
        (
            "Where required: is inference pinned to Singapore? No managed GLM-5.x endpoint pinned to Singapore was found "
            "in the sources.",
        ),
        (
            Citation("Amazon Bedrock model card, GLM 5.3", url=AWS_CARD, date=R, reliability="P"),
            Citation(
                "TechRepublic on hosting paths (via enterprise research)",
                url="https://techrepublic.com/article/news-chinese-ai-models-cost-risk",
                date="2026-06/07",
                reliability="S",
            ),
        ),
        "gap",
        "Singapore processing not established.",
        rule=_res3,
        flags=("UNVERIFIED: no Singapore-pinned managed GLM-5.x endpoint was found; self-host options not vetted.",),
    ),
    # ------------------------------------------------------------------ Singapore
    Item(
        "SG-1",
        "singapore",
        "MGF Agentic AI: bound risks upfront",
        ("Use-case assessment; limit autonomy and tool/data access.",),
        (
            Citation(
                "IMDA Model AI Governance Framework for Agentic AI (launched 22 Jan 2026), secondary summary",
                url=IMDA_MGF,
                date=R,
                reliability="S",
            ),
        ),
        "gap",
        "No use-case assessment recorded.",
        flags=(
            "UNVERIFIED: the IMDA primary document was not reached; a 'v1.5, 20 May 2026' edition is mentioned by one vendor page only.",
        ),
    ),
    Item(
        "SG-2",
        "singapore",
        "MGF Agentic AI: human accountability and approval checkpoints",
        ("Who is accountable? Where are human approval checkpoints?",),
        (Citation("IMDA MGF for Agentic AI, secondary summary", url=IMDA_MGF, date=R, reliability="S"),),
        "gap",
        "No accountability or checkpoint record.",
    ),
    Item(
        "SG-3",
        "singapore",
        "MGF Agentic AI: technical controls across the lifecycle (testing, restricted access)",
        ("Link conformance, eval and guard runs as testing evidence; record access restrictions.",),
        (Citation("IMDA MGF for Agentic AI, secondary summary", url=IMDA_MGF, date=R, reliability="S"),),
        "gap",
        "No testing evidence attested; related runs are linked below.",
        evidence_components=("conform", "eval", "guard"),
    ),
    Item(
        "SG-4",
        "singapore",
        "MGF Agentic AI: end-user responsibility (transparency, training)",
        ("What are end users told, and what training do they get?",),
        (Citation("IMDA MGF for Agentic AI, secondary summary", url=IMDA_MGF, date=R, reliability="S"),),
        "gap",
        "No end-user transparency record.",
    ),
    Item(
        "SG-5",
        "singapore",
        "PDPC Advisory Guidelines on use of personal data in generative AI (20 Jul 2026)",
        (
            "Lifecycle structure; Publicly Available Exception with a documentation requirement (DPIA or written record "
            "when reliance is arguable); data responsibilities of GenAI stakeholders; handling individual requests. "
            "Non-binding, but it indicates PDPC enforcement positions.",
        ),
        (Citation("Allen & Gledhill, 25 Aug 2026", url=PDPC, date="2026-08-25", reliability="S"),),
        "gap",
        "No mapping to the guidelines recorded.",
    ),
    Item(
        "SG-6",
        "singapore",
        "PDPA general duties: consent, purpose limitation, protection, transfer limitation",
        ("Duties apply to hosted paths that send personal data abroad.",),
        (Citation("PDPC guidelines via Allen & Gledhill", url=PDPC, date="2026-08-25", reliability="S"),),
        "needs-counsel",
        "Applicability and transfer analysis are legal.",
        True,
        flags=("UNVERIFIED: the PDPA duties were not researched beyond the PDPC guidelines.",),
    ),
    Item(
        "SG-7",
        "singapore",
        "AI Verify / Project Moonshot test report (optional eval evidence)",
        (
            "Moonshot (LLM benchmarking and red-teaming) reports can be attached as eval evidence for the Singapore variant.",
        ),
        (
            Citation(
                "aiverify-foundation/moonshot (Apache-2.0, v0.7.6)",
                url="https://github.com/aiverify-foundation/moonshot",
                date=R,
                reliability="P",
            ),
        ),
        "gap",
        "Optional: no Moonshot report attached.",
        evidence_components=("eval",),
    ),
    # ------------------------------------------------------------------ US EAR
    Item(
        "EAR-1",
        "us-ear",
        "Does any prompt, context or output contain EAR- or ITAR-controlled technology?",
        ("If yes, record the authorisation basis before using any China-hosted or Z.ai-operated endpoint.",),
        (
            Citation(
                "NatLawReview, export-control risks of US and Chinese AI models",
                url=NATLAW,
                date="2026-07-23",
                reliability="S",
            ),
        ),
        "needs-counsel",
        "Classification of data under the EAR is a legal determination.",
        True,
        flags=(
            "UNVERIFIED: the NatLawReview piece carries no visible firm byline; the research could not confirm it is the 'Sheppard Mullin 2026-07-23' article.",
        ),
    ),
    Item(
        "EAR-2",
        "us-ear",
        "Hosted via the Z.ai first-party API",
        (
            "The counterparty is an Entity List party (added 16 Jan 2025, 90 FR 4619, presumption of denial). Stop and escalate.",
        ),
        (
            Citation(
                "Federal Register 2025-00704",
                url="https://www.federalregister.gov/documents/2025/01/16/2025-00704/addition-of-entities-to-and-revision-of-entry-on-the-entity-list",
                date="2025-01-16",
                reliability="S",
            ),
            Citation("NatLawReview", url=NATLAW, date="2026-07-23", reliability="S"),
        ),
        "gap",
        "Not applicable unless this path is used.",
        True,
        rule=_ear2,
    ),
    Item(
        "EAR-3",
        "us-ear",
        "Hosted via a US/EU cloud or reseller: operator, inference location, flows to Z.ai",
        (
            "Record who operates the model, where inference runs, and whether any data, telemetry or fees flow to Z.ai. "
            "Bedrock documents state inputs and outputs are not shared with providers for DeepSeek; confirm per model.",
        ),
        (
            Citation("NatLawReview", url=NATLAW, date="2026-07-23", reliability="S"),
            Citation("Amazon Bedrock model card, GLM 5.3", url=AWS_CARD, date=R, reliability="P"),
        ),
        "gap",
        "Flows to Z.ai not established. Attest with an evidence_ref (contract or provider document).",
        rule=_ear3,
        flags=("UNVERIFIED: the Bedrock no-sharing statement was seen for DeepSeek; confirm for GLM 5.3.",),
    ),
    Item(
        "EAR-4",
        "us-ear",
        "Self-hosted open weights: provenance record",
        (
            "Record download source and hash, license text, GPU vendor (no Huawei Ascend without review), and no outbound telemetry.",
        ),
        (Citation("NatLawReview", url=NATLAW, date="2026-07-23", reliability="S"),),
        rule=_ear4,
    ),
    Item(
        "EAR-5",
        "us-ear",
        "Re-check Federal Register / BIS for new model-access rules",
        (
            "Analysts expected an interim final rule 'probably by fall of 2026'. The AI Diffusion Rule text (ECCN 4E091) "
            "was never formally rescinded. A recurring task: record the date of the last check.",
        ),
        (Citation("NatLawReview", url=NATLAW, date="2026-07-23", reliability="S"),),
        "gap",
        "No dated re-check recorded.",
    ),
    Item(
        "EAR-6",
        "us-ear",
        "Self-hosted: foreign-person access to the weights",
        ("A deemed export is person-based; record who can access the weights.",),
        (Citation("NatLawReview", url=NATLAW, date="2026-07-23", reliability="S"),),
        "needs-counsel",
        "Deemed-export analysis is legal.",
        True,
        rule=_ear6,
    ),
    # ------------------------------------------------------------------ license
    Item(
        "LIC-1",
        "license",
        "License recorded as 'MIT-style custom license, glm-5.3' (not 'MIT')",
        (
            "The Hugging Face metadata says license 'other', license_name 'glm-5.3'. The text is MIT-style (use, modify, "
            "fine-tune, sublicense, sell) with additions.",
        ),
        (Citation("GLM-5.3 LICENSE (read 2026-10-26)", url=HF_LICENSE, date=R, reliability="P"),),
        rule=_gen("license-notes", "License recorded in the license notes and the AI-BOM."),
    ),
    Item(
        "LIC-2",
        "license",
        "Model-as-a-Service clause (over US$10B revenue: Z.AI security review)",
        (
            "A licensee (with affiliates) running a MaaS business with over US$10B revenue over 12 months must pass a "
            "Z.AI security review before commercial use. Contact glmlicense@z.ai.",
        ),
        (Citation("GLM-5.3 LICENSE", url=HF_LICENSE, date=R, reliability="P"),),
        rule=_lic2,
    ),
    Item(
        "LIC-3",
        "license",
        "'Use of the Software must comply with applicable laws and regulations'",
        ("A license condition: map it to the other frameworks in this pack.",),
        (Citation("GLM-5.3 LICENSE", url=HF_LICENSE, date=R, reliability="P"),),
        "needs-counsel",
        "What the condition requires is a legal question.",
        True,
    ),
    # ------------------------------------------------------------------ governance (kit-generated)
    Item(
        "GOV-1",
        "governance",
        "Data-flow diagram (hosted vs self-hosted)",
        ("Mermaid source and SVG generated from the descriptor.",),
        (Citation("Research 04 §4.2 E1", url="research/04-deploy-compliance.md", date=R, reliability="S"),),
        rule=_gen("data-flow", "Data-flow diagram generated from the descriptor."),
    ),
    Item(
        "GOV-2",
        "governance",
        "Conformance and eval results attached",
        ("Link conformance and eval runs by run id so claims point at reproducible runs.",),
        (Citation("Research 04 §4.2 E4", url="research/04-deploy-compliance.md", date=R, reliability="S"),),
        evidence_components=("conform", "eval"),
        rule=_gov2,
    ),
    Item(
        "GOV-3",
        "governance",
        "Guard and reference-deployment evidence attached",
        ("Spend and loop guard runs and the deployment sizing or reference runs.",),
        (Citation("Research 04 §4.2 E", url="research/04-deploy-compliance.md", date=R, reliability="S"),),
        evidence_components=("guard", "deploy"),
        rule=_gov3,
    ),
    Item(
        "GOV-4",
        "governance",
        "CycloneDX AI-BOM",
        ("CycloneDX 1.6 emitted and validated against the 1.6 and 1.7 schemas.",),
        (
            Citation(
                "CycloneDX specification (Apache-2.0)",
                url="https://github.com/CycloneDX/specification",
                date=R,
                reliability="P",
            ),
        ),
        rule=_gov_bom,
    ),
    Item(
        "GOV-5",
        "governance",
        "Change log and incident log",
        ("Keep a change log and an incident log; link them.",),
        (Citation("Research 04 §4.2 E6", url="research/04-deploy-compliance.md", date=R, reliability="S"),),
        "gap",
        "No change or incident log referenced.",
    ),
    Item(
        "GOV-6",
        "governance",
        "'Checklist, not legal advice' notice on every page",
        ("Every generated page carries the disclaimer; the render layer refuses to write a page without it.",),
        (Citation("Research 04 §4.2 E7", url="research/04-deploy-compliance.md", date=R, reliability="S"),),
        rule=_gen("disclaimer", "Disclaimer present on every generated page (enforced at render time)."),
    ),
)


def validate_catalogue(items: tuple[Item, ...] | list[Item] = CATALOGUE) -> None:
    """Fail when an item has no valid citation, a duplicate id or an unknown framework."""
    problems: list[str] = []
    seen: set[str] = set()
    for it in items:
        if it.id in seen:
            problems.append(f"{it.id}: duplicate id")
        seen.add(it.id)
        if it.framework not in FRAMEWORKS:
            problems.append(f"{it.id}: unknown framework {it.framework!r}")
        if not it.citations:
            problems.append(f"{it.id}: no citation")
        for c in it.citations:
            if not c.valid:
                problems.append(f"{it.id}: citation {c.label!r} needs an article number or URL + date")
    if problems:
        raise CatalogueError("checklist catalogue invalid: " + "; ".join(problems))


def _applies(it: Item, dep: Deployment) -> Res | None:
    j = FRAMEWORKS[it.framework]["jurisdiction"]
    if j and j not in dep.jurisdictions:
        return Res("n/a", f"{j} is not among this deployment's jurisdictions ({', '.join(dep.jurisdictions)}).")
    return None


def resolve(it: Item, ctx: Ctx) -> Resolved:
    res = _applies(it, ctx.dep) or (it.rule(ctx) if it.rule else None)
    attested = False
    att = ctx.dep.attestations.get(it.id)
    if att and not (res and (res.locked or res.status == "n/a")):
        attested = True
        evidence = [EvidenceLink("attestation", att.evidence_ref, "attested")] if att.evidence_ref else []
        status, reason = att.status, f"Attested: {att.note or att.status}."
        if status == "met" and not att.evidence_ref:
            status, reason = "gap", "Attested met without an evidence_ref; recorded as gap."
        elif status == "met" and it.interpretive and not att.counsel_reviewed:
            status, reason = (
                "needs-counsel",
                "Attested met on an item that depends on legal interpretation without counsel_reviewed; recorded as needs-counsel.",
            )
        res = Res(status, reason, evidence)
    if res is None:
        res = Res(it.default, it.default_reason)
    if res.status == "met" and it.interpretive and not (att and att.counsel_reviewed):
        res = Res(
            "needs-counsel", f"Depends on legal interpretation, so not recorded as met. {res.reason}", res.evidence
        )
    related = [
        EvidenceLink("run", r.run_id, f"{r.component} · {r.verdict}")
        for r in runs_for(ctx.runs, it.evidence_components)
    ]
    evidence = list(res.evidence) + [e for e in related if all(e.ref != x.ref for x in res.evidence)]
    if res.status == "met" and not evidence:
        res = Res("gap", f"No evidence link available for a claimed 'met'. {res.reason}", [])
    return Resolved(
        it.id,
        it.framework,
        it.title,
        list(it.prompts),
        [c.as_dict() for c in it.citations],
        it.in_force,
        it.interpretive,
        list(it.flags),
        res.status,
        res.reason,
        evidence,
        attested,
    )


def resolve_all(ctx: Ctx, items: tuple[Item, ...] | list[Item] = CATALOGUE) -> list[Resolved]:
    validate_catalogue(items)
    return [resolve(it, ctx) for it in items]


def framework_counts(results: list[Resolved]) -> list[dict[str, Any]]:
    out = []
    for key, meta in FRAMEWORKS.items():
        rows = [r for r in results if r.framework == key]
        counts = {s: sum(1 for r in rows if r.status == s) for s in STATUS_ORDER}
        out.append(
            {
                "key": key,
                "title": meta["title"],
                "total": len(rows),
                **{s.replace("/", ""): n for s, n in counts.items()},
            }
        )
    return out


def sort_gaps_first(results: list[Resolved]) -> list[Resolved]:
    return sorted(results, key=lambda r: STATUS_ORDER.index(r.status))
