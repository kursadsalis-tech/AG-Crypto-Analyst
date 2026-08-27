import unittest
from unittest import mock

from analysis import forward_shadow_validator


class ForwardShadowStatusTests(unittest.TestCase):
    def test_unsupported_asset_quarantine_does_not_degrade_runtime(self):
        con = mock.Mock()
        with mock.patch.object(forward_shadow_validator, "runtime_connect", return_value=con), \
                mock.patch.object(forward_shadow_validator, "create_tables"), \
                mock.patch.object(forward_shadow_validator, "invalidate_legacy_seed", return_value=0), \
                mock.patch.object(
                    forward_shadow_validator,
                    "get_or_create_start",
                    return_value=(forward_shadow_validator.utcnow(), False),
                ), \
                mock.patch.object(forward_shadow_validator, "settle_due", return_value=0), \
                mock.patch.object(
                    forward_shadow_validator,
                    "record_new_predictions",
                    return_value=(0, 1),
                ), \
                mock.patch.object(forward_shadow_validator, "summary"):
            result = forward_shadow_validator.main()

        self.assertEqual(result["status"], "SUCCESS")
        self.assertEqual(result["unsupported_asset_quarantined"], 1)
        con.close.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
