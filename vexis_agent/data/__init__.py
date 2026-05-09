"""Shipped data files (templates the setup wizard installs into
``$VEXIS_HOME``). Read via ``importlib.resources`` so wheel installs
work transparently — the files are bundled into the package, no
repo-checkout assumption.

The repo-root copies (``config.example.yaml``, ``.env.example``) are
the human-browsable counterparts; ``tests/test_data_examples_consistency.py``
asserts byte-for-byte parity so users browsing GitHub see the same
text the wizard writes.
"""
