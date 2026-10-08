"""Nova lab: declared materials, disconnected Docker/Capsule build, signed record.

The GitHub runner is the explicitly trusted platform. Container layers include
their transitive file dependencies. No third-party Actions execute. Build-time
clock/randomness is allowed; reproducible output bytes are not claimed.
"""
import base64
import gzip
import hashlib
import json
import os
import platform
import re
import shutil
import socket
import subprocess
import sys
import tarfile
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
OUT = HERE / 'out'
LOCK = HERE / 'inputs.lock.json'
APP = 'nova-build-lab-20261008'
REPO = 'sparsity-xyz/sparsity-nova-app-hub'
WORKFLOW = '.github/workflows/build-on-merge.yml'
IMAGE = 'public.ecr.aws/d4t4u8d2/sparsity-xyz/nova-apps/' + APP
HELPER = 'public.ecr.aws/d4t4u8d2/sparsity-ai/nitro-cli:latest'
POLICY = 'nova-offline-inputs-v1'
IMAGE_KEYS = {'app_base', 'capsule_runtime', 'capsule_shell', 'nitro_cli'}


def require(ok, message):
    if not ok:
        raise ValueError(message)


def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for b in iter(lambda: f.read(1024 * 1024), b''):
            h.update(b)
    return h.hexdigest()


def canonical(obj):
    return (json.dumps(obj, sort_keys=True, separators=(',', ':'), ensure_ascii=False) + '\n').encode()


def write(name, obj):
    (OUT / name).write_bytes(canonical(obj))


def read(name):
    return json.loads((OUT / name).read_bytes())


def run(*args, capture=False, cwd=None, env=None):
    print('+', ' '.join(map(str, args)), flush=True)
    r = subprocess.run(list(map(str, args)), check=True, text=True, cwd=cwd, env=env,
                       stdout=subprocess.PIPE if capture else None)
    return r.stdout.strip() if capture else None


def inspect(image):
    return json.loads(run('docker', 'image', 'inspect', image, capture=True))[0]


def validate_lock(lock):
    require(lock['schema'] == 1 and lock['policy'] == POLICY, 'Unsupported input policy')
    require(lock['app'] == APP and lock['platform'] == 'linux/amd64', 'Wrong app/platform')
    require(lock['external_actions'] == [], 'Undeclared external Actions')
    require(set(lock['images']) == IMAGE_KEYS, 'Missing or extra image material')
    for value in lock['images'].values():
        require(re.fullmatch(r'[^\s]+@sha256:[0-9a-f]{64}', value['reference']), 'Unpinned image')
        require(value['platform'] == 'linux/amd64', 'Wrong material platform')
    require(re.fullmatch(r'[0-9a-f]{40}', lock['source']['commit']), 'Unpinned source commit')
    require(lock['source']['repository'] == 'https://github.com/' + REPO, 'Unexpected source repository')
    require(lock['source']['directory'] == 'apps/' + APP + '/source', 'Unexpected source context')
    require(lock['source']['dockerfile'] == 'Dockerfile', 'Unexpected Dockerfile')
    for item, key in ((lock['capsule_cli'], 'archive_sha256'), (lock['cosign'], 'sha256')):
        require(re.fullmatch('[0-9a-f]{64}', item[key]), 'Unpinned tool archive')
        require(item['url'].startswith('https://github.com/') and '/releases/download/v' in item['url'],
                'Tool must use explicit release URL')
    require(lock['trusted_platform']['label'] == 'ubuntu-24.04', 'Unexpected runner policy')


def inventory(directory):
    files = {}
    for path in sorted(Path(directory).rglob('*')):
        require(not path.is_symlink(), 'Symlink not allowed in build context')
        if path.is_file():
            files[path.relative_to(directory).as_posix()] = {
                'sha256': sha(path), 'size': path.stat().st_size,
                'mode': oct(path.stat().st_mode & 0o777)}
    return files


def verify_inventory(directory, expected):
    require(inventory(directory) == expected, 'Source/context inventory mismatch')


def download(url, name, expected):
    dest = OUT / name
    with urllib.request.urlopen(url, timeout=120) as src, dest.open('wb') as dst:
        shutil.copyfileobj(src, dst)
    require(sha(dest) == expected, 'Downloaded tool hash mismatch: ' + name)


def archive_git(repo, commit, name):
    with (OUT / name).open('wb') as f:
        subprocess.run(['git', '-C', str(repo), 'archive', '--format=tar', commit], stdout=f, check=True)
    raw = subprocess.run(['git', '-C', str(repo), 'cat-file', 'commit', commit], capture_output=True, check=True).stdout
    (OUT / name.replace('.tar', '.commit')).write_bytes(raw)


def extract_regular(archive, destination):
    destination.mkdir()
    with tarfile.open(archive) as tf:
        for member in tf.getmembers():
            name = Path(member.name)
            require(not name.is_absolute() and '..' not in name.parts, 'Unsafe source archive path')
            target = destination / name
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                require(member.isfile(), 'Source archive has a non-regular material')
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(tf.extractfile(member).read())
                target.chmod(member.mode & 0o777)


def platform_record():
    tools = {}
    for name in ('python3', 'docker', 'dockerd', 'containerd', 'runc', 'bash', 'git', 'unshare', 'ip', 'aws', 'gh'):
        path = shutil.which(name)
        if not path:
            continue
        path = Path(path).resolve()
        libs = {}
        r = subprocess.run(['ldd', str(path)], capture_output=True, text=True)
        for lib in re.findall(r'(/[\w./+:-]+)', r.stdout):
            if Path(lib).is_file():
                libs[lib] = sha(lib)
        tools[name] = {'path': str(path), 'sha256': sha(path), 'linked_libraries': libs}
    write('runner-platform.json', {
        'trust': 'GitHub-hosted execution substrate; not an attestation of the host OS',
        'image_os': os.environ.get('ImageOS'), 'image_version': os.environ.get('ImageVersion'),
        'kernel': platform.uname()._asdict(), 'os_release': Path('/etc/os-release').read_text(),
        'docker_version': json.loads(run('docker', 'version', '--format', '{{json .}}', capture=True)),
        'tools': tools, 'packages': run('dpkg-query', '-W', '-f=${binary:Package}\t${Version}\n', capture=True)})


def prepare():
    lock = json.loads(LOCK.read_bytes()); validate_lock(lock)
    require(not OUT.exists(), 'Output directory must be new')
    require(os.environ['GITHUB_REPOSITORY'] == REPO, 'Wrong builder repository')
    require(os.environ['GITHUB_SHA'] == os.environ['GITHUB_WORKFLOW_SHA'], 'Workflow/checkout mismatch')
    require(run('git', 'rev-parse', 'HEAD', capture=True) == os.environ['GITHUB_WORKFLOW_SHA'], 'Wrong checkout')
    require(not run('git', 'status', '--porcelain', capture=True), 'Dirty builder checkout')
    OUT.mkdir()
    shutil.copyfile(LOCK, OUT / 'inputs.lock.json')
    archive_git(ROOT, os.environ['GITHUB_WORKFLOW_SHA'], 'builder-source.tar')
    builder_snapshot = OUT / 'builder-snapshot'
    extract_regular(OUT / 'builder-source.tar', builder_snapshot)
    write('builder-files.json', inventory(builder_snapshot))
    source_repo = OUT / 'source-repo'
    run('git', 'init', source_repo)
    run('git', '-C', source_repo, '-c', 'credential.helper=', 'fetch', '--depth=1',
        lock['source']['repository'] + '.git', lock['source']['commit'])
    require(run('git', '-C', source_repo, 'rev-parse', 'FETCH_HEAD', capture=True) == lock['source']['commit'],
            'Resolved source commit mismatch')
    epoch = int(run('git', '-C', source_repo, 'show', '-s', '--format=%ct', 'FETCH_HEAD', capture=True))
    archive_git(source_repo, 'FETCH_HEAD', 'application-source.tar')
    extract_regular(OUT / 'application-source.tar', OUT / 'application-snapshot')
    write('application-files.json', inventory(OUT / 'application-snapshot'))
    context = OUT / 'context'
    shutil.copytree(OUT / 'application-snapshot' / lock['source']['directory'], context)
    write('context-files.json', inventory(context))
    # The complete closed-input lab app has no package installer or remote ADD.
    dockerfile = (context / 'Dockerfile').read_text()
    allowed = {'ARG', 'FROM', 'ENV', 'WORKDIR', 'COPY', 'USER', 'EXPOSE', 'ENTRYPOINT'}
    instructions = [line.split()[0] for line in dockerfile.splitlines() if line.strip() and not line.startswith('#')]
    require(all(x in allowed for x in instructions), 'Unsupported dependency-fetching Dockerfile instruction')
    require(set(inventory(context)) == {'Dockerfile', 'server.py', 'version.json'}, 'Undeclared source files')
    download(lock['capsule_cli']['url'], 'capsule-cli.tar.gz', lock['capsule_cli']['archive_sha256'])
    with tarfile.open(OUT / 'capsule-cli.tar.gz') as tf:
        entries = [m for m in tf.getmembers() if Path(m.name).name == 'capsule-cli' and m.isfile()]
        require(len(entries) == 1, 'Unexpected Capsule archive')
        (OUT / 'capsule-cli').write_bytes(tf.extractfile(entries[0]).read())
    (OUT / 'capsule-cli').chmod(0o755)
    download(lock['cosign']['url'], 'cosign-linux-amd64', lock['cosign']['sha256'])
    (OUT / 'cosign-linux-amd64').chmod(0o755)
    version = run(OUT / 'capsule-cli', '--version', capture=True)
    require(lock['capsule_cli']['version'] in version, 'Wrong Capsule version')
    token = os.environ.get('DOCKERHUB_TOKEN', '')
    if token:
        subprocess.run(['docker', 'login', '--username', os.environ['DOCKERHUB_USERNAME'], '--password-stdin'],
                       input=token, text=True, check=True, stdout=subprocess.DEVNULL)
    images = {}
    try:
        for key, item in sorted(lock['images'].items()):
            ref = item['reference']
            for attempt in range(3):
                try:
                    run('docker', 'pull', '--platform', lock['platform'], ref)
                    break
                except subprocess.CalledProcessError:
                    if attempt == 2:
                        raise
                    time.sleep(5 * (attempt + 1))
            obj = inspect(ref)
            require(obj['Architecture'] == 'amd64' and obj['Os'] == 'linux', 'Image platform mismatch')
            require(any(r.endswith('@' + ref.split('@')[1]) for r in obj['RepoDigests']), 'Pulled image digest mismatch')
            alias = 'nova-input-' + key.replace('_', '-') + ':locked'
            run('docker', 'tag', obj['Id'], alias)
            name = 'image-' + key + '.tar.gz'
            with (OUT / name).open('wb') as f:
                with gzip.GzipFile(filename='', mode='wb', fileobj=f, mtime=0, compresslevel=1) as z:
                    proc = subprocess.Popen(['docker', 'save', alias], stdout=subprocess.PIPE)
                    shutil.copyfileobj(proc.stdout, z)
                    require(proc.wait() == 0, 'Image archive export failed')
            images[key] = {'reference': ref, 'image_id': obj['Id'], 'alias': alias,
                           'rootfs_diff_ids': obj['RootFS']['Layers'], 'archive': name,
                           'archive_sha256': sha(OUT / name)}
    finally:
        if token:
            subprocess.run(['docker', 'logout'], stdout=subprocess.DEVNULL, check=True)
    platform_record()
    write('build-materials.json', {
        'policy': POLICY, 'lock_sha256': sha(LOCK), 'platform': lock['platform'], 'images': images,
        'builder': {'repository': 'https://github.com/' + REPO, 'commit': os.environ['GITHUB_WORKFLOW_SHA'],
                    'workflow': WORKFLOW, 'workflow_sha256': sha(ROOT / WORKFLOW),
                    'archive_sha256': sha(OUT / 'builder-source.tar'), 'inventory_sha256': sha(OUT / 'builder-files.json')},
        'source': {**lock['source'], 'archive_sha256': sha(OUT / 'application-source.tar'),
                   'inventory_sha256': sha(OUT / 'application-files.json'),
                   'context_inventory_sha256': sha(OUT / 'context-files.json')},
        'tools': {'capsule_archive_sha256': sha(OUT / 'capsule-cli.tar.gz'),
                  'capsule_binary_sha256': sha(OUT / 'capsule-cli'), 'cosign_sha256': sha(OUT / 'cosign-linux-amd64')},
        'build_args': {'SOURCE_COMMIT': lock['source']['commit'], 'SOURCE_DATE_EPOCH': str(epoch),
                       'BASE_IMAGE': images['app_base']['alias']},
        'trusted_platform': lock['trusted_platform'], 'nondeterminism': lock['nondeterminism'],
        'external_actions': [], 'run': {'id': os.environ['GITHUB_RUN_ID'], 'attempt': os.environ['GITHUB_RUN_ATTEMPT'],
            'ref': os.environ['GITHUB_REF'], 'workflow_ref': os.environ['GITHUB_WORKFLOW_REF']}})


def assert_materials(materials):
    require(materials['policy'] == POLICY, 'Wrong materials policy')
    require(set(materials['images']) == IMAGE_KEYS, 'Missing image material')
    require(sha(OUT / 'inputs.lock.json') == materials['lock_sha256'], 'Lock hash mismatch')
    require(sha(OUT / 'capsule-cli') == materials['tools']['capsule_binary_sha256'], 'Capsule binary changed')
    require(sha(OUT / 'capsule-cli.tar.gz') == materials['tools']['capsule_archive_sha256'], 'Capsule archive changed')
    verify_inventory(OUT / 'context', read('context-files.json'))
    for item in materials['images'].values():
        require(sha(OUT / item['archive']) == item['archive_sha256'], 'Image archive changed')


def assert_images(materials):
    for item in materials['images'].values():
        require(inspect(item['alias'])['Id'] == item['image_id'], 'Loaded image identity mismatch')
    require(inspect(HELPER)['Id'] == materials['images']['nitro_cli']['image_id'], 'Nitro helper alias changed')


def network_test():
    results = []
    for address in ('1.1.1.1', '2606:4700:4700::1111'):
        try:
            s = socket.create_connection((address, 443), timeout=2)
        except OSError as e:
            results.append({'address': address, 'blocked': True, 'reason': str(e)})
        else:
            s.close()
            raise RuntimeError('External network unexpectedly reachable')
    return results


def offline():
    lock = read('inputs.lock.json'); validate_lock(lock)
    materials = read('build-materials.json'); assert_materials(materials)
    before = run('ip', '-j', 'address', capture=True)
    require({n['ifname'] for n in json.loads(before)} == {'lo'}, 'Builder has a non-loopback interface')
    run('ip', 'link', 'set', 'lo', 'up')
    write('network-isolation.json', {'mechanism': 'unshare --net; isolated Docker daemon in same namespace',
                                   'interfaces_before_daemon': json.loads(before), 'host_probes': network_test(),
                                   'secrets_in_build_environment': False})
    daemon_root = Path('/tmp/nova-offline-docker')
    daemon_root.mkdir()
    (daemon_root / 'daemon.json').write_text('{}\n')
    log = (OUT / 'docker-daemon.log').open('w')
    daemon = subprocess.Popen(['dockerd', '--config-file=' + str(daemon_root / 'daemon.json'),
        '--data-root=' + str(daemon_root / 'data'), '--exec-root=' + str(daemon_root / 'exec'),
        '--pidfile=' + str(daemon_root / 'daemon.pid'), '--host=unix:///var/run/docker.sock',
        '--iptables=false', '--ip6tables=false', '--ip-forward=false', '--ip-masq=false',
        '--storage-driver=overlay2'], stdout=log, stderr=subprocess.STDOUT)
    try:
        for _ in range(60):
            if daemon.poll() is not None:
                raise RuntimeError('Offline Docker failed: ' + (OUT / 'docker-daemon.log').read_text()[-8000:])
            r = subprocess.run(['docker', 'info'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            if r.returncode == 0:
                break
            time.sleep(1)
        else:
            raise RuntimeError('Offline Docker not ready')
        require(not json.loads(run('docker', 'image', 'ls', '--format', 'json', capture=True) or '[]'),
                'Offline Docker must begin without cached images')
        for item in materials['images'].values():
            run('docker', 'load', '-i', OUT / item['archive'])
        run('docker', 'tag', materials['images']['nitro_cli']['alias'], HELPER)
        assert_images(materials)
        # Exercise rejection paths against this daemon and the real context.
        negative = []
        source_file = OUT / 'context/server.py'
        original = source_file.read_bytes()
        try:
            source_file.write_bytes(original + b'\n# unexpected build input\n')
            try:
                verify_inventory(OUT / 'context', read('context-files.json'))
            except ValueError as e:
                negative.append({'name': 'Reject changed source bytes before build', 'status': 'PASS', 'reason': str(e)})
            else:
                raise RuntimeError('Changed source was accepted')
        finally:
            source_file.write_bytes(original)
        run('docker', 'tag', materials['images']['app_base']['alias'], HELPER)
        try:
            try:
                assert_images(materials)
            except ValueError as e:
                negative.append({'name': 'Reject helper alias rebound to different image', 'status': 'PASS', 'reason': str(e)})
            else:
                raise RuntimeError('Rebound helper was accepted')
        finally:
            run('docker', 'tag', materials['images']['nitro_cli']['alias'], HELPER)
        write('build-rejection-tests.json', negative)
        # An actual container must also fail to reach the Internet.
        probe = "import socket; socket.create_connection(('1.1.1.1',443),2)"
        r = subprocess.run(['docker', 'run', '--rm', '--entrypoint', 'python3',
            materials['images']['app_base']['alias'], '-c', probe], capture_output=True, text=True)
        require(r.returncode != 0 and ('Network is unreachable' in r.stderr or 'timed out' in r.stderr),
                'Container network isolation probe did not fail as expected: ' + r.stderr)
        isolation = read('network-isolation.json')
        isolation['container_probe'] = {'blocked': True, 'exit_code': r.returncode, 'stderr': r.stderr}
        write('network-isolation.json', isolation)
        command = ['docker', 'build', '--platform', lock['platform'], '--network=none', '--pull=false', '--no-cache']
        for key, value in sorted(materials['build_args'].items()):
            command.extend(['--build-arg', key + '=' + value])
        command.extend(['-t', 'nova-lab-app:built', str(OUT / 'context')])
        # Docker's legacy builder is sufficient for the lab's COPY-only recipe.
        environment = dict(os.environ, DOCKER_BUILDKIT='0')
        write('build-command.json', {'argv': command, 'environment': environment,
                                    'network': 'none', 'cache': False})
        run(*command, env=environment)
        app_id = inspect('nova-lab-app:built')['Id']
        effective = lock['capsule']
        effective['target'] = 'nova-lab-release:built'
        effective['sources'] = {'app': app_id, 'capsule-runtime': materials['images']['capsule_runtime']['image_id'],
                                'capsule-shell': materials['images']['capsule_shell']['image_id']}
        write('capsule.yaml', effective)
        with (OUT / 'build-summary.json').open('w') as stdout, (OUT / 'build-output.txt').open('w') as stderr:
            subprocess.run([str(OUT / 'capsule-cli'), 'build', '-f', str(OUT / 'capsule.yaml')],
                           stdout=stdout, stderr=stderr, check=True)
        summary = read('build-summary.json')
        for key, material in [('App', None), ('CapsuleRuntime', 'capsule_runtime'),
                              ('CapsuleShell', 'capsule_shell'), ('NitroCLI', 'nitro_cli')]:
            expected = app_id if material is None else materials['images'][material]['image_id']
            require(summary['Sources'][key]['ID'] == expected, 'Capsule resolved unexpected ' + key)
        for key in ('PCR0', 'PCR1', 'PCR2'):
            require(re.fullmatch('[0-9a-f]{96}', summary['Measurements'][key]) and int(summary['Measurements'][key], 16),
                    'Invalid measurement ' + key)
        cid = run('docker', 'create', summary['Image']['ID'], capture=True)
        try:
            run('docker', 'cp', cid + ':/enclave/application.eif', OUT / 'application.eif')
            run('docker', 'cp', cid + ':/enclave/capsule.yaml', OUT / 'embedded-capsule.yaml')
        finally:
            run('docker', 'rm', cid)
        require(sha(OUT / 'capsule.yaml') == sha(OUT / 'embedded-capsule.yaml'), 'Embedded configuration changed')
        with (OUT / 'release-image.tar.gz').open('wb') as f:
            with gzip.GzipFile(filename='', mode='wb', fileobj=f, mtime=0, compresslevel=1) as z:
                proc = subprocess.Popen(['docker', 'save', 'nova-lab-release:built'], stdout=subprocess.PIPE)
                shutil.copyfileobj(proc.stdout, z)
                require(proc.wait() == 0, 'Release export failed')
        assert_materials(materials); assert_images(materials)
        isolation = read('network-isolation.json')
        isolation['after_build_probes'] = network_test()
        isolation['status'] = 'PASS'
        write('network-isolation.json', isolation)
        write('pcr.json', summary['Measurements'])
        write('offline-result.json', {'status': 'PASS', 'eif_sha256': sha(OUT / 'application.eif'),
              'release_image_id': summary['Image']['ID'], 'app_image_id': app_id,
              'input_materials_sha256': sha(OUT / 'build-materials.json'),
              'input_lock_sha256': sha(OUT / 'inputs.lock.json'), 'source_unchanged': True,
              'resolved_images_checked': True, 'network_isolation': True})
    finally:
        daemon.terminate()
        try:
            daemon.wait(timeout=60)
        except subprocess.TimeoutExpired:
            daemon.kill(); daemon.wait()
        log.close()


def publish():
    lock = read('inputs.lock.json'); validate_lock(lock)
    materials = read('build-materials.json'); assert_materials(materials)
    result = read('offline-result.json')
    require(result['status'] == 'PASS' and read('network-isolation.json')['status'] == 'PASS', 'Offline build failed')
    require(sha(OUT / 'application.eif') == result['eif_sha256'], 'EIF changed after build')
    require(sha(OUT / 'cosign-linux-amd64') == lock['cosign']['sha256'], 'Signing tool changed')
    require(materials['builder']['commit'] == os.environ['GITHUB_WORKFLOW_SHA'], 'Publishing workflow mismatch')
    run('docker', 'load', '-i', OUT / 'release-image.tar.gz')
    require(inspect('nova-lab-release:built')['Id'] == result['release_image_id'], 'Published image changed')
    version = lock['version'] + '-' + os.environ['GITHUB_RUN_ID'] + '-a' + os.environ['GITHUB_RUN_ATTEMPT']
    uri = IMAGE + ':' + version
    run('docker', 'tag', result['release_image_id'], uri)
    password = subprocess.run(['aws', 'ecr-public', 'get-login-password', '--region', 'us-east-1'],
                              check=True, capture_output=True, text=True).stdout
    subprocess.run(['docker', 'login', '--username', 'AWS', '--password-stdin', 'public.ecr.aws'],
                   input=password, text=True, check=True, stdout=subprocess.DEVNULL)
    run('docker', 'push', uri)
    refs = inspect(uri)['RepoDigests']
    digests = [r.split('@')[1] for r in refs if r.startswith(IMAGE + '@')]
    require(len(set(digests)) == 1, 'Published digest missing/ambiguous')
    digest = digests[0]
    cosign = OUT / 'cosign-linux-amd64'
    identity = 'https://github.com/' + REPO + '/' + WORKFLOW + '@' + os.environ['GITHUB_REF']
    verification = ['--certificate-identity=' + identity,
                    '--certificate-oidc-issuer=https://token.actions.githubusercontent.com',
                    '--certificate-github-workflow-sha=' + os.environ['GITHUB_WORKFLOW_SHA']]
    run(cosign, 'sign', '--yes', '--bundle', OUT / 'image.sigstore.json', IMAGE + '@' + digest)
    verified = run(cosign, 'verify', *verification, IMAGE + '@' + digest, capture=True)
    (OUT / 'image-verification.json').write_text(verified + '\n')
    names = sorted(p.name for p in OUT.iterdir() if p.is_file() and p.name != 'cosign-linux-amd64')
    # The signing binary is also retained; its bytes were checked before execution.
    names.append('cosign-linux-amd64')
    files = {name: {'sha256': sha(OUT / name), 'size': (OUT / name).stat().st_size} for name in sorted(names)}
    doc = {'schema_version': '2.0', 'type': 'https://sparsity.cloud/nova/build-attestation/v2',
        'builder': materials['builder'],
        'source': {'repo': lock['source']['repository'], 'ref': lock['source']['commit'],
                   'commit': lock['source']['commit'], 'directory': lock['source']['directory'], 'dockerfile': 'Dockerfile'},
        'enclave': {k.lower(): v for k, v in read('pcr.json').items() if k in ('PCR0', 'PCR1', 'PCR2')},
        'build': {'github_run_id': os.environ['GITHUB_RUN_ID'], 'github_run_attempt': os.environ['GITHUB_RUN_ATTEMPT'],
                  'builder_identity': identity, 'workflow_commit': os.environ['GITHUB_WORKFLOW_SHA'],
                  'source_date_epoch': materials['build_args']['SOURCE_DATE_EPOCH'], 'release_version': version},
        'image': {'uri': uri, 'digest': digest, 'config_digest': result['release_image_id'],
                  'rekor_log_index': json.loads((OUT / 'image.sigstore.json').read_text())['verificationMaterial']['tlogEntries'][0]['logIndex']},
        'eif': {'sha256': result['eif_sha256'], 'size': (OUT / 'application.eif').stat().st_size},
        'input_model': {'policy': POLICY, 'materials': materials, 'offline_result': result,
                        'network_evidence_sha256': sha(OUT / 'network-isolation.json')},
        'files': files}
    write('build-attestation.json', doc)
    run(cosign, 'sign-blob', '--yes', '--bundle', OUT / 'build-attestation.sigstore.json', OUT / 'build-attestation.json')
    run(cosign, 'verify-blob', '--bundle', OUT / 'build-attestation.sigstore.json', *verification, OUT / 'build-attestation.json')
    bundle = read('build-attestation.sigstore.json')
    (OUT / 'build-attestation.json.sig').write_text(bundle['messageSignature']['signature'] + '\n')
    certificate = base64.b64decode(bundle['verificationMaterial']['certificate']['rawBytes'])
    r = subprocess.run(['openssl', 'x509', '-inform', 'DER'], input=certificate, check=True, capture_output=True)
    (OUT / 'build-attestation.json.crt').write_bytes(r.stdout)
    (OUT / 'build-attestation.sha256').write_text(sha(OUT / 'build-attestation.json') + '  build-attestation.json\n')
    tag = APP + '-v' + version
    url = 'https://github.com/' + REPO + '/actions/runs/' + os.environ['GITHUB_RUN_ID']
    notes = ('# Complete input model for the Nova build lab\n\n'
        'Workflow commit: `' + materials['builder']['commit'] + '`\n\n'
        'Application commit: `' + lock['source']['commit'] + '`\n\n'
        'Signed build record SHA-256: `' + sha(OUT / 'build-attestation.json') + '`\n\n'
        'EIF SHA-256: `' + result['eif_sha256'] + '`\n\n'
        'Run (' + url + ').\n\n'
        'All declared source, configuration, tool archives, container input layers and output files are retained. '
        'Docker and Capsule executed in a network namespace without external connectivity and without credentials. '
        'The GitHub-hosted runner is the trusted platform; clock and randomness remain permitted. '
        'This is build provenance, not a reproducible-build or host-attestation claim.\n')
    (OUT / 'release-notes.md').write_text(notes)
    artifacts = sorted(p for p in OUT.iterdir() if p.is_file())
    require(all(p.stat().st_size < 2 * 1024**3 for p in artifacts), 'Release asset exceeds GitHub size limit')
    run('gh', 'release', 'create', tag, '--repo', REPO, '--target', materials['builder']['commit'],
        '--title', APP + ' complete inputs ' + version, '--notes-file', OUT / 'release-notes.md', *artifacts)
    for path in artifacts:
        run('aws', 's3', 'cp', path, 's3://nova-app-hub-artifacts-004118891089/builds/' + APP + '/' + version + '/' + path.name,
            '--only-show-errors')
    print('COMPLETE INPUT BUILD:', sha(OUT / 'build-attestation.json'), flush=True)


if __name__ == '__main__':
    {'prepare': prepare, 'offline': offline, 'publish': publish}[sys.argv[1]]()
