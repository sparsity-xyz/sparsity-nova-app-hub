# Nova App Hub - Internal Documentation

This document contains deployment, development, and infrastructure configuration information for Nova App Hub administrators.

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
| `DOCKERHUB_USERNAME` | Docker Hub username (to avoid rate limits) |
| `DOCKERHUB_TOKEN` | Docker Hub access token ([create here](https://hub.docker.com/settings/security)) |

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
├── docs/
│   └── nova-app-hub.md             # This file
├── schemas/
│   └── nova-build.schema.json      # JSON Schema
├── scripts/
│   └── validate-config.sh          # Local validation
└── README.md
```

## S3 Artifacts

Build artifacts are stored in S3:

```
s3://nova-app-hub-artifacts/builds/<app-name>/<version>/
├── pcr.json
├── build-metadata.json
├── build-output.txt
└── enclaver.yaml
```

## Remote Attestation

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

## Security Model

- **Admin-only merge**: Only administrators can merge PRs
- **Public repos only**: Only public GitHub repositories allowed
- **Transparent builds**: All logs visible in GitHub Actions
- **Attestation**: PCR values enable remote attestation
- **SLSA Level 3**: Builds are signed using Sigstore cosign with keyless signing

## Contributing

1. Fork this repository
2. Create your feature branch
3. Submit a pull request
