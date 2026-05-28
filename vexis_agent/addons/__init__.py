"""Bundled add-ons that ship in the vexis-agent wheel.

Each subdirectory under here is one add-on, discovered at daemon
startup by ``vexis_agent.core.addons.loader.discover_addons``. Both
bundled and user-installed add-ons require an explicit
``addons.enabled`` entry in ``~/.vexis/config.yaml`` — keeps core
simple and stable, matches the explicit-allow-list discovery
policy.

This file exists so ``vexis_agent.addons`` is an importable package
(needed by package-data discovery and some test helpers); the
add-on loader doesn't actually import THIS module — it walks the
directory at runtime and imports each add-on under
``vexis_addons.<name>`` via ``importlib.util``.
"""
