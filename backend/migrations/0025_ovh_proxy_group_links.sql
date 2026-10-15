CREATE TABLE IF NOT EXISTS ovh_proxy_group_links (
    id VARCHAR(96) PRIMARY KEY,
    organization_id VARCHAR(64) NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    proxy_group VARCHAR(80) NOT NULL,
    resource_type VARCHAR(16) NOT NULL,
    service_name VARCHAR(128) NOT NULL,
    instance_id VARCHAR(128) NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_ovh_proxy_group_resource UNIQUE (organization_id, proxy_group, resource_type, service_name, instance_id)
);
CREATE INDEX IF NOT EXISTS ix_ovh_proxy_group_links_org ON ovh_proxy_group_links (organization_id);
