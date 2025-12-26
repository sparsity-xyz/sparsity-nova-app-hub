# Nova App Hub

A centralized, transparent, and trustworthy build platform for AWS Nitro Enclave applications using [Enclaver](https://github.com/sparsity-xyz/enclaver).

## Overview

Nova App Hub enables developers to build their applications into AWS Nitro Enclave format through a transparent, auditable process. All builds are reproducible, ensuring consistent PCR (Platform Configuration Register) values for remote attestation.

### Key Features

- **Transparent Builds**: All build processes are visible in GitHub Actions
- **Reproducible**: Deterministic builds produce consistent PCR values
- **Trustworthy**: Source code and configurations are version-controlled and reviewed
- **Automated**: PR merge triggers automatic build pipeline
- **Verifiable**: Each release includes PCR values, source commit, and build artifacts
- **Enclaver-Powered**: Uses [Enclaver](https://github.com/sparsity-xyz/enclaver) for enclave packaging and runtime

## Architecture

```
┌────────────────────────────────────────────────────────────────────────────────┐
│                           Nova App Hub - Build Pipeline                         │
├────────────────────────────────────────────────────────────────────────────────┤
│                                                                                │
│  ┌─────────────────────────────── GitHub Actions ─────────────────────────────┐ │
│  │                                                                           │ │
│  │  1. Developer PR        2. Validation         3. Docker Build            │ │
│  │     ┌─────────┐           ┌─────────┐           ┌─────────────┐          │ │
│  │     │  Add    │ ────────▶ │ Schema  │ ────────▶ │ Build App   │          │ │
│  │     │ config  │           │ Check   │           │ Docker Image│          │ │
│  │     │  .yaml  │           │ Repo    │           │             │          │ │
│  │     └─────────┘           └─────────┘           └─────────────┘          │ │
│  │                                                        │                  │ │
│  │                                                        ▼                  │ │
│  │     ┌─────────────────┐      ┌─────────────┐      ┌─────────────┐        │ │
│  │     │  Push Release   │ ◀─── │  Enclaver   │ ◀─── │  Generate   │        │ │
│  │     │  Image to ECR   │      │    Build    │      │enclaver.yaml│        │ │
│  │     │  (EIF embedded) │      │  (EIF + RT) │      └─────────────┘        │ │
│  │     └─────────────────┘      └─────────────┘                             │ │
│  └───────────────────────────────────────────────────────────────────────────┘ │
│                                                                                │
└────────────────────────────────────────────────────────────────────────────────┘
```

## Quick Start

### 1. Create Your Configuration

Create a new directory under `apps/` with your application name:

```
apps/
└── your-app-name/
    └── nova-build.yaml
```

### 2. Configure Your Build

Create a `nova-build.yaml` file:

```yaml
# Required fields
name: your-app-name           # Must match directory name
version: 1.0.0                # Semantic version
repo: https://github.com/your-org/your-repo
branch: main

# Optional: Build configuration
build:
  directory: .                # Dockerfile location (default: root)
  dockerfile: Dockerfile      # Dockerfile name
  args:                       # Build arguments
    - name: BUILD_ENV
      value: production

# Optional: Enclave build configuration
enclave:
  debug_mode: false           # WARNING: Debug mode changes PCR values!

# Optional: Enclaver configuration
enclaver:
  ingress_port: 8000          # Your app's HTTP port
  api_port: 9000              # Internal API port (attestation, signing)
  aux_api_port: 9001          # Auxiliary API port
  memory_mb: 1500             # Enclave memory allocation
  egress_allow:               # Domains allowed for egress
    - api.example.com

# Optional: Reproducible build settings
reproducible:
  enabled: true
  # source_date_epoch: 1700000000  # Fixed timestamp (uses commit time if not set)

# Optional: Metadata
metadata:
  description: "Your application description"
  maintainer: you@example.com
  license: MIT
```

### 3. Submit a Pull Request

1. Fork this repository
2. Create your app configuration in `apps/your-app-name/nova-build.yaml`
3. Submit a pull request
4. Wait for automated validation
5. Admin merges PR → Build automatically starts

### 4. Get Your Artifacts

After build completes:

- **Release Image**: Docker image with embedded EIF in AWS ECR
- **PCR Values**: Recorded in `pcr.json`
- **Build Info**: Complete build metadata in `build-info.json`

## Configuration Reference

### Required Fields

| Field | Type | Description |
|-------|------|-------------|
| `name` | string | Application name (lowercase, numbers, hyphens) |
| `version` | string | Semantic version (e.g., 1.0.0) |
| `repo` | string | Public GitHub repository URL |
| `branch` | string | Git branch to build from |

### Optional Fields

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `commit` | string | (latest) | Specific commit hash |
| `build.directory` | string | `.` | Dockerfile directory |
| `build.dockerfile` | string | `Dockerfile` | Dockerfile name |
| `build.args` | array | `[]` | Docker build arguments |
| `enclave.debug_mode` | boolean | `false` | Enable debug mode (changes PCR!) |
| `enclaver.ingress_port` | integer | `8000` | Ingress traffic port |
| `enclaver.api_port` | integer | `9000` | Internal API port |
| `enclaver.aux_api_port` | integer | `9001` | Auxiliary API port |
| `enclaver.memory_mb` | integer | `1500` | Memory allocation (MB) |
| `enclaver.egress_allow` | array | `[]` | Allowed egress domains |
| `reproducible.enabled` | boolean | `true` | Enable reproducible builds |
| `reproducible.source_date_epoch` | integer | (commit time) | Fixed timestamp |

## Reproducible Builds & PCR Values

### What are PCR Values?

AWS Nitro Enclaves use Platform Configuration Registers (PCRs) for remote attestation:

| PCR | Description |
|-----|-------------|
| **PCR0** | Hash of the enclave image file |
| **PCR1** | Hash of the Linux kernel and bootstrap |
| **PCR2** | Hash of the application |

### Ensuring Reproducible PCR Values

To get consistent PCR values across builds:

1. **Use `SOURCE_DATE_EPOCH`**: Set a fixed timestamp in your config or let the system use the commit timestamp
2. **Pin dependencies**: Use specific versions in your Dockerfile
3. **Avoid non-deterministic operations**: No random data, sorted file operations
4. **Use digest-pinned base images**: 
   ```dockerfile
   FROM ubuntu:22.04@sha256:xxxxx
   ```

### Dockerfile Best Practices

```dockerfile
# Use digest-pinned base image for reproducibility
FROM ubuntu:22.04@sha256:xxxxx

# Accept SOURCE_DATE_EPOCH for reproducible builds
ARG SOURCE_DATE_EPOCH
ENV SOURCE_DATE_EPOCH=${SOURCE_DATE_EPOCH}

# Install packages in sorted order
RUN apt-get update && apt-get install -y --no-install-recommends \
    package1 \
    package2 \
    package3 \
    && rm -rf /var/lib/apt/lists/*

# Copy application
COPY . /app
WORKDIR /app

# Set entrypoint
ENTRYPOINT ["/app/entrypoint.sh"]
```

## Build Outputs

### GitHub Release

Each build creates a GitHub Release with:

- Tag: `<app-name>-v<version>`
- Attachments:
  - `pcr.json` - PCR values
  - `build-info.json` - Build metadata
  - `enclaver.yaml` - Generated enclaver configuration

### PCR.json Format

```json
{
  "PCR0": "abc123...",
  "PCR1": "def456...",
  "PCR2": "ghi789..."
}
```

### Release Docker Image

The release image contains everything needed to run the enclave:
- Enclaver runtime (`enclaver-run`)
- Nitro CLI
- EIF file (`/enclave/application.eif`)
- Configuration (`/enclave/enclaver.yaml`)

Run on an EC2 instance with Nitro Enclave support:

```bash
docker pull <ECR_REGISTRY>/nova-apps/<app-name>:<version>
docker run --rm --privileged <ECR_REGISTRY>/nova-apps/<app-name>:<version>
```

### S3 Artifacts

```
s3://nova-app-hub-artifacts/builds/<app-name>/<version>/
├── pcr.json
├── build-info.json
├── build-output.txt
└── enclaver.yaml
```

## AWS Infrastructure Setup

### Prerequisites

- AWS Account
- GitHub repository admin access

### Deploy Infrastructure

1. Deploy the CloudFormation stack:

```bash
aws cloudformation create-stack \
  --stack-name nova-app-hub \
  --template-body file://aws/cloudformation/infrastructure.yml \
  --capabilities CAPABILITY_NAMED_IAM \
  --parameters \
    ParameterKey=ProjectName,ParameterValue=nova-app-hub \
    ParameterKey=GitHubOrg,ParameterValue=your-org \
    ParameterKey=GitHubRepo,ParameterValue=your-repo
```

2. Get the outputs:

```bash
aws cloudformation describe-stacks \
  --stack-name nova-app-hub \
  --query 'Stacks[0].Outputs'
```

3. Configure GitHub Secrets:

| Secret | Value |
|--------|-------|
| `AWS_ACCESS_KEY_ID` | From CloudFormation output |
| `AWS_SECRET_ACCESS_KEY` | From CloudFormation output |

4. Update workflow environment variables in `.github/workflows/build-on-merge.yml`:

```yaml
env:
  AWS_REGION: <your-region>
  ECR_REGISTRY: <account-id>.dkr.ecr.<region>.amazonaws.com
  ECR_REPOSITORY_PREFIX: nova-apps
  S3_BUCKET: <artifacts-bucket-name>
```

## Repository Structure

```
nova-app-hub/
├── .github/
│   └── workflows/
│       ├── pr-validation.yml       # PR validation
│       └── build-on-merge.yml      # Build pipeline (Enclaver)
├── apps/
│   ├── _example/                   # Example configuration
│   │   └── nova-build.yaml
│   └── your-app/
│       ├── nova-build.yaml         # Build configuration
│       └── BUILD_INFO.md           # Auto-generated build info
├── aws/
│   └── cloudformation/
│       └── infrastructure.yml      # AWS infrastructure
├── schemas/
│   └── nova-build.schema.json      # JSON Schema
├── scripts/
│   └── validate-config.sh          # Local validation
└── README.md
```

## Security

- **Admin-only merge**: Only administrators can merge PRs
- **Public repos only**: Only public GitHub repositories allowed
- **Transparent builds**: All logs visible in GitHub Actions
- **Reproducible**: Same source produces same PCR values
- **Attestation**: PCR values enable remote attestation

## Verification

### Verify Build Artifacts

1. Check the GitHub Actions run for complete logs
2. Compare PCR values in release with expected values
3. Verify image digest matches

### Remote Attestation

Use the PCR values from `pcr.json` in your AWS KMS key policy:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": { "AWS": "arn:aws:iam::ACCOUNT:role/enclave-role" },
      "Action": "kms:Decrypt",
      "Resource": "*",
      "Condition": {
        "StringEqualsIgnoreCase": {
          "kms:RecipientAttestation:PCR0": "<PCR0-value>"
        }
      }
    }
  ]
}
```

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
- Debug mode enabled

### Q: What instance types support Nitro Enclaves?

Most newer instance types: `m5.xlarge`, `c5.xlarge`, `r5.xlarge`, etc. with `.metal` variants having best support.

### Q: How does Enclaver work?

Enclaver packages your application Docker image into a release image containing:
1. The EIF (Enclave Image File)
2. Odyn supervisor for ingress/egress, attestation, encryption
3. Nitro CLI for enclave lifecycle management

See [Enclaver documentation](https://github.com/sparsity-xyz/enclaver) for details.

## Contributing

1. Fork this repository
2. Create your feature branch
3. Submit a pull request

## License

This project is licensed under the terms specified in the LICENSE file.
