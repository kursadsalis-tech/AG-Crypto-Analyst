import unittest

from data import okx_l2_source_audit as audit


class OkxL2SourceAuditTests(unittest.TestCase):
    def test_metadata_never_claims_schema_or_model_readiness(self):
        instruments = {"code": "0", "data": {"instList": ["BTC-USDT"]}}
        downloads = [{
            "code": "0",
            "data": {"details": [{
                "instFamily": "BTC-USDT",
                "groupDetails": [{
                    "filename": "btc.tar.gz", "sizeMB": "12.5",
                    "url": "https://static.okx.test/btc.tar.gz",
                    "dateTs": "1",
                }],
            }]},
        }]
        result = audit.audit(instruments, downloads, ("BTC-USDT", "ETH-USDT"))
        self.assertEqual(result["cohort"]["listed_count"], 1)
        self.assertEqual(result["cohort"]["artifact_verified_count"], 1)
        self.assertFalse(result["actual_file_schema_verified"])
        self.assertFalse(result["ready_for_historical_model_test"])
        self.assertFalse(result["model_or_policy_changed"])

    def test_failed_download_response_adds_no_artifact(self):
        result = audit.audit(
            {"code": "0", "data": {"instList": ["BTC-USDT"]}},
            [{"code": "500", "data": {}}],
            ("BTC-USDT",),
        )
        self.assertEqual(result["cohort"]["artifact_verified_count"], 0)
        self.assertFalse(result["ready_for_historical_model_test"])


if __name__ == "__main__":
    unittest.main()
