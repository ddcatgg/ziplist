# -*- coding: utf-8 -*-
from __future__ import unicode_literals, print_function
import sys
import os
import time
import glob
import zipfile
import argparse
import io  # For Python 2.7 compatible open with encoding
import fnmatch  # For glob recursive backport


def init_colors():
    """初始化控制台颜色支持"""
    try:
        import colorama
        colorama.init()
        return {
            'yellow': colorama.Fore.YELLOW,
            'red': colorama.Fore.RED,
            'green': colorama.Fore.GREEN,
            'reset': colorama.Fore.RESET
        }
    except ImportError:
        # 如果没有 colorama，使用 ANSI 转义序列
        if os.name == 'nt':  # Windows 平台
            return {'yellow': '', 'red': '', 'green': '', 'reset': ''}
        else:  # 类 Unix 平台
            return {
                'yellow': '\033[33m',
                'red': '\033[31m',
                'green': '\033[32m',
                'reset': '\033[0m'
            }


# 初始化颜色
COLORS = init_colors()


def find_matching_files(source_dir_abs, source_pattern):
    """
    根据给定的模式查找匹配的文件。

    :param source_dir_abs: 源目录的绝对路径
    :param source_pattern: 搜索模式（支持 ** 通配符）
    :return: 匹配文件的绝对路径列表
    """
    glob_pattern = os.path.join(source_dir_abs, os.path.normpath(source_pattern))
    matched_paths = []

    if '**' in source_pattern:
        # 使用 os.walk 和 fnmatch 模拟 Python 3 中 glob 的 recursive=True 功能
        for root, dirnames, filenames in os.walk(source_dir_abs):
            for item in dirnames + filenames:
                full_path = os.path.join(root, item)
                relative_path = os.path.relpath(full_path, source_dir_abs)
                pattern_for_fnmatch = source_pattern.replace('**', '*').replace(os.path.sep, '/')
                relative_path_for_fnmatch = relative_path.replace(os.path.sep, '/')
                if fnmatch.fnmatch(relative_path_for_fnmatch, pattern_for_fnmatch):
                    matched_paths.append(full_path)
    else:
        # 对于不含'**'的普通模式，直接使用 glob
        matched_paths = glob.glob(glob_pattern)

    return matched_paths


def calculate_arcname(relative_found_path, source_pattern, dest_pattern):
    """
    计算文件在压缩包中的路径。

    :param relative_found_path: 相对于源目录的文件路径
    :param source_pattern: 源模式
    :param dest_pattern: 目标模式（可能为None）
    :return: 压缩包内的路径
    """
    if dest_pattern is None:
        # 如果规则不包含 '->'
        if '**' in source_pattern:
            # 规则: Sounds/**
            # 效果: 将 Sounds/sub/c.ogg 打包为 sub/c.ogg (保留相对路径)
            pattern_base = source_pattern.split('**')[0]
            if pattern_base:
                arcname = os.path.relpath(relative_found_path, pattern_base)
            else:
                arcname = relative_found_path
        else:
            # 规则: Debug/File.dll 或 Sounds/*.*
            # 效果: 打包到压缩包根目录，只保留文件名（扁平化）
            arcname = os.path.basename(relative_found_path)
    else:
        # 规则中包含 '->'
        if '**' in source_pattern:
            # 规则: Sounds/** -> Sounds1/**
            base_src = source_pattern.split('**')[0]
            wildcard_match = os.path.relpath(relative_found_path, base_src)
            base_dest = dest_pattern.rstrip('*.')    # 末尾是 /* 或者 /** 或者 /*.* 都不重要
            arcname = os.path.join(base_dest, wildcard_match)
        elif '*' in source_pattern:
            # 规则: Sounds/*.* -> Sounds1/*.*
            src_parent_dir = os.path.dirname(source_pattern)
            dest_parent_dir = dest_pattern.rstrip('*.')    # 末尾是 /* 或者 /** 或者 /*.* 都不重要
            file_name = os.path.basename(relative_found_path)
            if not src_parent_dir:
                arcname = os.path.join(dest_parent_dir, file_name)
            else:
                relative_to_src_parent = os.path.relpath(relative_found_path, src_parent_dir)
                arcname = os.path.join(dest_parent_dir, relative_to_src_parent)
        else:
            # 规则: Debug/Agent.exe -> Release/*
            # 重命名路径，文件名不变。
            if dest_pattern.endswith('/') or dest_pattern.endswith('\\') or dest_pattern.endswith('*'):
                dest_parent_dir = dest_pattern.rstrip('*.')
                file_name = os.path.basename(relative_found_path)
                arcname = os.path.join(dest_parent_dir, file_name)
            else:
                # 规则: Debug/Agent.exe -> Release/Agent.exe
                # 重命名文件和路径
                arcname = dest_pattern

    # 统一压缩包内的路径分隔符为 '/'
    return arcname.replace(os.path.sep, '/')


def process_ignore_rules(rules, source_dir_abs):
    """
    处理所有忽略规则，返回被忽略文件的集合。

    :param rules: 规则列表
    :param source_dir_abs: 源目录的绝对路径
    :return: 被忽略文件的绝对路径集合
    """
    ignored_files = set()
    print("--- 处理忽略规则 ---")

    ignore_rules = filter(lambda x: x['is_ignore'] is True, rules)
    for rule in ignore_rules:
        source_pattern = rule['source']
        print("规则: '!{0}'".format(source_pattern))

        matched_paths = find_matching_files(source_dir_abs, source_pattern)

        # 将匹配到的文件添加到忽略集合中
        for found_abs_path in matched_paths:
            if os.path.isfile(found_abs_path):
                ignored_files.add(found_abs_path)

    return ignored_files


def process_add_rules(rules, source_dir_abs, ignored_files):
    """
    处理所有添加规则，返回要添加到压缩包的文件列表。

    :param rules: 规则列表
    :param source_dir_abs: 源目录的绝对路径
    :param ignored_files: 被忽略文件的集合
    :return: 要添加到压缩包的文件列表，每个元素是一个元组 (源文件路径, 压缩包内路径)
    """
    # 使用列表来存储要添加的文件，每个元素是一个元组 (源文件路径, 目标路径)
    # 这样允许同一个源文件出现多次，每次都有不同的目标路径
    files_to_add = []

    print("\n--- 处理添加规则 ---")

    add_rules = filter(lambda x: x['is_ignore'] is False, rules)
    for rule in add_rules:
        source_pattern = rule['source']
        dest_pattern = rule['dest']

        # 使用辅助函数查找匹配的文件
        matched_paths = find_matching_files(source_dir_abs, source_pattern)

        # --- 这是个普通(添加)规则 ---
        if not matched_paths:
            print("\n{red}!!! MISSING: {0}{reset}".format(
                rule['source'], red=COLORS['red'], reset=COLORS['reset']))
            print("{yellow}--- 规则未匹配到任何文件，请检查路径或文件名。按回车键继续... ---{reset}".format(
                yellow=COLORS['yellow'], reset=COLORS['reset']))
            raw_input()  # Python 2
            sys.exit(2)

        if dest_pattern:
            print("规则: '{0} -> {1}'".format(source_pattern, dest_pattern))
        else:
            print("规则: '{0}'".format(source_pattern))

        for found_abs_path in matched_paths:
            # 只处理文件，跳过目录
            if not os.path.isfile(found_abs_path):
                continue

            # 使用辅助函数计算文件在压缩包中的路径
            relative_found_path = os.path.relpath(found_abs_path, source_dir_abs)
            arcname = calculate_arcname(relative_found_path, source_pattern, dest_pattern)

            # 检查文件是否被忽略规则排除
            if found_abs_path in ignored_files:
                print("{yellow}  [忽略] '{0}'{reset}".format(
                    relative_found_path, yellow=COLORS['yellow'], reset=COLORS['reset']))
            else:
                files_to_add.append((found_abs_path, arcname))
                print("{green}  [添加] '{0}' -> '{1}'{reset}".format(
                    relative_found_path, arcname, green=COLORS['green'], reset=COLORS['reset']))

    return files_to_add


def create_zip_from_list(source_dir, ziplist_path, output_zip_path):
    """
    根据 .ziplist 文件的规则，从源目录打包文件到 ZIP 压缩包。

    处理规则时，先处理所有的忽略规则，生成忽略列表，然后再处理添加规则。
    当一个文件被添加规则匹配并且也在忽略列表中时，会显示[忽略]信息。

    :param source_dir: 要打包文件的来源目录。
    :param ziplist_path: .ziplist 配置文件的路径。
    :param output_zip_path: 输出的 ZIP 文件路径。
    """
    # 确保源目录和配置文件存在
    if not os.path.isdir(source_dir):
        print("{red}错误：源目录 '{0}' 不存在。{reset}".format(
            source_dir, red=COLORS['red'], reset=COLORS['reset']))
        return
    if not os.path.isfile(ziplist_path):
        print("{red}错误：配置文件 '{0}' 不存在。{reset}".format(
            ziplist_path, red=COLORS['red'], reset=COLORS['reset']))
        return

    # --- 1. 解析 .ziplist 文件 ---
    rules = parse_ziplist_file(ziplist_path)

    # --- 2. 先处理所有的忽略规则，建立忽略文件列表 ---
    source_dir_abs = os.path.abspath(source_dir)
    ignored_files = process_ignore_rules(rules, source_dir_abs)

    # --- 3. 然后处理添加规则 ---
    files_to_add = process_add_rules(rules, source_dir_abs, ignored_files)

    # --- 4. 执行打包 ---
    if not files_to_add:
        print("\n没有需要打包的文件，操作终止。")
        return

    # 创建输出目录
    out_dir = os.path.dirname(output_zip_path)
    if out_dir and not os.path.exists(out_dir):
        os.makedirs(out_dir)

    # 打包文件
    create_zip_file(files_to_add, output_zip_path)


def parse_ziplist_file(ziplist_path):
    """
    解析 .ziplist 文件内容，返回规则列表。

    :param ziplist_path: .ziplist 配置文件的路径
    :return: 规则列表，每个规则是一个字典，包含 source、dest 和 is_ignore 字段
    """
    rules = []
    # Use io.open for Python 2.7 compatibility with encoding
    with io.open(ziplist_path, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            # 忽略注释行和空行
            if not line or line.startswith('#'):
                continue

            # Check for negation `!` prefix
            is_ignore = line.startswith('!')
            if is_ignore:
                # 移除 '!' 和前面的空格
                line = line[1:].lstrip()

            # 分割源和目标路径
            if '->' in line:
                parts = line.split('->', 1)
                source_pattern = parts[0].strip()
                dest_pattern = parts[1].strip()
            else:
                source_pattern = line
                dest_pattern = None

            # Store rule with `is_ignore` flag
            rules.append({'source': source_pattern, 'dest': dest_pattern, 'is_ignore': is_ignore})

    return rules


def create_zip_file(files_to_add, output_zip_path):
    """
    将指定的文件列表打包到 ZIP 文件中。

    :param files_to_add: 要添加到压缩包的文件列表，每个元素是一个元组 (源文件路径, 压缩包内路径)
    :param output_zip_path: 输出的 ZIP 文件路径
    """
    print("\n--- 开始创建 ZIP 文件: {0} ---".format(output_zip_path))

    # 使用字典来记录每个目标路径被使用的情况
    arcname_sources = {}
    has_duplicates = False

    with zipfile.ZipFile(output_zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
        for source_path, arcname in files_to_add:
            if arcname in arcname_sources:
                # 如果是同一个源文件要添加到不同位置，这是允许的
                # 如果是不同源文件要添加到同一个位置，这是需要警告的
                if source_path != arcname_sources[arcname]:
                    print("{yellow}警告：压缩包内路径 '{0}' 重复，源文件 '{1}' 将会覆盖 '{2}'。{reset}".format(
                        arcname, source_path, arcname_sources[arcname],
                        yellow=COLORS['yellow'], reset=COLORS['reset']))
                    has_duplicates = True
                else:
                    # 同一个源文件添加到同一位置，忽略。
                    print("{yellow}警告：同一个源文件多次添加到同一位置 '{0}' -> '{1}' 忽略！{reset}".format(
                        source_path, arcname,
                        yellow=COLORS['yellow'], reset=COLORS['reset']))
                    continue
            # 打包进 zip 文件
            zipf.write(source_path, arcname)
            arcname_sources[arcname] = source_path

    if has_duplicates:
        print("\n{yellow}提示：打包过程中存在同名文件覆盖，请检查您的 .ziplist 规则。{reset}".format(
            yellow=COLORS['yellow'], reset=COLORS['reset']))

    print("\n{green}成功！总共打包了 {0} 个文件到 '{1}'。{reset}".format(
        len(files_to_add), output_zip_path, green=COLORS['green'], reset=COLORS['reset']))
    time.sleep(2)


if __name__ == '__main__':
    # 设置命令行参数解析器
    parser = argparse.ArgumentParser(
        description="根据 .ziplist 文件打包项目文件到 .zip 压缩包。",
    )
    parser.add_argument(
        "ziplist_path",
        help="要处理的 .ziplist 配置文件的路径。"
    )
    args = parser.parse_args()

    # 获取 .ziplist 文件的绝对路径
    # 在 Python 2 中，命令行参数是 str 类型（bytes），需要解码
    ziplist_arg = args.ziplist_path.decode(sys.getfilesystemencoding())
    ziplist_abs_path = os.path.abspath(ziplist_arg)

    # 检查文件是否存在
    if not os.path.isfile(ziplist_abs_path):
        # ziplist_abs_path 现在是 unicode，可以安全地格式化
        print("{red}错误：指定的配置文件不存在: {0}{reset}".format(
            ziplist_abs_path, red=COLORS['red'], reset=COLORS['reset']))
        sys.exit(1)  # 以错误码退出

    # 约定：源文件目录就是 .ziplist 文件所在的目录
    source_dir = os.path.dirname(ziplist_abs_path)

    # 根据 .ziplist 的文件名，生成对应的 .zip 文件名
    base_name = os.path.splitext(os.path.basename(ziplist_abs_path))[0]
    output_zip_path = os.path.join(source_dir, "{0}.zip".format(base_name))

    print("=" * 60)
    print("源文件目录: {0}".format(source_dir))
    print("配置文件:     {0}".format(ziplist_abs_path))
    print("输出压缩包:   {0}".format(output_zip_path))
    print("=" * 60)

    # 调用核心功能函数
    create_zip_from_list(
        source_dir=source_dir,
        ziplist_path=ziplist_abs_path,
        output_zip_path=output_zip_path
    )
