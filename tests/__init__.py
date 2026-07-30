"""Test package bootstrap.

Isolate the shared repository-root ``history.json`` for the entire test
session so controller constructions that fall back to the default historian
never read or write the real file. This runs once, before any test module is
imported, under both ``python -m unittest discover -s tests`` and
single-module invocations such as ``python -m unittest tests.test_behavior``
(importing any ``tests.*`` module imports this package first).

See ``tests/fixtures.py`` for the rationale and the ``make_test_historian``
helper used by tests that need an explicit isolated historian.
"""

from __future__ import annotations

import atexit

from tests.fixtures import install_default_history_isolation

atexit.register(install_default_history_isolation())
