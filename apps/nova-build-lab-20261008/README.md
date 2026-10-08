# Synthetic build lab

This app exercises the existing Build and Publish workflow on branch `codex/build-lab-sigstore`. It has no secrets or customer data. It reuses the existing ECR, S3 and release stages under the unique app name `nova-build-lab-20261008`; platform callbacks are omitted when dispatching.

The branch makes cosign signing and verification mandatory, verifies the exact GitHub workflow identity/revision, retains Sigstore bundles and passes the source commit into the synthetic app. No local operator key is used. Public signing records contain public identity, digest, signature and certificate information; private signing keys remain ephemeral on the runner.

Source directory: `apps/nova-build-lab-20261008/source`. The current source ref resolves to the branch commit and that resolved commit is recorded in the signed build attestation. Rebuilds may change PCRs; verify each exact signed release.
