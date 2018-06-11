import os
import tarfile

from buildstream.utils import sha256sum

from .repo import Repo


class Tar(Repo):

    def __init__(self, directory, subdir):
        # the repo is the file, not the dir it's in, so subdir is removed.
        super().__init__(directory, '')

    def create(self, directory):
        tarball = os.path.join(self.repo, 'file.tar.gz')

        old_dir = os.getcwd()
        os.chdir(directory)
        with tarfile.open(tarball, "w:gz") as tar:
            tar.add(".")
        os.chdir(old_dir)

        return sha256sum(tarball)

    def source_config(self, ref=None):
        tarball = os.path.join(self.repo, 'file.tar.gz')
        config = {
            'kind': 'tar',
            'url': 'file://' + tarball,
            'directory': '',
            'base-dir': ''
        }
        if ref is not None:
            config['ref'] = ref

        return config
