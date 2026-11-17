#!/usr/bin/env python3
"""Build one scoped private source archive on an ephemeral GitHub-hosted runner."""
import io
import json
import os
from pathlib import Path
import re
import subprocess
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
        return r.read()


def run(args, **kwargs):
    """Fail on any build or publication error and print bounded progress."""
    print('Running ' + args[0], flush=True)
    return subprocess.run(args, check=True, **kwargs)


def extract(data, destination):
    """Reject links and paths escaping the archive's single source root."""
    destination.mkdir()
    with tarfile.open(fileobj=io.BytesIO(data), mode='r:gz') as archive:
        rows = archive.getmembers()
        roots = {Path(m.name).parts[0] for m in rows if Path(m.name).parts}
        if len(roots) != 1 or sum(m.size for m in rows) > 1024 * 1024 * 1024:
            raise ValueError('Invalid or oversized source archive')
        for m in rows:
            parts = Path(m.name).parts
            if m.issym() or m.islnk() or not (m.isfile() or m.isdir()) or '..' in parts or Path(m.name).is_absolute():
                raise ValueError('Unsafe source archive member')
            if len(parts) == 1:
                continue
            m.name = str(Path(*parts[1:]))
            archive.extract(m, destination, filter='data')


def main():
    """Separate untrusted Dockerfile execution from privileged publication/signing."""
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
        context = (source / build.get('directory', '.')).resolve()
        dockerfile = (context / build.get('dockerfile', 'Dockerfile')).resolve()
        if not context.is_relative_to(source.resolve()) or not dockerfile.is_relative_to(context):
            raise ValueError('Build directory escapes the source archive')
        local = 'nova-private-input:' + build_id
        # Customer Dockerfiles receive no GitHub, AWS, job-ticket or signing credentials.
        args = ['docker', 'build', '--no-cache', '-t', local, '-f', str(dockerfile)]
        for item in build.get('args', []):
            name = item['name']
            if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', name):
                raise ValueError('Invalid build argument name')
            args += ['--build-arg', name + '=' + str(item['value'])]
        args.append(str(context))
        run(args)
        capsule = yaml.safe_load(info['capsule_config'])
        registry = info['registry']
        if not re.fullmatch(r'\d{12}\.dkr\.ecr\.[a-z0-9-]+\.amazonaws\.com', registry):
            raise ValueError('Private ECR registry required')
        # The destination is supplied by the authenticated control plane.
        app_sqid = info['app_sqid']
        if not re.fullmatch(r'[A-Za-z0-9]+', app_sqid):
            raise ValueError('Invalid application identifier')
        image = info['image_uri']
        if not re.fullmatch(re.escape(registry + '/nova-private/' + app_sqid + ':build-' + build_id + '-') + r'[a-f0-9]{12}', image):
            raise ValueError('Invalid scoped private image destination')
        capsule['target'] = image
        capsule['sources'] = {'app': local}
        output = ROOT / 'private-output'
        output.mkdir()
        (output / 'capsule.yaml').write_text(yaml.safe_dump(capsule))
        capsule_run = run(['capsule-cli', 'build', '--file', 'capsule.yaml'], cwd=output, capture_output=True, text=True)
        text = capsule_run.stdout + capsule_run.stderr
        measurements = {}
        for i in range(3):
            match = re.search(r'PCR' + str(i) + r'["\s:]+([a-f0-9]{96})', text)
            if not match:
                raise ValueError('Capsule did not produce valid PCR measurements')
            measurements['pcr' + str(i)] = match.group(1)
        run(['docker', 'image', 'inspect', image], stdout=subprocess.DEVNULL)
        # Credentials are requested only after customer code has finished building.
        password = run(['aws', 'ecr', 'get-login-password', '--region', registry.split('.')[3]], capture_output=True, text=True).stdout
        run(['docker', 'login', '--username', 'AWS', '--password-stdin', registry], input=password, text=True)
        repository = 'nova-private/' + app_sqid
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
            'builder': os.environ['GITHUB_REPOSITORY'], 'run_id': run_id,
            'signature': 'private-key; transparency-log upload disabled', **measurements}
        request('/result', {'status': 'success', 'run_id': run_id, 'attestation': evidence})
        print('Private build and evidence delivery complete.', flush=True)
    except Exception:
        request('/result', {'status': 'failed', 'run_id': run_id})
        raise
    finally:
        print(f'Total time: {time.monotonic() - STARTED:.1f}s', flush=True)


if __name__ == '__main__':
    main()
