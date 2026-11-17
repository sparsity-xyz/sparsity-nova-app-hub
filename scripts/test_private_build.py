"""Validate real archive extraction and credential-free build arguments."""
import importlib.util
import io
import json
import os
from pathlib import Path
import tarfile
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('private_builder', Path(__file__).with_name('private-build.py'))
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def archive(name, kind=tarfile.REGTYPE, link=''):
    """Produce a representative GitHub source archive."""
    data = io.BytesIO()
    with tarfile.open(fileobj=data, mode='w:gz') as out:
        m = tarfile.TarInfo(name);m.type=kind;m.linkname=link
        content=b'FROM scratch\n';m.size=len(content) if kind==tarfile.REGTYPE else 0
        out.addfile(m,io.BytesIO(content) if m.size else None)
    return data.getvalue()


class ArchiveTests(unittest.TestCase):
    """Source archive entries cannot escape the isolated build context."""
    def test_normal_source(self):
        with tempfile.TemporaryDirectory() as d:
            target=Path(d)/'source';module.extract(archive('repo-sha/Dockerfile'),target)
            self.assertEqual((target/'Dockerfile').read_text(),'FROM scratch\n')

    def test_unsafe_members(self):
        for name,kind,link in [('root/../../escape',tarfile.REGTYPE,''),('/absolute',tarfile.REGTYPE,''),
            ('root/link',tarfile.SYMTYPE,'/etc/passwd'),('root/link',tarfile.LNKTYPE,'../../escape')]:
            with self.subTest(name=name,kind=kind),tempfile.TemporaryDirectory() as d:
                with self.assertRaises(ValueError):module.extract(archive(name,kind,link),Path(d)/'source')


class WorkflowTests(unittest.TestCase):
    """Exercise runner orchestration against the actual Nova configuration shape."""
    def test_root_build_then_private_publication(self):
        import yaml
        registry = '123456789012.dkr.ecr.us-east-1.amazonaws.com'
        image = registry + '/nova-private-staging/abc123:build-7-abcdef012345'
        info = dict(registry=registry, app_sqid='abc123', image_uri=image, commit='a' * 40,
            build_config='build:\n  directory: /\n  dockerfile: Dockerfile\n  args:\n    - name: LABEL\n      value: "a b; echo ignored"\n',
            capsule_config='version: v1\nsources:\n  app: placeholder\n  capsule-runtime: public.ecr.aws/reviewed/runtime:tag\n')
        calls, requests = [], []

        def request(path, payload=None):
            requests.append((path, payload))
            if path.startswith('/input?'): return json.dumps(info).encode()
            if path.startswith('/source?'): return archive('repo-sha/Dockerfile')
            return b'{}'

        def run(args, **kwargs):
            calls.append((args, kwargs))
            if args[0] == 'capsule-cli':
                stdout = json.dumps({'PCR0': 'c'*96, 'PCR1': 'd'*96, 'PCR2': 'e'*96})
            elif args[:3] == ['docker', 'image', 'inspect'] and '--format={{index .RepoDigests 0}}' in args:
                stdout = args[-1] + '@sha256:' + 'b'*64
            elif args[:2] == ['docker', 'inspect']:
                stdout = image + '@sha256:' + 'f'*64
            else:
                stdout = ''
            return SimpleNamespace(stdout=stdout, stderr='', returncode=0)

        with tempfile.TemporaryDirectory() as d, patch.object(module, 'ROOT', Path(d)), \
                patch.dict(os.environ, BUILD_ID='7', BUILD_TICKET='scoped-ticket', GITHUB_RUN_ID='42', GITHUB_REPOSITORY='nova/private'), \
                patch.object(module, 'request', side_effect=request), patch.object(module, 'run', side_effect=run), \
                patch.object(module.subprocess, 'run', return_value=SimpleNamespace(returncode=0)):
            module.build_image()
            docker = calls[0]
            self.assertEqual(docker[0][-1], str((Path(d)/'private-source').resolve()))
            self.assertIn('LABEL=a b; echo ignored', docker[0])
            self.assertTrue(docker[1]['unprivileged'])
            self.assertFalse(any(args[0] in ('aws', 'cosign') for args, _ in calls))
            capsule = yaml.safe_load((Path(d)/'private-output/capsule.yaml').read_text())
            self.assertTrue(capsule['sources']['capsule-runtime'].startswith('public.ecr.aws/reviewed/runtime:tag@sha256:'))
            self.assertEqual(capsule['target'], image)
            module.publish_image()
            self.assertIn((['docker', 'push', image], {}), calls)
            self.assertEqual(requests[-1][1]['status'], 'success')
            self.assertEqual(requests[-1][1]['attestation']['image_digest'], 'sha256:'+'f'*64)
            self.assertTrue(any(args[:3] == ['cosign', 'sign', '--yes'] and '--tlog-upload=false' in args for args, _ in calls))

    def test_build_subprocess_does_not_inherit_host_capabilities(self):
        with patch.dict(os.environ, AWS_SECRET_ACCESS_KEY='aws-secret', COSIGN_PRIVATE_KEY='sign-secret',
                BUILD_TICKET='job-secret', GITHUB_TOKEN='github-secret', PATH='/bin'), \
                patch.object(module.subprocess, 'run') as run:
            module.run(['docker', 'build', '.'], unprivileged=True)
            env = run.call_args.kwargs['env']
            self.assertEqual(env['PATH'], '/bin')
            for key in ('AWS_SECRET_ACCESS_KEY', 'COSIGN_PRIVATE_KEY', 'BUILD_TICKET', 'GITHUB_TOKEN'):
                self.assertNotIn(key, env)

    def test_failure_callback_does_not_replace_original_failure(self):
        with patch.dict(os.environ, GITHUB_RUN_ID='42'), patch.object(module, 'request', side_effect=OSError('unreachable')):
            module.report_failure()


if __name__=='__main__':unittest.main()
