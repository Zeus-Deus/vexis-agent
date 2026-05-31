"""Anchor pytest's rootdir at the browser add-on package + document the
import contract.

The production loader imports each add-on under a synthetic
``vexis_addons.<name>`` module name via importlib. The bundled browser
add-on ALSO lives at the real dotted path ``vexis_agent.addons.browser``,
and these tests import it that way for brevity (the repo root is on
``sys.path`` in the test env). Mirrors the codemux add-on's tests.
"""
