"""Host quality: latency and silent-downgrade fingerprinting (issue 05).

This package holds the data-side of the host-quality verdict: statistics (``stats``), versioned probe sets and
capability canaries (``probes``, ``data/``), the stored reference fingerprint (``reference``), pure analysis over
exchanges (``analysis``) and the run engine (``engine``). The cases, executors, checks, mock defects, CLI
commands and dashboard tab live in their usual extension-point folders and import from here.

Latency and fingerprint results never enter the correctness verdict of ``glmkit conform run``: the cases are
``opt_in`` and the host-quality run (``glmkit conform host``) writes its own envelope (component ``hostq``).
"""
