ALTER TABLE workspace_settings ADD COLUMN IF NOT EXISTS two_captcha_api_key_encrypted TEXT;
ALTER TABLE workspace_settings ADD COLUMN IF NOT EXISTS two_captcha_enabled BOOLEAN NOT NULL DEFAULT FALSE;
