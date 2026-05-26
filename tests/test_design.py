import tempfile
from pathlib import Path
from unittest import TestCase

from ending.cli import design
from ending.cli.design import Design, DesignDirectory
from ending.util import misc


class TestDesignDirectory(TestCase):
    def setUp(self) -> None:
        self._old_base_path = misc.ENDING_PATH
        self._base_path = tempfile.TemporaryDirectory()
        misc.ENDING_PATH = Path(str(self._base_path.name))
        design.ENDING_PATH = misc.ENDING_PATH

    def tearDown(self) -> None:
        misc.ENDING_PATH = self._old_base_path
        design.ENDING_PATH = misc.ENDING_PATH
        self._base_path.cleanup()

    def test_create(self) -> None:
        design_dir = DesignDirectory("test_create")
        design_dir.create()
        self.assertTrue(design_dir.get_module_path().exists())
        self.assertTrue(design_dir.get_sub_path("map").exists())
        self.assertTrue(design_dir.get_sub_path("queries").exists())

    def test_load(self) -> None:
        design_dir = DesignDirectory("test_load")
        design_dir.create()
        design = design_dir.load()
        self.assertTrue(issubclass(design, Design))
        self.assertTrue(hasattr(design, "send"))
        self.assertTrue(hasattr(design, "inject"))
        self.assertTrue(hasattr(design, "setup"))
        self.assertTrue(hasattr(design, "set_configuration"))
