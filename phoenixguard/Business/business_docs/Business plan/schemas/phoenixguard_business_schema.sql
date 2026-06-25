-- PhoenixGuard Commercial PostgreSQL schema skeleton.
-- This is an architecture scaffold, not a final migration.

CREATE TABLE customers (
    id UUID PRIMARY KEY,
    email TEXT NOT NULL UNIQUE,
    full_name TEXT NOT NULL,
    country_code TEXT,
    phone TEXT,
    status TEXT NOT NULL DEFAULT 'active',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE risk_disclosures (
    id UUID PRIMARY KEY,
    version TEXT NOT NULL UNIQUE,
    title TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    effective_at TIMESTAMPTZ NOT NULL,
    retired_at TIMESTAMPTZ
);

CREATE TABLE risk_disclosure_acceptances (
    id UUID PRIMARY KEY,
    customer_id UUID NOT NULL REFERENCES customers(id),
    disclosure_id UUID NOT NULL REFERENCES risk_disclosures(id),
    accepted_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    ip_address INET,
    user_agent TEXT,
    license_id UUID,
    UNIQUE (customer_id, disclosure_id)
);

CREATE TABLE billing_customers (
    id UUID PRIMARY KEY,
    customer_id UUID NOT NULL REFERENCES customers(id),
    provider TEXT NOT NULL,
    provider_customer_id TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (provider, provider_customer_id)
);

CREATE TABLE subscriptions (
    id UUID PRIMARY KEY,
    customer_id UUID NOT NULL REFERENCES customers(id),
    provider TEXT NOT NULL,
    provider_subscription_id TEXT NOT NULL,
    plan_code TEXT NOT NULL,
    status TEXT NOT NULL,
    current_period_start TIMESTAMPTZ,
    current_period_end TIMESTAMPTZ,
    cancel_at_period_end BOOLEAN NOT NULL DEFAULT false,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (provider, provider_subscription_id)
);

CREATE TABLE licenses (
    id UUID PRIMARY KEY,
    customer_id UUID NOT NULL REFERENCES customers(id),
    subscription_id UUID REFERENCES subscriptions(id),
    license_key_hash TEXT NOT NULL UNIQUE,
    plan_code TEXT NOT NULL,
    status TEXT NOT NULL,
    expires_at TIMESTAMPTZ,
    revoked_at TIMESTAMPTZ,
    revoke_reason TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE mt4_accounts (
    id UUID PRIMARY KEY,
    customer_id UUID NOT NULL REFERENCES customers(id),
    broker_server_hash TEXT NOT NULL,
    broker_server_label TEXT,
    account_number_hash TEXT NOT NULL,
    account_number_masked TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (customer_id, broker_server_hash, account_number_hash)
);

CREATE TABLE license_account_bindings (
    id UUID PRIMARY KEY,
    license_id UUID NOT NULL REFERENCES licenses(id),
    mt4_account_id UUID NOT NULL REFERENCES mt4_accounts(id),
    status TEXT NOT NULL DEFAULT 'active',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (license_id, mt4_account_id)
);

CREATE TABLE devices (
    id UUID PRIMARY KEY,
    customer_id UUID NOT NULL REFERENCES customers(id),
    license_id UUID NOT NULL REFERENCES licenses(id),
    device_fingerprint_hash TEXT NOT NULL,
    device_label TEXT,
    connector_version TEXT,
    status TEXT NOT NULL DEFAULT 'active',
    registered_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen_at TIMESTAMPTZ,
    revoked_at TIMESTAMPTZ,
    UNIQUE (license_id, device_fingerprint_hash)
);

CREATE TABLE release_builds (
    id UUID PRIMARY KEY,
    channel TEXT NOT NULL,
    ea_version TEXT NOT NULL,
    connector_version TEXT NOT NULL,
    minimum_connector_version TEXT,
    manifest_json JSONB NOT NULL,
    sha256_manifest TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'draft',
    published_at TIMESTAMPTZ,
    revoked_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE entitlement_snapshots (
    id UUID PRIMARY KEY,
    license_id UUID NOT NULL REFERENCES licenses(id),
    device_id UUID REFERENCES devices(id),
    status TEXT NOT NULL,
    reason TEXT,
    snapshot_json JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE execution_sessions (
    id UUID PRIMARY KEY,
    license_id UUID NOT NULL REFERENCES licenses(id),
    device_id UUID REFERENCES devices(id),
    session_key TEXT NOT NULL,
    symbol TEXT,
    timeframe TEXT,
    status TEXT NOT NULL DEFAULT 'active',
    started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    ended_at TIMESTAMPTZ,
    UNIQUE (license_id, session_key)
);

CREATE TABLE signed_execution_commands (
    id UUID PRIMARY KEY,
    license_id UUID NOT NULL REFERENCES licenses(id),
    device_id UUID REFERENCES devices(id),
    execution_session_id UUID REFERENCES execution_sessions(id),
    packet_id TEXT NOT NULL,
    stream_sequence BIGINT NOT NULL,
    command_status TEXT NOT NULL,
    side TEXT,
    symbol TEXT,
    created_epoch DOUBLE PRECISION,
    valid_until_epoch DOUBLE PRECISION,
    command_hash TEXT NOT NULL,
    signature_alg TEXT,
    delivered_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (license_id, packet_id)
);

CREATE TABLE connector_heartbeats (
    id UUID PRIMARY KEY,
    license_id UUID NOT NULL REFERENCES licenses(id),
    device_id UUID NOT NULL REFERENCES devices(id),
    connector_version TEXT,
    ea_version TEXT,
    mt4_terminal_build TEXT,
    status TEXT NOT NULL,
    detail TEXT,
    ip_address INET,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE email_events (
    id UUID PRIMARY KEY,
    customer_id UUID REFERENCES customers(id),
    provider TEXT NOT NULL,
    template_key TEXT NOT NULL,
    provider_message_id TEXT,
    status TEXT NOT NULL,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE audit_events (
    id UUID PRIMARY KEY,
    actor_type TEXT NOT NULL,
    actor_id TEXT,
    action TEXT NOT NULL,
    target_type TEXT,
    target_id TEXT,
    ip_address INET,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_licenses_customer ON licenses(customer_id);
CREATE INDEX idx_devices_license ON devices(license_id);
CREATE INDEX idx_commands_license_created ON signed_execution_commands(license_id, created_at DESC);
CREATE INDEX idx_heartbeats_device_created ON connector_heartbeats(device_id, created_at DESC);
CREATE INDEX idx_audit_target ON audit_events(target_type, target_id);

