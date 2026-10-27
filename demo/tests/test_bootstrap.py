"""Verify first install, environment reuse and retry without downloading packages."""
from pathlib import Path
import tempfile
import subprocess
import unittest
from unittest.mock import patch

import run


class BootstrapTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="learning-bootstrap-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "requirements.txt").write_text("fastapi\n", encoding="utf-8")
        self.executable = self.root / ".venv" / ("Scripts/python.exe" if run.os.name == "nt" else "bin/python")
        self.stamp = self.root / ".venv" / ".demo-requirements"
        self.patch_root = patch.object(run, "ROOT", self.root)
        self.patch_root.start()
        self.addCleanup(self.patch_root.stop)

    def create_environment(self, path):
        self.executable.parent.mkdir(parents=True, exist_ok=True)
        self.executable.touch()

    def test_first_install_uses_project_python_and_tsinghua_then_reuses(self):
        with patch.object(run.venv.EnvBuilder, "create", side_effect=self.create_environment) as create, \
                patch.object(run.subprocess, "check_call") as install:
            self.assertEqual(run.bootstrap(), self.executable)
            command = install.call_args.args[0]
            self.assertEqual(command[:4], [str(self.executable), "-m", "pip", "install"])
            self.assertEqual(command[command.index("-i") + 1], "https://pypi.tuna.tsinghua.edu.cn/simple")
            self.assertTrue(self.stamp.is_file())
            run.bootstrap()
            self.assertEqual(create.call_count, 1)
            self.assertEqual(install.call_count, 1)
            (self.root / "requirements.txt").write_text("fastapi\nuvicorn\n", encoding="utf-8")
            run.bootstrap()
            self.assertEqual(install.call_count, 2)

    def test_failed_install_is_retried_without_success_stamp(self):
        with patch.object(run.venv.EnvBuilder, "create", side_effect=self.create_environment), \
                patch.object(run.subprocess, "check_call", side_effect=[subprocess.CalledProcessError(1, "pip"), None]) as install:
            with self.assertRaises(subprocess.CalledProcessError):
                run.bootstrap()
            self.assertFalse(self.stamp.exists())
            run.bootstrap()
            self.assertTrue(self.stamp.exists())
            self.assertEqual(install.call_count, 2)

    def test_recreated_environment_does_not_trust_an_old_stamp(self):
        with patch.object(run.venv.EnvBuilder, "create", side_effect=self.create_environment), \
                patch.object(run.subprocess, "check_call") as install:
            run.bootstrap()
            self.executable.unlink()
            run.bootstrap()
            self.assertEqual(install.call_count, 2)
