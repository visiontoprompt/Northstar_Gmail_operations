-- Publisher signup passwords belong to an account/newsletter pair, never to
-- the Gmail credential or the public newsletter identity.
ALTER TABLE newsletter_subscriptions ADD COLUMN IF NOT EXISTS signup_password_encrypted TEXT;
