"""scene_import.SceneLibrary.delete_scene: uploaded worlds move to <scenes>/.trash, built-in ones are refused."""
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from robot_console.scene_import import SceneImportError, SceneLibrary

WORLD = """<?xml version="1.0"?>
<sdf version="1.8"><world name="w">
  <model name="floor"><static>true</static><link name="link">
    <collision name="c"><geometry><box><size>4 4 0.1</size></box></geometry></collision>
    <visual name="v"><geometry><box><size>4 4 0.1</size></box></geometry></visual>
  </link></model>
</world></sdf>
"""


class SceneDeleteTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='console-scene-delete-test-')
        root = Path(self.tmp.name)
        self.builtin = {'office': {'label': {'zh': 'o', 'en': 'o'}, 'description': {'zh': '', 'en': ''}, 'args': {}}}
        self.lib = SceneLibrary(root / 'scenes', self.builtin)
        sdf = root / 'room.sdf'
        sdf.write_text(WORLD, encoding='utf-8')
        try:
            self.lib.import_scene('room', str(sdf), 'room.sdf', [0, 0, 0.1, 0])
        except SceneImportError as e:          # sim_robot's trusted world template is not installed here
            self.skipTest(f'scene import unavailable: {e.code} {e.params}')

    def tearDown(self):
        self.tmp.cleanup()

    def test_delete_moves_folder_to_trash_and_drops_preset(self):
        self.assertIn('custom_room', self.builtin)
        trash = Path(self.lib.delete_scene('custom_room'))
        self.assertNotIn('custom_room', self.builtin)
        self.assertFalse((self.lib.root / 'room').exists())
        self.assertTrue((trash / 'world.sdf').is_file())
        self.assertEqual(trash.parent, self.lib.root / '.trash')
        # A reload does not bring it back, and the name can be used again
        self.lib.load()
        self.assertNotIn('custom_room', self.builtin)

    def test_builtin_and_unknown_scenes_are_refused(self):
        for name in ('office', 'custom_nope', '../x', 'custom_../x'):
            with self.assertRaises(SceneImportError) as cm:
                self.lib.delete_scene(name)
            self.assertEqual(cm.exception.code, 'scene_not_custom')
        self.assertIn('office', self.builtin)


if __name__ == '__main__':
    unittest.main()
