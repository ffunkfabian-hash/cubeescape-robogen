"""Shared test helpers: the scripts folder on sys.path, temp folders."""
import os
import sys
import tempfile

SCRIPTS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts")
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)


def tempdir(case):
    """A temp folder removed after the test (realpath: /var is a link to /private/var on macOS)."""
    d = tempfile.TemporaryDirectory(prefix="robogen-test-")
    case.addCleanup(d.cleanup)
    return os.path.realpath(d.name)
