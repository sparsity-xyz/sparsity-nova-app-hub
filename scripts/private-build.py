#!/usr/bin/env python3
"""Build one scoped private source archive on an ephemeral GitHub-hosted runner."""
import io
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tarfile
import time
import urllib.request

STARTED = time.monotonic()
ROOT = Path.cwd()


def request(path, payload=None):
    """Keep the scoped capability out of URLs and build arguments."""
    url = os.environ['NOVA_ORIGIN'].rstrip('/') + '/api/private-builds/' + os.environ['BUILD_ID'] + path
    req = urllib.request.Request(url, data=json.dumps(payload).encode() if payload is not None else None,
        headers={'Authorization': 'Bearer ' + os.environ['BUILD_TICKET'], 'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=180) as r:
        limit = 256 * 1024 * 1024 if path.startswith('/source?') else 1024 * 1024
        data = r.read(limit + 1)
        if len(data) > limit:
            raise ValueError('Private build response exceeds its size limit')
        return data


def run(args, *, unprivileged=False, **kwargs):
    """Fail on any build or publication error and print bounded progress."""
    print('Running ' + args[0], flush=True)
    if unprivileged:
        kwargs['env'] = {k: v for k, v in os.environ.items()
            if not k.startswith(('AWS_', 'COSIGN_', 'ACTIONS_', 'GITHUB_'))
            and k not in ('BUILD_TICKET', 'GH_TOKEN')}
    return subprocess.run(args, check=True, **kwargs)


def extract(data, destination):
    """Reject links and paths escaping the archive's single source root."""
    destination.mkdir()
    with tarfile.open(fileobj=io.BytesIO(data), mode='r:gz') as archive:
        rows = archive.getmembers()
        roots = {Path(m.name).parts[0] for m in rows if Path(m.name).parts}
        if len(roots) != 1 or len(rows) > 100000 or sum(m.size for m in rows) > 1024 * 1024 * 1024:
            raise ValueError('Invalid or oversized source archive')
        for m in rows:
            parts = Path(m.name).parts
            if m.issym() or m.islnk() or not (m.isfile() or m.isdir()) or '..' in parts or Path(m.name).is_absolute():
                raise ValueError('Unsafe source archive member')
            if len(parts) == 1:
                continue
            m.name = str(Path(*parts[1:]))
            archive.extract(m, destination, filter='data')


def scoped_image(info):
    """Validate the control plane's destination, including its environment prefix."""
    registry = info['registry']
    if not re.fullmatch(r'\d{12}\.dkr\.ecr\.[a-z0-9-]+\.amazonaws\.com', registry):
        raise ValueError('Private ECR registry required')
    app_sqid = info['app_sqid']
    if not re.fullmatch(r'[a-z0-9]+', app_sqid):
        raise ValueError('Invalid application identifier')
    image = info['image_uri']
    prefix = r'[a-z0-9]+(?:[._-][a-z0-9]+)*(?:/[a-z0-9]+(?:[._-][a-z0-9]+)*)*'
    if not re.fullmatch(re.escape(registry + '/') + prefix + re.escape('/' + app_sqid + ':build-' + os.environ['BUILD_ID'] + '-') + r'[a-f0-9]{12}', image):
        raise ValueError('Invalid scoped private image destination')
    return image


def build_image():
    """Build on the ephemeral runner before publication credentials are installed."""
    import yaml
    run_id = os.environ['GITHUB_RUN_ID']
    build_id = os.environ['BUILD_ID']
    if not re.fullmatch(r'[1-9][0-9]*', build_id):
        raise ValueError('Invalid build identifier')
    print('::add-mask::' + os.environ['BUILD_TICKET'], flush=True)
    info = json.loads(request('/input?run_id=' + run_id))
    try:
        source = ROOT / 'private-source'
        extract(request('/source?run_id=' + run_id), source)
        config = yaml.safe_load(info['build_config'])
        build = config.get('build', {})
        directory = build.get('directory') or '.'
        # The Nova directory picker represents the repository root as '/'.
        context = (source / ('.' if directory == '/' else directory)).resolve()
        dockerfile = (context / build.get('dockerfile', 'Dockerfile')).resolve()
        if not context.is_relative_to(source.resolve()) or not dockerfile.is_relative_to(context):
            raise ValueError('Build directory escapes the source archive')
        local = 'nova-private-input:' + build_id
        # Customer Dockerfiles receive no GitHub, AWS, job-ticket or signing credentials.
        args = ['docker', 'build', '--platform', 'linux/amd64', '--no-cache', '-t', local, '-f', str(dockerfile)]
        for item in build.get('args', []):
            name = item['name']
            if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', name):
                raise ValueError('Invalid build argument name')
            args += ['--build-arg', name + '=' + str(item['value'])]
        args.append(str(context))
        run(args, unprivileged=True)
        capsule = yaml.safe_load(info['capsule_config'])
        image = scoped_image(info)
        capsule['target'] = image
        capsule['sources'] = {**capsule.get('sources', {}), 'app': local}
        # Preload through Docker CLI; Capsule's direct pull does not pass registry auth.
        for name in ('capsule-runtime', 'capsule-shell'):
            base = capsule['sources'].get(name) or 'public.ecr.aws/d4t4u8d2/sparsity-ai/' + name + ':latest'
            run(['docker', 'pull', '--platform', 'linux/amd64', base], unprivileged=True)
            resolved = run(['docker', 'image', 'inspect', '--format={{index .RepoDigests 0}}', base],
                unprivileged=True, capture_output=True, text=True).stdout.strip()
            if not re.fullmatch(r'.+@sha256:[0-9a-f]{64}', resolved):
                raise ValueError('Capsule base image did not resolve to a digest')
            capsule['sources'][name] = resolved
        run(['docker', 'pull', '--platform', 'linux/amd64', 'public.ecr.aws/d4t4u8d2/sparsity-ai/nitro-cli:latest'], unprivileged=True)
        output = ROOT / 'private-output'
        output.mkdir()
        (output / 'capsule.yaml').write_text(yaml.safe_dump(capsule))
        try:
            capsule_run = run(['capsule-cli', 'build', '--file', 'capsule.yaml'], cwd=output, unprivileged=True, capture_output=True, text=True)
        except subprocess.CalledProcessError as exc:
            print(exc.stdout or '', flush=True)
            print(exc.stderr or '', flush=True)
            raise
        text = capsule_run.stdout + capsule_run.stderr
        measurements = {}
        for i in range(3):
            match = re.search(r'PCR' + str(i) + r'["\s:]+([a-f0-9]{96})', text)
            if not match:
                raise ValueError('Capsule did not produce valid PCR measurements')
            measurements['pcr' + str(i)] = match.group(1)
        run(['docker', 'image', 'inspect', image], stdout=subprocess.DEVNULL)
        (output / 'pending-result.json').write_text(json.dumps({'info': info, 'measurements': measurements}))
    except Exception:
        report_failure()
        raise


def publish_image():
    """Only this workflow step receives AWS and signing credentials."""
    pending = json.loads((ROOT / 'private-output/pending-result.json').read_text())
    info, measurements = pending['info'], pending['measurements']
    image = scoped_image(info)
    registry = info['registry']
    try:
        password = run(['aws', 'ecr', 'get-login-password', '--region', registry.split('.')[3]], capture_output=True, text=True).stdout
        run(['docker', 'login', '--username', 'AWS', '--password-stdin', registry], input=password, text=True)
        repository = image.split('/', 1)[1].rsplit(':', 1)[0]
        found = subprocess.run(['aws', 'ecr', 'describe-repositories', '--region', registry.split('.')[3], '--repository-names', repository], capture_output=True, text=True)
        if found.returncode:
            run(['aws', 'ecr', 'create-repository', '--region', registry.split('.')[3], '--repository-name', repository, '--image-tag-mutability', 'IMMUTABLE'], stdout=subprocess.DEVNULL)
        run(['docker', 'push', image])
        digest = run(['docker', 'inspect', '--format={{index .RepoDigests 0}}', image], capture_output=True, text=True).stdout.strip().split('@')[-1]
        if not re.fullmatch(r'sha256:[0-9a-f]{64}', digest):
            raise ValueError('No immutable private image digest returned')
        # An offline/private signature avoids publishing source identity to Rekor.
        run(['cosign', 'sign', '--yes', '--tlog-upload=false', '--key', 'env://COSIGN_PRIVATE_KEY', image + '@' + digest])
        evidence = {'git_commit': info['commit'], 'image_uri': image, 'image_digest': digest,
            'builder': os.environ['GITHUB_REPOSITORY'], 'run_id': os.environ['GITHUB_RUN_ID'],
            'signature': 'private-key; transparency-log upload disabled', **measurements}
        request('/result', {'status': 'success', 'run_id': os.environ['GITHUB_RUN_ID'], 'attestation': evidence})
        print('Private build and evidence delivery complete.', flush=True)
    except Exception:
        report_failure()
        raise
    finally:
        print(f'Total time: {time.monotonic() - STARTED:.1f}s', flush=True)


def report_failure():
    """Do not mask a build error when the callback is unreachable or already terminal."""
    try:
        request('/result', {'status': 'failed', 'run_id': os.environ['GITHUB_RUN_ID']})
    except Exception:
        print('Failure callback unavailable; Nova will expire the scoped build.', flush=True)


if __name__ == '__main__':
    actions = {'build': build_image, 'publish': publish_image, 'report-failure': report_failure}
    if len(sys.argv) != 2 or sys.argv[1] not in actions:
        raise SystemExit('Expected build, publish, or report-failure')
    actions[sys.argv[1]]()
