# ruff: noqa: E501
"""License notes for GLM-5.3 (research 04 §0, §4.2 C5). Flags the Art. 53(2) question as needs-counsel."""

from __future__ import annotations

from glmkit.evidence.checklist import HF_LICENSE, Resolved
from glmkit.evidence.descriptor import Deployment
from glmkit.evidence.render import UL, H, P, Table

RECORDED_AS = "MIT-style custom license, glm-5.3"


def license_blocks(dep: Deployment, items: dict[str, Resolved]) -> list:
    def st(i: str) -> str:
        return "{status:" + items[i].status + "}"

    m = dep.operator.maas_over_10b_revenue
    declared = {
        True: "yes (a Z.AI security review is required before commercial use)",
        False: "no (self-declared)",
        None: "not declared",
    }[m]
    return [
        P(
            f"Recorded as: **{RECORDED_AS}**. The Hugging Face metadata says `license: other`, `license_name: glm-5.3`; "
            f"the text is read from [the LICENSE file]({HF_LICENSE}) (read 2026-10-26). Press and law-firm pieces call "
            "GLM-5.2 'MIT'; this pack does not, because the GLM-5.3 text adds conditions."
        ),
        H(2, "What the text allows and adds"),
        UL(
            [
                "Allows: use, modify, fine-tune, sublicense and sell the Software (MIT-style grant).",
                "Condition 1: use of the Software must comply with applicable laws and regulations. " + st("LIC-3"),
                "Condition 2 (Model-as-a-Service clause): a licensee, with affiliates, running a MaaS business with over "
                "US$10B revenue over 12 months must pass a Z.AI security review before commercial use. Contact "
                "glmlicense@z.ai. " + st("LIC-2"),
            ]
        ),
        H(2, "This deployment"),
        Table(
            ["Question", "Declared in the descriptor"],
            [
                ["Path", dep.path],
                ["Operator runs a MaaS business over US$10B revenue", declared],
                ["Weights source", dep.weights.source_url or "not logged"],
            ],
        ),
        H(2, "EU AI Act Art. 53(2) open-source exemption: needs-counsel"),
        P(
            "The exemption requires a free and open-source license permitting use, access, modification and "
            "redistribution without monetisation. The GLM-5.3 license has a MaaS security-review clause and a "
            "'comply with applicable laws' condition. Whether that defeats the exemption is a question for counsel; "
            "this pack does not answer it. " + st("EU-C5")
        ),
        P(
            "Consequence to plan for: if the exemption does not apply, the documentation duties of Art. 53(1)(b) toward "
            "downstream providers sit with the model provider, and you will want that documentation from Z.ai "
            "(checklist item EU-B4, EU-D2)."
        ),
        H(2, "Sources"),
        UL(
            [
                f"[GLM-5.3 LICENSE]({HF_LICENSE}) (primary, read 2026-10-26)",
                "[Hugging Face model card](https://huggingface.co/zai-org/GLM-5.3) (primary, 2026-10-26)",
                "[Art. 53 of Regulation (EU) 2024/1689](https://artificialintelligenceact.eu/article/53/) (2026-10-26)",
            ]
        ),
    ]
