import os
import unittest
from unittest import mock

from database import runtime_store
from runtime import remote_mirror_drain


class RemoteMirrorDrainTests(unittest.TestCase):
    def test_drain_submits_recovered_tables(self):
        con = mock.MagicMock()
        con._remote_dirty_tables = {"paper_agent_positions"}
        expected = runtime_store.MirrorResult(
            attempted=frozenset({"paper_agent_positions"}),
            succeeded=frozenset({"paper_agent_positions"}),
        )
        with mock.patch.object(runtime_store, "connect", return_value=con):
            with mock.patch.object(
                runtime_store, "_mirror_after_local_commit", return_value=expected
            ) as mirror:
                result = remote_mirror_drain.drain("state.db")
        mirror.assert_called_once_with(con, {"paper_agent_positions"})
        self.assertEqual(result, expected)
        con.close.assert_called_once()

    def test_empty_outbox_does_not_open_remote_connection(self):
        con = mock.MagicMock()
        con._remote_dirty_tables = set()
        with mock.patch.object(runtime_store, "connect", return_value=con):
            with mock.patch.object(runtime_store, "_mirror_after_local_commit") as mirror:
                result = remote_mirror_drain.drain("state.db")
        mirror.assert_not_called()
        self.assertEqual(result, runtime_store.MirrorResult())

    def test_critical_evidence_is_drained_before_bulk_backlog(self):
        con = mock.MagicMock()
        con._remote_dirty_tables = {
            "crypto_news_analysis", "cloud_runtime_attestations"
        }
        def result_for(_con, tables):
            table = next(iter(tables))
            return runtime_store.MirrorResult(
                attempted=frozenset({table}), succeeded=frozenset({table})
            )
        with mock.patch.object(runtime_store, "connect", return_value=con):
            with mock.patch.object(
                runtime_store, "_mirror_after_local_commit", side_effect=result_for
            ) as mirror:
                result = remote_mirror_drain.drain("state.db")
        self.assertEqual(
            [call.args[1] for call in mirror.call_args_list],
            [{"cloud_runtime_attestations"}, {"crypto_news_analysis"}],
        )
        self.assertEqual(
            result.succeeded,
            frozenset({"cloud_runtime_attestations", "crypto_news_analysis"}),
        )

    def test_critical_evidence_uses_large_batch_without_leaking_environment(self):
        con = mock.MagicMock()
        con._remote_dirty_tables = {
            "cloud_runtime_attestations", "crypto_news_analysis"
        }
        observed = []

        def result_for(_con, tables):
            observed.append((next(iter(tables)), os.getenv("AGCA_REMOTE_BATCH_SIZE")))
            return runtime_store.MirrorResult(
                attempted=frozenset(tables), succeeded=frozenset(tables)
            )

        with mock.patch.dict(os.environ, {
            "AGCA_REMOTE_BATCH_SIZE": "25",
            "AGCA_REMOTE_PRIORITY_BATCH_SIZE": "750",
        }, clear=False):
            with mock.patch.object(runtime_store, "connect", return_value=con):
                with mock.patch.object(
                    runtime_store, "_mirror_after_local_commit", side_effect=result_for
                ):
                    remote_mirror_drain.drain("state.db")
            self.assertEqual(os.environ["AGCA_REMOTE_BATCH_SIZE"], "25")

        self.assertEqual(observed, [
            ("cloud_runtime_attestations", "750"),
            ("crypto_news_analysis", "25"),
        ])


if __name__ == "__main__":
    unittest.main()
