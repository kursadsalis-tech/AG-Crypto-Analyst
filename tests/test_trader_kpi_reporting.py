import sqlite3
import unittest

from reports import investment_committee_report as report
from paper_trading.crypto_profit_agent import FORWARD_COHORT


class TraderKpiReportingTests(unittest.TestCase):
    def test_action_economics_are_distinct_from_three_class_accuracy(self):
        con = sqlite3.connect(":memory:")
        con.execute("""
            CREATE TABLE forecast_engine_predictions(
              status TEXT,horizon_hours INTEGER,direction_hit INTEGER,
              absolute_error_pct REAL,interval_hit INTEGER,action_hit INTEGER,
              action_net_utility_pct REAL
            )
        """)
        con.executemany("INSERT INTO forecast_engine_predictions VALUES(?,?,?,?,?,?,?)", [
            ("SETTLED",24,0,2.0,1,1,2.0),
            ("SETTLED",24,0,1.0,1,0,-0.5),
        ])
        score = report.unified_forecast_performance(con)[24]
        self.assertEqual(score["direction_hit_rate"], 0.0)
        self.assertEqual(score["action_hit_rate"], 50.0)
        self.assertAlmostEqual(score["action_net_utility_pct"], 0.75)
        self.assertAlmostEqual(score["profit_factor"], 4.0)
        con.close()

    def test_clean_paper_economics_are_reported_from_actual_positions(self):
        con = sqlite3.connect(":memory:")
        con.execute("""
            CREATE TABLE paper_agent_positions(
              position_id INTEGER,exit_time TEXT,horizon_hours INTEGER,
              direction TEXT,net_pnl REAL,validation_cohort TEXT,
              evidence_eligible INTEGER,status TEXT
            )
        """)
        con.executemany("INSERT INTO paper_agent_positions VALUES(?,?,?,?,?,?,?,?)", [
            (1,"a",24,"SHORT",10.0,FORWARD_COHORT,1,"CLOSED"),
            (2,"b",24,"SHORT",-2.0,FORWARD_COHORT,1,"CLOSED"),
            (3,"c",4,"LONG",99.0,"LEGACY",0,"CLOSED"),
        ])
        score = report.paper_trader_performance(con)
        self.assertEqual(len(score), 1)
        self.assertEqual(score[0]["net_pnl"], 8.0)
        self.assertEqual(score[0]["profit_factor"], 5.0)
        con.close()


if __name__ == "__main__":
    unittest.main()
