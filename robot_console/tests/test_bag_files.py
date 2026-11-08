"""bag_files.py: bag upload (begin / stream files / finish / cancel), delete to trash, empty trash.

Pure Python: a fake Playback object, a fake request whose body is streamed in chunks.
"""
import asyncio
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
import unittest.mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from robot_console.bag_files import BagFileError, BagFiles  # noqa: E402

META = """rosbag2_bagfile_information:
  version: 5
  storage_identifier: sqlite3
  duration:
    nanoseconds: 10000000000
  starting_time:
    nanoseconds_since_epoch: 1793427531200016975
  message_count: 98
  topics_with_message_count:
    - topic_metadata:
        name: /points
        type: sensor_msgs/msg/PointCloud2
        serialization_format: cdr
        offered_qos_profiles: ""
      message_count: 98
  relative_file_paths:
    - t10_0.db3
"""


class Body:
    def __init__(self, data, chunk=7):
        self.data, self.chunk = data, chunk

    async def stream(self):
        for i in range(0, len(self.data), self.chunk):
            yield self.data[i:i + self.chunk]


class BagFilesTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='console-bagfiles-test-')
        self.root = str(Path(self.tmp.name).resolve())
        self.state = {'state': 'idle'}
        self.pb = SimpleNamespace(roots=[self.root], status=lambda: self.state)
        self.files = BagFiles({'playback': {}}, self.pb)

    def tearDown(self):
        self.tmp.cleanup()

    def code(self, fn, *args):
        with self.assertRaises(BagFileError) as cm:
            fn(*args)
        return cm.exception.code

    def put(self, token, path, data):
        return asyncio.run(self.files.receive(token, path, Body(data)))

    def upload(self, name='t10', meta=META, db=b'x' * 100):
        files = {'metadata.yaml': meta.encode(), 't10_0.db3': db}
        tok = self.files.begin(name, sum(len(v) for v in files.values()), len(files))['token']
        for path, data in files.items():
            self.put(tok, path, data)
        return tok

    def test_upload_lands_in_uploads_and_is_listed(self):
        tok = self.upload()
        out = self.files.finish(tok)
        self.assertEqual(out['path'], f'{self.root}/uploads/t10')
        self.assertTrue(Path(out['path'], 't10_0.db3').is_file())
        self.assertFalse(any(Path(self.root, 'uploads', '.uploads').iterdir()))     # staging is empty
        self.assertEqual(self.code(self.files.begin, 't10', 10, 1), 'bag_exists')

    def test_rejects_bad_names_paths_and_sizes(self):
        self.assertEqual(self.code(self.files.begin, '../x', 10, 1), 'bag_bad_name')
        self.assertEqual(self.code(self.files.begin, '.hidden', 10, 1), 'bag_bad_name')
        self.assertEqual(self.code(self.files.begin, 'ok', 0, 1), 'bag_upload_invalid')
        tok = self.files.begin('ok', 20, 2)['token']
        for bad in ('../metadata.yaml', 'sub/a.db3', 'notes.txt', '.metadata.yaml'):
            with self.assertRaises(BagFileError):
                self.put(tok, bad, b'abc')
        with self.assertRaises(BagFileError):                 # more bytes than announced
            self.put(tok, 'a.db3', b'x' * 21)
        self.assertFalse(Path(self.files.staging, tok, 'a.db3').exists())
        self.assertEqual(self.code(self.files.begin, 'other', 10, 1), 'bag_upload_busy')
        self.files.cancel(tok)
        self.assertFalse(Path(self.files.staging, tok).exists())

    def test_finish_checks_the_metadata(self):
        tok = self.files.begin('broken', 50, 1)['token']
        self.put(tok, 't10_0.db3', b'x' * 50)
        self.assertEqual(self.code(self.files.finish, tok), 'bag_upload_invalid')       # no metadata.yaml
        self.files.cancel(tok)
        tok = self.files.begin('partial', len(META), 1)['token']
        self.put(tok, 'metadata.yaml', META.encode())
        with self.assertRaises(BagFileError) as cm:                                     # t10_0.db3 never came
            self.files.finish(tok)
        self.assertIn('t10_0.db3', cm.exception.params['reason'])

    def test_free_space_check(self):
        self.files.space_check = [self.root]
        with unittest.mock.patch('shutil.disk_usage', return_value=SimpleNamespace(free=3 * 1024 ** 3)):
            self.assertEqual(self.code(self.files.begin, 'big', 2 * 1024 ** 3, 1), 'bag_no_space')
            self.files.begin('small', 512 * 1024 ** 2, 1)

    def test_delete_moves_to_trash_and_empty_frees_it(self):
        self.files.finish(self.upload())
        bag = f'{self.root}/uploads/t10'
        self.state = {'state': 'running', 'bag': bag}
        self.assertEqual(self.code(self.files.delete, bag), 'bag_in_use')
        self.state = {'state': 'idle'}
        out = self.files.delete(bag)
        self.assertTrue(out['trash'].startswith(f'{self.root}/.trash/uploads_t10_'))
        self.assertFalse(Path(bag).exists())
        self.assertEqual(self.code(self.files.delete, bag), 'bag_not_found')
        [t] = self.files.trash()
        self.assertEqual(t['items'], 1)
        self.assertGreater(t['size'], 100)
        self.assertGreater(self.files.empty_trash()['freed'], 100)
        self.assertEqual(self.files.trash(), [])

    def test_a_root_that_is_a_bag_is_not_deleted(self):
        Path(self.root, 'metadata.yaml').write_text(META)
        Path(self.root, 't10_0.db3').write_bytes(b'x')
        self.assertEqual(self.code(self.files.delete, self.root), 'bag_is_root')



if __name__ == '__main__':
    unittest.main()
