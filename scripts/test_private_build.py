"""Validate real archive extraction and credential-free build arguments."""
import importlib.util
import io
from pathlib import Path
import tarfile
import tempfile
import unittest

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


if __name__=='__main__':unittest.main()
