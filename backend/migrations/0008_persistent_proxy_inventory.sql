-- Keep proxy inventory after its last account assignment is removed.
-- The connection payload is encrypted with the application secret key.
ALTER TABLE proxy_inventory ADD COLUMN encrypted_payload TEXT;
