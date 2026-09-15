-- Safe proxy health observations. Proxy credentials remain encrypted in account_secrets.
CREATE TABLE proxy_inventory (
  id VARCHAR(96) PRIMARY KEY,
  organization_id VARCHAR(64) NOT NULL REFERENCES organizations(id),
  label VARCHAR(160),
  region VARCHAR(80),
  provider VARCHAR(200),
  country VARCHAR(120),
  city VARCHAR(120),
  public_ip VARCHAR(64),
  test_status VARCHAR(32) NOT NULL DEFAULT 'NOT_TESTED',
  test_error VARCHAR(240),
  latency_ms INTEGER,
  last_tested_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX ix_proxy_inventory_org ON proxy_inventory (organization_id);
