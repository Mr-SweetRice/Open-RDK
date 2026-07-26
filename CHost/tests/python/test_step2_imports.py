import sys
import unittest


class Step2ImportTests(unittest.TestCase):
    def test_native_package_imports(self):
        from openrdkC import CommsRuntimeC

        self.assertEqual(CommsRuntimeC.__name__, "CommsRuntimeC")

    def test_lifecycle_is_idempotent(self):
        from openrdkC import CommsRuntimeC

        runtime = CommsRuntimeC(auto_start=False)
        self.assertFalse(runtime.is_running)
        self.assertIs(runtime.start(), runtime)
        self.assertTrue(runtime.is_running)
        self.assertIs(runtime.start(), runtime)
        runtime.stop()
        runtime.stop()
        self.assertFalse(runtime.is_running)

    def test_context_manager(self):
        from openrdkC import CommsRuntimeC

        with CommsRuntimeC(auto_start=False) as runtime:
            self.assertTrue(runtime.is_running)
            self.assertEqual(runtime.native_version, "0.1.0")
        self.assertFalse(runtime.is_running)

    def test_device_access_is_explicitly_unimplemented(self):
        from openrdkC import CommsRuntimeC

        runtime = CommsRuntimeC()
        with self.assertRaises(NotImplementedError):
            runtime.list_devices()
        runtime.stop()

    def test_package_does_not_import_standard_host(self):
        self.assertNotIn("openrdk", sys.modules)
        import openrdkC  # noqa: F401
        self.assertNotIn("openrdk", sys.modules)


if __name__ == "__main__":
    unittest.main()
