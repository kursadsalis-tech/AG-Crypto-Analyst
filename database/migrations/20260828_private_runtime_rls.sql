-- AGCA runtime mirror is private and written only by its PostgreSQL owner role.
-- No anon/authenticated policies are intentional: Data API access fails closed.
REVOKE ALL ON SCHEMA agca_runtime FROM anon, authenticated;
REVOKE ALL ON ALL TABLES IN SCHEMA agca_runtime FROM anon, authenticated;
ALTER DEFAULT PRIVILEGES IN SCHEMA agca_runtime
    REVOKE ALL ON TABLES FROM anon, authenticated;

ALTER TABLE agca_runtime.external_trader_consensus_snapshots ENABLE ROW LEVEL SECURITY;
ALTER TABLE agca_runtime.external_trader_learning_reviews ENABLE ROW LEVEL SECURITY;
ALTER TABLE agca_runtime.external_trader_source_snapshots ENABLE ROW LEVEL SECURITY;
ALTER TABLE agca_runtime.external_trader_trade_observations ENABLE ROW LEVEL SECURITY;
ALTER TABLE agca_runtime.forecast_drift_challenger_predictions ENABLE ROW LEVEL SECURITY;
ALTER TABLE agca_runtime.forecast_feature_challenger_predictions ENABLE ROW LEVEL SECURITY;
ALTER TABLE agca_runtime.paper_agent_forward_evaluations ENABLE ROW LEVEL SECURITY;
ALTER TABLE agca_runtime.paper_agent_research_candidates ENABLE ROW LEVEL SECURITY;
ALTER TABLE agca_runtime.paper_agent_strategy_allocation_history ENABLE ROW LEVEL SECURITY;
ALTER TABLE agca_runtime.paper_agent_strategy_allocations ENABLE ROW LEVEL SECURITY;
