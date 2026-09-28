import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from datetime import datetime, timezone
from unittest.mock import patch
from control_center.telemetry import runtime_visibility


class TelemetryTests(unittest.TestCase):
    def test_stale_missing_and_pending_evidence_are_distinct(self):
        now = datetime(2026,9,28,tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as tmp, patch.dict("os.environ", {"AGCA_STATE_DIR":tmp}):
            Path(tmp,"technical-kline-health.json").write_text(json.dumps({
                "at":"2026-09-27T00:00:00+00:00", "broad_radar_instrument_count":204,
                "broad_radar_fresh":True,"broad_radar_snapshot_timestamp_ms":now.timestamp()*1000-86400000}))
            con = sqlite3.connect(":memory:")
            con.executescript("""CREATE TABLE paper_agent_positions(position_id,symbol,direction,entry_price,selection_reason,status);
                INSERT INTO paper_agent_positions VALUES(1,'ETHUSDT','LONG',100,'why','PENDING_ENTRY');""")
            out = runtime_visibility(con,{}, {},now)
            self.assertEqual(out["Geniş radar"]["Sağlık kaydı"],"STALE")
            self.assertEqual(out["Geniş radar"]["Radar yaşı (ms)"],86400000)
            self.assertIsNone(out["Orchestrator kapasitesi"])
            self.assertEqual(out["Bekleyen pozisyonlar"][0]["symbol"],"ETHUSDT")
            self.assertIsNone(out["Yetki ve çalışan kaynak"]["Gerçek emir eventleri"])
            con.close()
