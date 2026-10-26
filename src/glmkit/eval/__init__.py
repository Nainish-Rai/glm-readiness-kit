"""Migration eval engine (switch-eval): replay a golden set or SwitchTrace file on several targets, grade it and
report per task type whether the candidate is non-inferior to the baseline, with paired statistics.

CLI: ``glmkit eval run``. Contract for golden sets and graders: docs/contracts.md, section "Eval".
"""
