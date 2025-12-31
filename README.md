# Nova App Hub

A centralized, transparent, and trustworthy build platform for AWS Nitro Enclave applications using [Enclaver](https://github.com/sparsity-xyz/enclaver).

## Overview

Nova App Hub enables developers to build their applications into AWS Nitro Enclave format through a transparent, auditable process. Each build produces PCR (Platform Configuration Register) values for remote attestation.

### Key Features

- **Transparent Builds**: All build processes are visible in GitHub Actions
- **Trustworthy**: Source code and configurations are version-controlled and reviewed
- **Automated**: PR merge triggers automatic build pipeline
- **Verifiable**: Each release includes PCR values, source commit, and build artifacts
- **SLSA Level 3**: Builds are signed using Sigstore cosign with keyless signing

## Quick Start

### Using Nova Platform (Recommended)

The easiest way to build your application is through [Nova Platform](https://nova.sparsity.xyz):

1. Create an app on Nova Platform
2. Configure your repository URL and enclaver settings
3. Click "Trigger Build" - Nova Platform generates both config files automatically

### Manual Configuration

If you prefer to manage configurations manually:

#### 1. Create Your Configuration

Create a new directory under `apps/` with your application name:

```
apps/
└── your-app-name/
    ├── nova-build.yaml    # Build configuration
    └── enclaver.yaml      # Enclaver configuration
```

#### 2. Configure nova-build.yaml (Build Settings)

```yaml
# Required fields
name: your-app-name           # Must match directory name
version: 1.0.0                # Semantic version
repo: https://github.com/your-org/your-repo
ref: main                     # Branch, tag, or commit SHA

# Optional: Build configuration
build:
  directory: .                # Dockerfile location (default: root)
  dockerfile: Dockerfile      # Dockerfile name
  args:                       # Build arguments
    - name: BUILD_ENV
      value: production

# Optional: Metadata
metadata:
  description: "Your application description"
  maintainer: you@example.com
  license: MIT
```

#### 3. Configure enclaver.yaml (Enclaver Settings)

```yaml
version: v1
name: your-app-name
target: nova-apps/your-app-name:1.0.0
sources:
  app: your-app-name:1.0.0
defaults:
  memory_mb: 1500             # Enclave memory allocation
ingress:
  - listen_port: 8000         # Your app's HTTP port
api:
  listen_port: 9000           # Internal API port (attestation, signing)
aux_api:
  listen_port: 9001           # Auxiliary API port
egress:
  allow:                      # Domains allowed for egress
    - api.example.com
```

#### 4. Trigger Build

Builds can be triggered via:
- **Nova Platform**: Click "Trigger Build" button
- **Workflow Dispatch**: Manually trigger via GitHub Actions with `app_dir` input

### Get Your Artifacts

After build completes:

- **Release Image**: Docker image with embedded EIF in AWS ECR
- **PCR Values**: Recorded in `pcr.json`
- **Build Metadata**: Complete build info in `build-metadata.json` (includes PCR, SLSA provenance)

## Configuration Reference

### Required Fields

| Field | Type | Description |
|-------|------|-------------|
| `name` | string | Application name (lowercase, numbers, hyphens) |
| `version` | string | Semantic version (e.g., 1.0.0) |
| `repo` | string | Public GitHub repository URL |
| `ref` | string | Git reference (branch, tag, or commit SHA) |

### Optional Fields (nova-build.yaml)

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `build.directory` | string | `.` | Dockerfile directory |
| `build.dockerfile` | string | `Dockerfile` | Dockerfile name |
| `build.args` | array | `[]` | Docker build arguments |
| `metadata.description` | string | - | Brief description of the application |
| `metadata.maintainer` | string | - | Maintainer email address |
| `metadata.license` | string | - | License identifier (e.g., MIT) |
| `reproducible.enabled` | boolean | `true` | Enable reproducible build mode |
| `reproducible.source_date_epoch` | integer | - | Fixed Unix timestamp for reproducible builds |

### Enclaver Configuration (enclaver.yaml)

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `ingress[].listen_port` | integer | `8000` | Ingress traffic port |
| `api.listen_port` | integer | `9000` | Internal API port |
| `aux_api.listen_port` | integer | `9001` | Auxiliary API port |
| `defaults.memory_mb` | integer | `1500` | Memory allocation (MB) |
| `egress.allow` | array | `[]` | Allowed egress domains |

## PCR Values

AWS Nitro Enclaves use Platform Configuration Registers (PCRs) for remote attestation:

| PCR | Description |
|-----|-------------|
| **PCR0** | Hash of the enclave image file |
| **PCR1** | Hash of the Linux kernel and bootstrap |
| **PCR2** | Hash of the application |

## Build Outputs

Each build creates a GitHub Release with:

- Tag: `<app-name>-v<version>`
- Attachments:
  - `pcr.json` - PCR values for attestation
  - `build-metadata.json` - Unified build metadata (source, image, PCR, SLSA)
  - `enclaver.yaml` - Generated enclaver configuration

### Running the Release Image

Run on an EC2 instance with Nitro Enclave support:

```bash
docker pull <ECR_REGISTRY>/nova-apps/<app-name>:<version>
docker run --rm --privileged <ECR_REGISTRY>/nova-apps/<app-name>:<version>
```

## SLSA Provenance & Verification

All container images are signed using [Sigstore cosign](https://sigstore.dev) with keyless signing. This provides SLSA Level 3 provenance guarantees.

### Verify Container Image Signature

```bash
cosign verify \
  --certificate-identity-regexp='https://github.com/sparsity-xyz/sparsity-nova-app-hub/.*' \
  --certificate-oidc-issuer='https://token.actions.githubusercontent.com' \
  <ECR_REGISTRY>/nova-apps/<app-name>:<version>@<digest>
```

### Verify Build Artifacts

1. Check the GitHub Actions run for complete logs
2. Compare PCR values in release with expected values
3. Verify the container signature using cosign

## FAQ

### Q: Can I use a private repository?

No, only public GitHub repositories are supported for transparency.

### Q: How do I update my application?

Modify the `nova-build.yaml` and submit a new PR. Update the `version` field.

### Q: Why are my PCR values different?

Possible causes:
- Different `SOURCE_DATE_EPOCH`
- Non-deterministic Dockerfile operations
- Different base image (not pinned by digest)

### Q: What instance types support Nitro Enclaves?

Most newer instance types: `m5.xlarge`, `c5.xlarge`, `r5.xlarge`, etc. with `.metal` variants having best support.

### Q: How does Enclaver work?

Enclaver packages your application Docker image into a release image containing:
1. The EIF (Enclave Image File)
2. Odyn supervisor for ingress/egress, attestation, encryption
3. Nitro CLI for enclave lifecycle management

See [Enclaver documentation](https://github.com/sparsity-xyz/enclaver) for details.

---

> **For administrators**: See [docs/nova-app-hub.md](docs/nova-app-hub.md) for AWS infrastructure setup, repository structure, and internal documentation.

## License

This project is licensed under the terms specified in the LICENSE file.
