import json
import os
import tempfile
import unittest
from pathlib import Path

from parcel_watch.config import CREDENTIAL_KEYS

FIXTURES = Path(__file__).parent / "fixtures"


def fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


def assert_no_private(test: unittest.TestCase, result) -> None:
    test.assertNotIn("PRIVATE", json.dumps(result.to_dict()))


class StateDirTest(unittest.TestCase):
    """Runs each test with its own PARCEL_WATCH_HOME and no carrier env vars."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.home = Path(self._tmp.name)
        self._saved = {k: os.environ.get(k) for k in ("PARCEL_WATCH_HOME", *CREDENTIAL_KEYS)}
        for k in CREDENTIAL_KEYS:
            os.environ.pop(k, None)
        os.environ["PARCEL_WATCH_HOME"] = str(self.home)

    def tearDown(self):
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self._tmp.cleanup()
