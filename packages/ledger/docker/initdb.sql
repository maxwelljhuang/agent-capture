-- Roles created on the cluster the first time the DB starts.
-- The migration creates tables; this file creates the principals.
DO $$ BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'ledger_app') THEN
        CREATE ROLE ledger_app LOGIN PASSWORD 'ledger_app';
    END IF;
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'ledger_reader') THEN
        CREATE ROLE ledger_reader LOGIN PASSWORD 'ledger_reader';
    END IF;
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'ledger_retention') THEN
        CREATE ROLE ledger_retention LOGIN PASSWORD 'ledger_retention';
    END IF;
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'ledger_attestation') THEN
        CREATE ROLE ledger_attestation LOGIN PASSWORD 'ledger_attestation';
    END IF;
END $$;

GRANT CONNECT ON DATABASE ledger TO ledger_app, ledger_reader, ledger_retention, ledger_attestation;
GRANT USAGE ON SCHEMA public TO ledger_app, ledger_reader, ledger_retention, ledger_attestation;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT SELECT, INSERT ON TABLES TO ledger_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT SELECT ON TABLES TO ledger_reader, ledger_attestation;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO ledger_retention;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT INSERT ON TABLES TO ledger_reader;  -- access_log
