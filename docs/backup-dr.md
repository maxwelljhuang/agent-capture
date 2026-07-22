# Backup, restore & disaster recovery

The ledger is a **tamper-evident system of record**; a bank's auditors will
require a backup/DR story for it. You are not "deployed" until a **restore drill**
passes.

## What to back up
1. **Ledger Postgres** — the spans store (system of record). Highest priority.
2. **Enforcement Postgres** — the hold queue (in-flight fail-to-human reviews).
3. **The Ed25519 signing key(s)** — back up *all* key_ids (offline/KMS); losing
   them means you can't sign new attestations and can't prove provenance of the
   public key. The **public** PEMs should also be archived with the regulator.
4. **The attestation sink archive** (`LEDGER_ATTESTATION_SINK`, e.g. S3/file) —
   the signed Merkle roots. This is the off-database anchor that lets a regulator
   verify integrity **even if the database is lost or doctored**.

## Backup methods
- **Logical:** `pg_dump -Fc` on a schedule (simple; coarse RPO).
- **Continuous / PITR (recommended for the ledger):** WAL archiving + base
  backups → restore to any point in time. Tighter RPO for the record of record.
- The attestation sink should be append-only object storage with its own
  retention ≥ the longest `RetentionClass` (7y EXTENDED).

## Restore drill (run it; don't assume it)
1. Restore the ledger DB into a clean Postgres.
2. Start `ledger serve` against it; `GET /ready` is green.
3. `POST /verify/<trajectory_id>` on a sample of trajectories → `chain_intact: true`
   (recomputes every `content_hash` from stored bodies).
4. **Independent check:** recompute a trajectory's root hash, walk the Merkle
   proof from `GET /attestations/proof/<tid>`, and compare against the signed
   root in the **attestation archive** + the published public key. This proves
   the restored data matches what was attested — detection does not require
   trusting the restored DB.
5. Enforcement: restore its DB, `enforcement db migrate` is a no-op (already at
   head), pending holds are intact; the timeout worker resumes.

## Things to decide / watch
- **RPO/RTO targets** per service (define with the bank; the ledger likely wants
  the tightest).
- **Retention interplay:** restoring an *old* backup can resurrect spans the
  retention worker had deleted; reconcile against `retention_operations` and
  re-run retention after a restore if required by policy.
- **HA vs. DR:** this doc is DR (restore from backup). Read-replicas / failover
  for high availability are P2 in `docs/production-hardening-plan.md`.
