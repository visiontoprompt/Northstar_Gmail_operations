CREATE TABLE IF NOT EXISTS proxy_groups (
    id VARCHAR(96) PRIMARY KEY,
    organization_id VARCHAR(64) NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    name VARCHAR(80) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_proxy_group_org_name UNIQUE (organization_id, name)
);

CREATE INDEX IF NOT EXISTS ix_proxy_groups_organization_id ON proxy_groups (organization_id);

INSERT INTO proxy_groups (id, organization_id, name)
SELECT 'proxy_group_' || md5(organization_id || ':' || proxy_group), organization_id, proxy_group
FROM proxy_inventory
WHERE proxy_group IS NOT NULL AND btrim(proxy_group) <> ''
GROUP BY organization_id, proxy_group
ON CONFLICT (organization_id, name) DO NOTHING;
