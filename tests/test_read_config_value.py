import ast
import configparser
import os
import tempfile
import unittest
from pathlib import Path
from typing import Any


def load_read_config_value():
    """从 main.py 提取 read_config_value 的真实源码执行，避免导入整个程序。"""
    tree = ast.parse(Path('main.py').read_text(encoding='utf-8'))
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == 'read_config_value':
            module = ast.Module(body=[node], type_ignores=[])
            namespace = {'configparser': configparser, 'Any': Any}
            exec(compile(module, 'main.py', 'exec'), namespace)
            return namespace['read_config_value']
    raise RuntimeError('read_config_value not found in main.py')


class ReadConfigValueTest(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.config_path = os.path.join(self.tmp_dir, 'config.ini')
        self.reader = load_read_config_value()
        self.reader.__globals__['config_file'] = self.config_path
        self.reader.__globals__['text_encoding'] = 'utf-8'

    def write_config(self, content):
        Path(self.config_path).write_text(content, encoding='utf-8')

    def test_missing_priority_section_auto_creates(self):
        """旧版 config.ini 缺少 [优先监控] 分区时不应崩溃，应自动补建并写回默认值。"""
        self.write_config('[录制设置]\n循环时间(秒) = 30\n')
        parser = configparser.RawConfigParser()
        value = self.reader(parser, '优先监控', '优先监控轮询间隔(秒)', 3)
        self.assertEqual(value, 3)
        self.assertIn('优先监控', parser.sections())
        persisted = configparser.RawConfigParser()
        persisted.read(self.config_path, encoding='utf-8')
        self.assertEqual(persisted.getint('优先监控', '优先监控轮询间隔(秒)'), 3)

    def test_missing_option_in_existing_section_returns_default(self):
        self.write_config('[录制设置]\n循环时间(秒) = 30\n')
        parser = configparser.RawConfigParser()
        value = self.reader(parser, '录制设置', '不存在的键', '默认值')
        self.assertEqual(value, '默认值')

    def test_existing_value_returned_untouched(self):
        self.write_config('[录制设置]\n循环时间(秒) = 30\n')
        parser = configparser.RawConfigParser()
        value = self.reader(parser, '录制设置', '循环时间(秒)', 120)
        self.assertEqual(value, '30')
        self.assertIn('循环时间(秒) = 30', Path(self.config_path).read_text(encoding='utf-8'))


if __name__ == '__main__':
    unittest.main()
