"""bag_files.py: bag upload (begin / stream files / finish / cancel), delete to trash, list / restore / purge / empty.

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

    def test_finish_refuses_an_open_file_even_when_its_path_exists(self):
        async def scenario():
            tok = self.files.begin('partial', len(META.encode()) + 100, 2)['token']
            await self.files.receive(tok, 'metadata.yaml', Body(META.encode()))
            started, resume = asyncio.Event(), asyncio.Event()
            class SlowBody:
                async def stream(self):
                    yield b'x' * 20
                    started.set()
                    await resume.wait()
                    yield b'x' * 80
            task = asyncio.create_task(self.files.receive(tok, 't10_0.db3', SlowBody()))
            await started.wait()
            try:
                self.assertEqual(self.code(self.files.finish, tok), 'bag_upload_invalid')
                self.assertFalse(Path(self.root, 'uploads', 'partial').exists())
            finally:
                resume.set()
                await task
            out = self.files.finish(tok)
            self.assertEqual(Path(out['path'], 't10_0.db3').stat().st_size, 100)
        asyncio.run(scenario())

    def test_finish_checks_announced_size_and_file_count(self):
        for name, size, count in [('short', len(META.encode()) + 101, 2),
                                  ('missing_file', len(META.encode()) + 100, 3)]:
            tok = self.files.begin(name, size, count)['token']
            self.put(tok, 'metadata.yaml', META.encode())
            self.put(tok, 't10_0.db3', b'x' * 100)
            self.assertEqual(self.code(self.files.finish, tok), 'bag_upload_invalid')
            self.files.cancel(tok)

    def test_concurrent_files_share_the_total_byte_limit(self):
        async def scenario():
            tok = self.files.begin('concurrent', 100, 2)['token']
            started, resume = asyncio.Event(), asyncio.Event()
            class SlowBody:
                async def stream(self):
                    yield b'x' * 70
                    started.set()
                    await resume.wait()
            task = asyncio.create_task(self.files.receive(tok, 'one.db3', SlowBody()))
            await started.wait()
            try:
                with self.assertRaises(BagFileError):
                    await self.files.receive(tok, 'two.db3', Body(b'x' * 70))
            finally:
                resume.set()
                await task
            self.files.cancel(tok)
        asyncio.run(scenario())

    def test_cancel_during_upload_does_not_damage_the_next_session(self):
        async def scenario():
            tok = self.files.begin('old', 100, 1)['token']
            started, resume = asyncio.Event(), asyncio.Event()
            class SlowBody:
                async def stream(self):
                    yield b'x'
                    started.set()
                    await resume.wait()
                    yield b'y'
            task = asyncio.create_task(self.files.receive(tok, 'old.db3', SlowBody()))
            await started.wait()
            self.files._session['touched'] = 0
            self.assertEqual(self.code(self.files.begin, 'new', 100, 1), 'bag_upload_busy')
            self.files.cancel(tok)
            new = self.files.begin('new', 100, 1)['token']
            resume.set()
            with self.assertRaises(BagFileError):
                await task
            self.assertFalse(Path(self.files.staging, tok).exists())
            self.assertEqual(self.files._session['token'], new)
            self.files.cancel(new)
        asyncio.run(scenario())

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

    def test_trash_lists_restores_and_purges(self):
        self.files.finish(self.upload())
        bag = f'{self.root}/uploads/t10'
        self.files.delete(bag)
        out = self.files.trash_entries()
        self.assertEqual(out['dirs'], [f'{self.root}/.trash'])
        [e] = out['entries']
        self.assertEqual((e['name'], e['restorable']), ('uploads/t10', True))
        self.assertGreater(e['size'], 100)
        self.files.restore(e['id'])
        self.assertTrue(Path(bag, 'metadata.yaml').is_file())
        self.assertEqual(self.files.trash_entries()['entries'], [])
        self.assertEqual(list(Path(self.root, '.trash').iterdir()), [])          # the .json record went too
        # restore refuses to overwrite a bag that took the old place
        self.files.delete(bag)
        [e] = self.files.trash_entries()['entries']
        self.files.finish(self.upload())
        self.assertEqual(self.code(self.files.restore, e['id']), 'bag_exists')
        self.assertGreater(self.files.purge(e['id'])['freed'], 100)
        self.assertEqual(list(Path(self.root, '.trash').iterdir()), [])
        self.assertTrue(Path(bag).is_dir())                                      # the new bag is untouched

    def test_trash_ids_must_be_trash_entries(self):
        self.files.finish(self.upload())
        bag = f'{self.root}/uploads/t10'
        for bad in (bag, f'{self.root}/uploads', f'{self.root}/.trash', f'{self.root}/.trash/../uploads/t10', '/tmp', ''):
            self.assertEqual(self.code(self.files.purge, bad), 'bag_trash_not_found', bad)
        self.assertTrue(Path(bag).is_dir())

    def test_old_entries_without_a_record_can_only_be_purged(self):
        Path(self.root, '.trash', 'old_bag_20261101_120000').mkdir(parents=True)
        Path(self.root, '.trash', 'old_bag_20261101_120000', 'x.db3').write_bytes(b'x' * 10)
        [e] = self.files.trash_entries()['entries']
        self.assertEqual((e['name'], e['restorable']), ('old_bag_20261101_120000', False))
        self.assertEqual(self.code(self.files.restore, e['id']), 'bag_trash_not_restorable')
        # a forged record cannot send the folder outside the bag folder
        Path(e['id'] + '.json').write_text('{"origin": "../outside"}')
        self.assertEqual(self.code(self.files.restore, e['id']), 'bag_trash_not_restorable')
        self.files.purge(e['id'])
        self.assertEqual(self.files.trash_entries()['entries'], [])

    def test_a_root_that_is_a_bag_is_not_deleted(self):
        Path(self.root, 'metadata.yaml').write_text(META)
        Path(self.root, 't10_0.db3').write_bytes(b'x')
        self.assertEqual(self.code(self.files.delete, self.root), 'bag_is_root')



if __name__ == '__main__':
    unittest.main()
