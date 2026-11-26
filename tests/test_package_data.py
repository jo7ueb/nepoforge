"""配布パッケージに runtime plugin が含まれることを検証する。"""

import unittest
from importlib.resources import files


class TestPackageData(unittest.TestCase):
    def test_runtime_guard_plugin_is_a_package_resource(self):
        resource = files("nepoforge").joinpath("data/plugins/runtime-guard.ts")
        self.assertTrue(resource.is_file())
        self.assertTrue(resource.read_text(encoding="utf-8").strip())


if __name__ == "__main__":
    unittest.main()
