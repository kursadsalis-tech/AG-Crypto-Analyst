import unittest
from unittest.mock import patch

from control_center import server


class ControlCenterServerTests(unittest.TestCase):
    def test_snapshot_uses_read_model_then_operator_presentation(self):
        raw = {"system": {}, "portfolio": {}, "positions": [], "closed_positions": []}
        rendered = {"durum": "Sistem çalışıyor"}
        with patch.object(server, "overview", return_value=raw) as read, \
             patch.object(server, "operator_view", return_value=rendered) as present:
            self.assertEqual(server.snapshot(), rendered)
            read.assert_called_once_with()
            present.assert_called_once_with(raw)

    def test_v1_has_no_generic_write_surface(self):
        self.assertFalse(hasattr(server, "execute_sql"))
        self.assertFalse(hasattr(server, "mutate_database"))


if __name__ == "__main__":
    unittest.main()
