"""文件自动整理(src/file_organizer.py)的单元测试。

覆盖配置解析、媒体识别、移动/重名、四个整理步骤、busy 注册表、时长探测,
以及 main.py 的接线(配置读取与录制/转码期间的 busy 注册)。
"""

import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src import file_organizer as fo  # noqa: E402


def make_media(path, content=b'x'):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    Path(path).write_bytes(content)


class OrganizerTestBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = os.path.abspath(self.tmp.name)
        fo._reset_for_tests()

    def tearDown(self):
        fo.reset_busy_registry()
        self.tmp.cleanup()

    def settings(self, **kwargs):
        values = dict(
            enabled=True,
            interval_seconds=3600,
            source_dir=self.root,
            target_dir='',
            min_duration_seconds=0.0,
            grace_seconds=0,
        )
        values.update(kwargs)
        return fo.OrganizerSettings(**values)


class ParseSettingsTest(OrganizerTestBase):
    def test_interval_minutes(self):
        self.assertEqual(fo.parse_interval_minutes('90'), 90)
        self.assertEqual(fo.parse_interval_minutes('0'), fo.MIN_INTERVAL_MINUTES)
        self.assertEqual(fo.parse_interval_minutes('abc'), fo.DEFAULT_INTERVAL_MINUTES)
        self.assertEqual(fo.parse_interval_minutes('999999'), fo.MAX_INTERVAL_MINUTES)

    def test_min_duration_seconds(self):
        self.assertEqual(fo.parse_min_duration_seconds('30'), 30.0)
        self.assertEqual(fo.parse_min_duration_seconds('-5'), 0.0)
        self.assertEqual(fo.parse_min_duration_seconds('x'), fo.DEFAULT_MIN_DURATION_SECONDS)
        self.assertEqual(fo.parse_min_duration_seconds('99999999'), fo.MAX_MIN_DURATION_SECONDS)

    def test_grace_seconds(self):
        self.assertEqual(fo.parse_grace_seconds('0'), 0)
        self.assertEqual(fo.parse_grace_seconds('-1'), 0)
        self.assertEqual(fo.parse_grace_seconds('abc'), fo.DEFAULT_GRACE_SECONDS)
        self.assertEqual(fo.parse_grace_seconds('99999999'), fo.MAX_GRACE_SECONDS)

    def test_invalid_value_warns_only_once(self):
        with mock.patch.object(fo.logger, 'warning') as warning:
            fo.parse_interval_minutes('oops')
            fo.parse_interval_minutes('oops')
        self.assertEqual(warning.call_count, 1)


class MediaFileTest(unittest.TestCase):
    def test_media_extensions(self):
        for name in ('a.ts', 'b.MKV', 'c.mp4', 'd.mp3', 'e.m4a', 'f.flv', 'g.mov'):
            self.assertTrue(fo.is_media_file(name), name)
        for name in ('a.part', 'b.tmp', 'c.srt', 'd.ass', 'e', '.flv'):
            self.assertFalse(fo.is_media_file(name), name)


class MoveTest(OrganizerTestBase):
    def test_move_creates_target(self):
        src = os.path.join(self.root, 'sub', 'a.mkv')
        make_media(src)
        target = os.path.join(self.root, 'dst')
        moved = fo.move_to_directory(src, target)
        self.assertEqual(moved, os.path.join(target, 'a.mkv'))
        self.assertTrue(os.path.isfile(moved))
        self.assertFalse(os.path.exists(src))

    def test_collision_gets_index_suffix(self):
        target = os.path.join(self.root, 'dst')
        make_media(os.path.join(target, 'a.mkv'), b'old')
        src = os.path.join(self.root, 'sub', 'a.mkv')
        make_media(src, b'new')
        moved = fo.move_to_directory(src, target)
        self.assertEqual(moved, os.path.join(target, 'a_1.mkv'))
        self.assertEqual(Path(os.path.join(target, 'a.mkv')).read_bytes(), b'old')
        self.assertEqual(Path(moved).read_bytes(), b'new')

    def test_same_directory_is_noop(self):
        src = os.path.join(self.root, 'a.mkv')
        make_media(src)
        self.assertIsNone(fo.move_to_directory(src, self.root))
        self.assertTrue(os.path.isfile(src))


class FlattenTest(OrganizerTestBase):
    def test_moves_nested_media_to_root(self):
        make_media(os.path.join(self.root, 'p', 'a', 'x.mkv'))
        make_media(os.path.join(self.root, 'p', 'y.flv'))
        Path(os.path.join(self.root, 'p', 'a', 'note.txt')).write_text('n')
        report = fo.OrganizeReport()
        fo.flatten_media(self.root, self.settings(), report)
        self.assertEqual(report.moved_to_root, 2)
        self.assertTrue(os.path.isfile(os.path.join(self.root, 'x.mkv')))
        self.assertTrue(os.path.isfile(os.path.join(self.root, 'y.flv')))
        self.assertTrue(os.path.isfile(os.path.join(self.root, 'p', 'a', 'note.txt')))

    def test_skips_busy_fresh_and_target_subtree(self):
        busy_file = os.path.join(self.root, 'busy', 'a.mkv')
        make_media(busy_file)
        fresh_file = os.path.join(self.root, 'fresh', 'b.mkv')
        make_media(fresh_file)
        target = os.path.join(self.root, 'archive')
        archived = os.path.join(target, 'c.mkv')
        make_media(archived)
        report = fo.OrganizeReport()
        with fo.busy_dir(os.path.join(self.root, 'busy')):
            fo.flatten_media(
                self.root,
                self.settings(grace_seconds=3600, target_dir=target),
                report,
            )
        self.assertEqual(report.moved_to_root, 0)
        self.assertTrue(os.path.isfile(busy_file))
        self.assertTrue(os.path.isfile(fresh_file))
        self.assertTrue(os.path.isfile(archived))

    def test_does_not_follow_symlinked_dirs(self):
        outside = os.path.join(self.root, 'outside')
        make_media(os.path.join(outside, 'x.mkv'))
        source = os.path.join(self.root, 'src')
        os.makedirs(source)
        try:
            os.symlink(outside, os.path.join(source, 'link'))
        except (OSError, NotImplementedError):
            self.skipTest('symlink not permitted')
        report = fo.OrganizeReport()
        fo.flatten_media(source, self.settings(source_dir=source), report)
        self.assertEqual(report.moved_to_root, 0)
        self.assertTrue(os.path.isfile(os.path.join(outside, 'x.mkv')))


class RemoveEmptyDirsTest(OrganizerTestBase):
    def test_removes_nested_empty_chain_keeps_root(self):
        os.makedirs(os.path.join(self.root, 'a', 'b'))
        report = fo.OrganizeReport()
        fo.remove_empty_dirs(self.root, self.settings(), report)
        self.assertEqual(report.removed_dirs, 2)
        self.assertFalse(os.path.exists(os.path.join(self.root, 'a')))
        self.assertTrue(os.path.isdir(self.root))

    def test_keeps_dir_with_file(self):
        os.makedirs(os.path.join(self.root, 'a'))
        Path(os.path.join(self.root, 'a', 'note.txt')).write_text('x')
        report = fo.OrganizeReport()
        fo.remove_empty_dirs(self.root, self.settings(), report)
        self.assertEqual(report.removed_dirs, 0)
        self.assertTrue(os.path.isdir(os.path.join(self.root, 'a')))

    def test_fresh_dir_is_kept(self):
        os.makedirs(os.path.join(self.root, 'new'))
        report = fo.OrganizeReport()
        fo.remove_empty_dirs(self.root, self.settings(grace_seconds=3600), report)
        self.assertEqual(report.removed_dirs, 0)
        self.assertTrue(os.path.isdir(os.path.join(self.root, 'new')))

    def test_busy_dir_is_kept(self):
        os.makedirs(os.path.join(self.root, 'rec'))
        report = fo.OrganizeReport()
        with fo.busy_dir(os.path.join(self.root, 'rec')):
            fo.remove_empty_dirs(self.root, self.settings(), report)
        self.assertEqual(report.removed_dirs, 0)
        self.assertTrue(os.path.isdir(os.path.join(self.root, 'rec')))

    def test_dirs_emptied_by_flatten_are_removed_in_same_cycle(self):
        media = os.path.join(self.root, 'a', 'b', 'x.mkv')
        make_media(media)
        old = time.time() - 7200
        os.utime(media, (old, old))  # 文件足够旧, 目录仍是新建的(宽限期内)
        report = fo.OrganizeReport()
        touched = set()
        settings = self.settings(grace_seconds=3600)
        fo.flatten_media(self.root, settings, report, touched)
        fo.remove_empty_dirs(self.root, settings, report, touched)
        self.assertEqual(report.moved_to_root, 1)
        self.assertEqual(report.removed_dirs, 2)
        self.assertFalse(os.path.exists(os.path.join(self.root, 'a')))


class DeleteShortTest(OrganizerTestBase):
    def _patch(self, mapping):
        return mock.patch.object(
            fo, 'probe_duration_seconds',
            side_effect=lambda path: mapping.get(os.path.basename(path)),
        )

    def test_deletes_short_and_keeps_long_or_unknown(self):
        for name in ('short.mkv', 'long.flv', 'unknown.mp4'):
            make_media(os.path.join(self.root, name))
        Path(os.path.join(self.root, 'note.txt')).write_text('x')
        report = fo.OrganizeReport()
        durations = {'short.mkv': 2.0, 'long.flv': 10.0, 'unknown.mp4': None, 'note.txt': 1.0}
        with self._patch(durations):
            fo.delete_short_media(self.root, self.settings(min_duration_seconds=5), report)
        self.assertEqual(report.deleted_short, 1)
        self.assertFalse(os.path.exists(os.path.join(self.root, 'short.mkv')))
        for name in ('long.flv', 'unknown.mp4', 'note.txt'):
            self.assertTrue(os.path.exists(os.path.join(self.root, name)), name)

    def test_boundary_duration_is_kept(self):
        make_media(os.path.join(self.root, 'exact.mkv'))
        report = fo.OrganizeReport()
        with self._patch({'exact.mkv': 5.0}):
            fo.delete_short_media(self.root, self.settings(min_duration_seconds=5), report)
        self.assertEqual(report.deleted_short, 0)
        self.assertTrue(os.path.exists(os.path.join(self.root, 'exact.mkv')))

    def test_disabled_when_threshold_is_zero(self):
        make_media(os.path.join(self.root, 'a.mkv'))
        report = fo.OrganizeReport()
        with mock.patch.object(fo, 'probe_duration_seconds') as probe:
            fo.delete_short_media(self.root, self.settings(min_duration_seconds=0), report)
        probe.assert_not_called()
        self.assertTrue(os.path.exists(os.path.join(self.root, 'a.mkv')))

    def test_fresh_file_is_skipped(self):
        make_media(os.path.join(self.root, 'a.mkv'))
        report = fo.OrganizeReport()
        with self._patch({'a.mkv': 1.0}):
            fo.delete_short_media(
                self.root,
                self.settings(min_duration_seconds=5, grace_seconds=3600),
                report,
            )
        self.assertEqual(report.deleted_short, 0)
        self.assertTrue(os.path.exists(os.path.join(self.root, 'a.mkv')))

    def test_only_top_level_files_are_checked(self):
        make_media(os.path.join(self.root, 'sub', 'short.mkv'))
        report = fo.OrganizeReport()
        with self._patch({'short.mkv': 1.0}):
            fo.delete_short_media(self.root, self.settings(min_duration_seconds=5), report)
        self.assertEqual(report.deleted_short, 0)
        self.assertTrue(os.path.isfile(os.path.join(self.root, 'sub', 'short.mkv')))


class ArchiveTest(OrganizerTestBase):
    def test_archives_top_level_media_only(self):
        make_media(os.path.join(self.root, 'a.mkv'))
        make_media(os.path.join(self.root, 'b.mp3'))
        make_media(os.path.join(self.root, 'sub', 'c.mkv'))
        Path(os.path.join(self.root, 's.srt')).write_text('x')
        target = os.path.join(self.root, 'out')
        report = fo.OrganizeReport()
        fo.archive_media(self.root, self.settings(target_dir=target), report)
        self.assertEqual(report.archived, 2)
        self.assertTrue(os.path.isfile(os.path.join(target, 'a.mkv')))
        self.assertTrue(os.path.isfile(os.path.join(target, 'b.mp3')))
        self.assertTrue(os.path.isfile(os.path.join(self.root, 'sub', 'c.mkv')))
        self.assertTrue(os.path.isfile(os.path.join(self.root, 's.srt')))

    def test_collision_keeps_existing_file(self):
        make_media(os.path.join(self.root, 'a.mkv'), b'new')
        target = os.path.join(self.root, 'out')
        make_media(os.path.join(target, 'a.mkv'), b'old')
        report = fo.OrganizeReport()
        fo.archive_media(self.root, self.settings(target_dir=target), report)
        self.assertEqual(report.archived, 1)
        self.assertEqual(Path(os.path.join(target, 'a.mkv')).read_bytes(), b'old')
        self.assertEqual(Path(os.path.join(target, 'a_1.mkv')).read_bytes(), b'new')

    def test_no_target_or_same_directory_is_noop(self):
        make_media(os.path.join(self.root, 'a.mkv'))
        report = fo.OrganizeReport()
        fo.archive_media(self.root, self.settings(target_dir=''), report)
        fo.archive_media(self.root, self.settings(target_dir=self.root), report)
        self.assertEqual(report.archived, 0)
        self.assertTrue(os.path.exists(os.path.join(self.root, 'a.mkv')))


class RunOnceTest(OrganizerTestBase):
    def _patch_steps(self, calls):
        return mock.patch.multiple(
            fo,
            flatten_media=mock.Mock(side_effect=lambda *a, **k: calls.append('flatten')),
            remove_empty_dirs=mock.Mock(side_effect=lambda *a, **k: calls.append('remove')),
            delete_short_media=mock.Mock(side_effect=lambda *a, **k: calls.append('delete')),
            archive_media=mock.Mock(side_effect=lambda *a, **k: calls.append('archive')),
        )

    def test_runs_four_steps_in_order(self):
        calls = []
        with self._patch_steps(calls):
            report = fo.run_once(self.settings())
        self.assertEqual(calls, ['flatten', 'remove', 'delete', 'archive'])
        self.assertEqual(report.total, 0)

    def test_disabled_does_nothing(self):
        calls = []
        with self._patch_steps(calls):
            fo.run_once(self.settings(enabled=False))
        self.assertEqual(calls, [])

    def test_missing_source_is_skipped(self):
        calls = []
        with self._patch_steps(calls):
            fo.run_once(self.settings(source_dir=os.path.join(self.root, 'nope')))
        self.assertEqual(calls, [])

    def test_empty_source_is_skipped(self):
        calls = []
        with self._patch_steps(calls):
            fo.run_once(self.settings(source_dir=''))
        self.assertEqual(calls, [])

    def test_full_cycle_end_to_end(self):
        make_media(os.path.join(self.root, 'p', 'short.mkv'))
        make_media(os.path.join(self.root, 'p', 'sub', 'long.mkv'))
        target = os.path.join(self.root, 'out')
        settings = self.settings(target_dir=target, min_duration_seconds=5)
        with mock.patch.object(
            fo, 'probe_duration_seconds',
            side_effect=lambda path: 1.0 if 'short' in path else 30.0,
        ):
            report = fo.run_once(settings)
        self.assertEqual(report.moved_to_root, 2)
        self.assertEqual(report.deleted_short, 1)
        self.assertEqual(report.archived, 1)
        self.assertTrue(os.path.isfile(os.path.join(target, 'long.mkv')))
        self.assertFalse(os.path.exists(os.path.join(target, 'short.mkv')))
        self.assertFalse(os.path.exists(os.path.join(self.root, 'p')))


class BusyRegistryTest(OrganizerTestBase):
    def test_dir_refcount_and_ancestor_check(self):
        directory = os.path.join(self.root, 'a')
        fo.register_busy_dir(directory)
        fo.register_busy_dir(directory)
        fo.unregister_busy_dir(directory)
        self.assertTrue(fo._dir_is_busy(directory))
        self.assertTrue(fo._dir_is_busy(os.path.join(directory, 'b')))
        fo.unregister_busy_dir(directory)
        self.assertFalse(fo._dir_is_busy(directory))

    def test_file_refcount(self):
        path = os.path.join(self.root, 'a.mkv')
        fo.register_busy_file(path)
        fo.register_busy_file(path)
        fo.unregister_busy_file(path)
        self.assertTrue(fo._file_is_busy(path))
        fo.unregister_busy_file(path)
        self.assertFalse(fo._file_is_busy(path))

    def test_context_managers_release(self):
        directory = os.path.join(self.root, 'a')
        path = os.path.join(self.root, 'x.mkv')
        with fo.busy_dir(directory):
            self.assertTrue(fo._dir_is_busy(directory))
        self.assertFalse(fo._dir_is_busy(directory))
        with fo.busy_file(path):
            self.assertTrue(fo._file_is_busy(path))
        self.assertFalse(fo._file_is_busy(path))


class ProbeDurationTest(OrganizerTestBase):
    def test_zero_byte_file_is_zero(self):
        path = os.path.join(self.root, 'empty.mkv')
        Path(path).write_bytes(b'')
        self.assertEqual(fo.probe_duration_seconds(path), 0.0)

    def test_ffprobe_format_duration(self):
        path = os.path.join(self.root, 'a.mkv')
        make_media(path)
        payload = '{"format": {"duration": "12.5"}, "streams": []}'
        fake = mock.Mock(stdout=payload, stderr='')
        with mock.patch.object(fo, '_find_ffprobe', return_value='/fake/ffprobe'), \
                mock.patch.object(fo.subprocess, 'run', return_value=fake):
            self.assertEqual(fo.probe_duration_seconds(path), 12.5)

    def test_ffprobe_falls_back_to_stream_duration(self):
        path = os.path.join(self.root, 'a.mkv')
        make_media(path)
        payload = '{"format": {"duration": "N/A"}, "streams": [{"duration": "N/A"}, {"duration": "7.25"}]}'
        fake = mock.Mock(stdout=payload, stderr='')
        with mock.patch.object(fo, '_find_ffprobe', return_value='/fake/ffprobe'), \
                mock.patch.object(fo.subprocess, 'run', return_value=fake):
            self.assertEqual(fo.probe_duration_seconds(path), 7.25)

    def test_ffmpeg_fallback_without_ffprobe(self):
        path = os.path.join(self.root, 'a.mkv')
        make_media(path)
        stderr = '  Duration: 00:01:02.50, start: 0.000000, bitrate: 10 kb/s\n'
        fake = mock.Mock(stdout='', stderr=stderr)
        with mock.patch.object(fo, '_find_ffprobe', return_value=None), \
                mock.patch.object(fo.subprocess, 'run', return_value=fake):
            self.assertAlmostEqual(fo.probe_duration_seconds(path), 62.5, places=2)

    def test_unknown_when_both_probes_fail(self):
        path = os.path.join(self.root, 'a.mkv')
        make_media(path)
        with mock.patch.object(fo, '_find_ffprobe', return_value=None), \
                mock.patch.object(fo.subprocess, 'run', side_effect=FileNotFoundError):
            self.assertIsNone(fo.probe_duration_seconds(path))

    def test_ffprobe_unusable_falls_back_to_ffmpeg(self):
        path = os.path.join(self.root, 'a.mkv')
        make_media(path)
        bad = mock.Mock(stdout='not json', stderr='')
        good = mock.Mock(stdout='', stderr='  Duration: 00:00:09.00, start: 0.0\n')
        with mock.patch.object(fo, '_find_ffprobe', return_value='/fake/ffprobe'), \
                mock.patch.object(fo.subprocess, 'run', side_effect=[bad, good]):
            self.assertAlmostEqual(fo.probe_duration_seconds(path), 9.0, places=2)


class UpdateSettingsTest(OrganizerTestBase):
    def test_disabled_does_not_start_thread(self):
        with mock.patch.object(fo, 'start_organizer_thread') as start:
            fo.update_settings(self.settings(enabled=False))
        start.assert_not_called()
        self.assertFalse(fo.get_settings().enabled)

    def test_enabled_starts_thread(self):
        with mock.patch.object(fo, 'start_organizer_thread') as start:
            fo.update_settings(self.settings(enabled=True))
        start.assert_called_once()

    def test_unchanged_settings_report_false(self):
        settings = self.settings(enabled=False)
        fo.update_settings(settings)
        with mock.patch.object(fo, 'start_organizer_thread'):
            self.assertFalse(fo.update_settings(settings))

    def test_interval_is_clamped(self):
        fo.update_settings(self.settings(enabled=False, interval_seconds=1))
        self.assertEqual(fo.get_settings().interval_seconds, fo.MIN_INTERVAL_MINUTES * 60)

    def test_start_thread_is_idempotent(self):
        fo.update_settings(self.settings(enabled=False))
        fo.start_organizer_thread()
        fo.start_organizer_thread()
        threads = [t for t in threading.enumerate() if t.name == 'file-organizer']
        self.assertEqual(len(threads), 1)
        self.assertTrue(fo.is_organizer_running())


class WiringTest(unittest.TestCase):
    def setUp(self):
        self.source = (ROOT / 'main.py').read_text(encoding='utf-8')

    def test_main_reads_organizer_config(self):
        for key in (
            '自动整理是否开启(是/否)',
            '整理间隔(分钟)',
            '整理源目录(留空则用直播保存路径)',
            '归档目标目录(留空则不归档)',
            '删除小于该时长的视频(秒)',
            '写入宽限期(秒)',
        ):
            self.assertIn(key, self.source)
        self.assertIn("'文件整理'", self.source)
        self.assertIn('file_organizer.update_settings(file_organizer.OrganizerSettings(', self.source)

    def test_recording_registers_output_directory(self):
        self.assertIn('register_busy_dir(busy_dir)', self.source)
        self.assertIn('unregister_busy_dir(busy_dir)', self.source)
        self.assertIn('def _check_subprocess(', self.source)

    def test_conversions_and_downloads_register_files(self):
        self.assertEqual(self.source.count('file_organizer.register_busy_file(converts_file_path)'), 3)
        self.assertEqual(self.source.count('file_organizer.unregister_busy_file(converts_file_path)'), 3)
        self.assertIn('file_organizer.register_busy_file(save_path)', self.source)
        self.assertIn('file_organizer.unregister_busy_file(save_path)', self.source)


if __name__ == '__main__':
    unittest.main()
