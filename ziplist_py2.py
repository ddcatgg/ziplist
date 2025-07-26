# -*- coding: utf-8 -*-
from __future__ import unicode_literals, print_function
import os
import glob
import zipfile
import shutil
import argparse
import io  # For Python 2.7 compatible open with encoding
import sys # For sys.exit
import fnmatch # For glob recursive backport

def create_zip_from_list(source_dir, ziplist_path, output_zip_path):
    """
    根据 .ziplist 文件的规则，从源目录打包文件到 ZIP 压缩包。

    :param source_dir: 要打包文件的来源目录。
    :param ziplist_path: .ziplist 配置文件的路径。
    :param output_zip_path: 输出的 ZIP 文件路径。
    """
    # 确保源目录和配置文件存在
    if not os.path.isdir(source_dir):
        print(u"错误：源目录 '{0}' 不存在。".format(source_dir))
        return
    if not os.path.isfile(ziplist_path):
        print(u"错误：配置文件 '{0}' 不存在。".format(ziplist_path))
        return

    # --- 1. 解析 .ziplist 文件 ---
    rules = []
    # Use io.open for Python 2.7 compatibility with encoding
    with io.open(ziplist_path, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            # 忽略注释行和空行
            if not line or line.startswith('#'):
                continue
            
            # <<< NEW FEATURE: Check for negation `!` prefix >>>
            is_negative = line.startswith('!')
            if is_negative:
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
            
            # 将路径分隔符统一为 OS 标准，以便 glob 匹配
            source_pattern = source_pattern.replace('/', os.path.sep).replace('\\', os.path.sep)
            if dest_pattern:
                dest_pattern = dest_pattern.replace('/', os.path.sep).replace('\\', os.path.sep)

            # Store rule with its type (positive or negative)
            rules.append({'source': source_pattern, 'dest': dest_pattern, 'negative': is_negative})

    # --- 2. 根据规则搜集和排除文件 (顺序处理) ---
    # 使用字典来存储最终要添加的文件，键是源文件绝对路径，值是压缩包内的目标路径(arcname)
    # 这样可以自然地处理规则覆盖和忽略的问题
    files_to_add = {}
    source_dir_abs = os.path.abspath(source_dir)

    print("--- 开始处理打包规则 (严格按顺序) ---")
    for rule in rules:
        source_pattern = rule['source']
        dest_pattern = rule['dest']
        
        # 构建完整的 glob 搜索模式
        glob_pattern = os.path.join(source_dir_abs, source_pattern)
        
        # --- Python 2.7 glob backport for recursive=True ---
        matched_paths = []
        if '**' in source_pattern:
            # 使用 os.walk 和 fnmatch 模拟 Python 3 中 glob 的 recursive=True 功能
            # 这比 Py3 的实现效率低，但对于保证功能正确性是可靠的标准库方案
            for root, dirnames, filenames in os.walk(source_dir_abs):
                # 同时检查目录和文件
                for item in dirnames + filenames:
                    full_path = os.path.join(root, item)
                    relative_path = os.path.relpath(full_path, source_dir_abs)
                    
                    # fnmatch 需要将路径分隔符统一为'/'，且不支持'**'
                    # 我们用 '*' 替换 '**'，因为 os.walk 已经处理了递归，这里的效果是匹配任意字符
                    pattern_for_fnmatch = source_pattern.replace('**', '*').replace(os.path.sep, '/')
                    relative_path_for_fnmatch = relative_path.replace(os.path.sep, '/')
                    
                    if fnmatch.fnmatch(relative_path_for_fnmatch, pattern_for_fnmatch):
                        matched_paths.append(full_path)
        else:
            # 对于不含'**'的普通模式，直接使用 glob
            matched_paths = glob.glob(glob_pattern)
        # --- End of backport ---

        # <<< NEW LOGIC: Handle positive and negative rules differently >>>
        if not rule['negative']:
            # --- 这是个普通(添加)规则 ---
            if not matched_paths:
                # <<< REQUIREMENT 2: MODIFIED WARNING AND PAUSE >>>
                print(u"\n!!! MISSING: {0}".format(rule['source']))
                raw_input("--- 规则未匹配到任何文件，请检查路径或文件名。按回车键继续... ---")
                continue
            
            print(u"规则: '{0}'".format(rule['source']))
            for found_abs_path in matched_paths:
                # 只处理文件，跳过目录
                if not os.path.isfile(found_abs_path):
                    continue
                
                # --- 3. 计算文件在压缩包内的目标路径 (arcname) ---
                arcname = ''
                relative_found_path = os.path.relpath(found_abs_path, source_dir_abs)

                if dest_pattern is None:
                    # 如果规则不包含 '->'
                    if '**' in source_pattern:
                        # 规则: Sounds/**
                        # 效果: 将 Sounds/sub/c.ogg 打包为 sub/c.ogg (保留相对路径)
                        # 实现方式：从文件的相对路径中，移除模式中的静态基本路径
                        pattern_base = source_pattern.split('**')[0]

                        # 如果 pattern_base 为空（例如规则是 '**/*.dll'），则不移除任何部分
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
                        # 效果: 递归地将 Sounds 下所有文件和目录结构复制到 Sounds1 下
                        base_src = source_pattern.split('**')[0]
                        wildcard_match = os.path.relpath(relative_found_path, base_src)
                        base_dest = dest_pattern.split('**')[0]
                        arcname = os.path.join(base_dest, wildcard_match)
                    elif '*' in source_pattern:
                        # 规则: Sounds/*.* -> Sounds1/*.*
                        # 效果: 将 Sounds 目录下的文件打包到 Sounds1 目录下
                        src_parent_dir = os.path.dirname(source_pattern)
                        dest_parent_dir = os.path.dirname(dest_pattern)
                        file_name = os.path.basename(relative_found_path)
                        
                        # 如果源模式是类似 `*.*` 这样没有目录的，则直接用目标目录
                        if not src_parent_dir:
                            arcname = os.path.join(dest_parent_dir, file_name)
                        else:
                            # 保持相对路径结构
                            relative_to_src_parent = os.path.relpath(relative_found_path, src_parent_dir)
                            arcname = os.path.join(dest_parent_dir, relative_to_src_parent)
                    else:
                        # 规则: Debug/Agent.exe -> Release/Agent.exe
                        # 效果: 精确重命名
                        arcname = dest_pattern
                
                # 统一压缩包内的路径分隔符为 '/'
                arcname = arcname.replace(os.path.sep, '/')
                # In Python 2, paths from os functions might be bytes, ensure they are unicode
                # if unicode_literals is on, they should be, but let's be safe.
                files_to_add[unicode(found_abs_path)] = unicode(arcname)
                print(u"  [添加] '{0}' -> '{1}'".format(relative_found_path, arcname))

        else:
            # --- 这是个忽略(!)规则 ---
            print(u"规则: '!{0}'".format(rule['source']))
            for found_abs_path in matched_paths:
                if found_abs_path in files_to_add:
                    relative_path_to_remove = os.path.relpath(found_abs_path, source_dir_abs)
                    print(u"  [忽略] '{0}'".format(relative_path_to_remove))
                    del files_to_add[found_abs_path]


    # --- 4. 执行打包 ---
    if not files_to_add:
        print(u"\n没有需要打包的文件，操作终止。")
        return
        
    print(u"\n--- 开始创建 ZIP 文件: {0} ---".format(output_zip_path))
    # 确保输出目录存在
    # os.makedirs(os.path.dirname(output_zip_path) or '.', exist_ok=True) # exist_ok not in Py2
    out_dir = os.path.dirname(output_zip_path)
    if out_dir and not os.path.exists(out_dir):
        os.makedirs(out_dir)

    # 使用集合来检测最终是否有重名的目标路径
    added_arcnames = set()
    has_duplicates = False

    with zipfile.ZipFile(output_zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
        for source_path, arcname in files_to_add.items():
            if arcname in added_arcnames:
                print(u"警告：压缩包内路径 '{0}' 重复，源文件 '{1}' 将会覆盖之前的文件。".format(arcname, source_path))
                has_duplicates = True
            # arcname must be a byte string in py2 zipfile
            zipf.write(source_path, arcname.encode('utf-8'))
            added_arcnames.add(arcname)
    
    if has_duplicates:
         print(u"\n提示：打包过程中存在同名文件覆盖，请检查您的 .ziplist 规则。")
    
    print(u"\n成功！总共打包了 {0} 个文件到 '{1}'。".format(len(files_to_add), output_zip_path))


# (测试函数保留，但不再从主程序调用)
def setup_test_environment(base_dir="test_project"):
    """创建一个用于测试的文件结构。"""
    print(u"--- 正在创建测试环境 at '{0}' ---".format(base_dir))
    if os.path.exists(base_dir):
        shutil.rmtree(base_dir)
    
    paths_to_create = [
        "SipVoice.dll",
        "Ping.dll",
        "Debug/TrayIconDll.dll",
        "Debug/AgentExe.exe",
        "res/AgentExe.ico",
        "Sounds/a.wav",
        "Sounds/b.mp3",
        "Sounds/sub/c.ogg",
        "Sounds/sub/another.wav"
    ]

    for p in paths_to_create:
        full_path = os.path.join(base_dir, p)
        out_dir = os.path.dirname(full_path)
        if not os.path.exists(out_dir):
            os.makedirs(out_dir)
        with open(full_path, 'w') as f:
            f.write("this is {0}".format(p))
    print("测试文件创建完毕。")

def create_test_ziplist(filepath=".ziplist"):
    """创建一个用于测试的 .ziplist 文件。"""
    print(u"--- 正在创建测试配置文件 at '{0}' ---".format(filepath))
    content = u"""
# 这是一个演示 '!' 忽略语法的 .ziplist 文件

# 1. 首先，包含 Sounds 目录下的所有内容，保留其内部目录结构
Sounds/**

# 2. 然后，使用 '!' 忽略掉所有的 .wav 文件
#    注意 **/*.wav 可以匹配任意子目录下的 .wav 文件
!**/*.wav

# 3. 再忽略掉 Debug 目录下的所有内容
Debug/**

# 4. 但是，我还是需要 Debug 目录下的 AgentExe.exe，并重命名它
#    因为 'Debug/**' 已经把它添加进来了，而 '!' 规则没有匹配它，所以它依然在列表里
#    这里我们用一个更精确的规则覆盖它，并给它一个新的目标路径
Debug/AgentExe.exe -> bin/Agent.exe

# 5. 最后，添加一个根目录的文件
Ping.dll
"""
    with io.open(filepath, 'w', encoding='utf-8') as f:
        f.write(content)
    print("测试配置文件创建完毕。")


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
    # In Py2, argv can be bytes, decode it
    ziplist_arg = args.ziplist_path.decode(sys.getfilesystemencoding())
    ziplist_abs_path = os.path.abspath(ziplist_arg)

    # 检查文件是否存在
    if not os.path.isfile(ziplist_abs_path):
        print(u"错误：指定的配置文件不存在: {0}".format(ziplist_abs_path))
        sys.exit(1) # 以错误码退出

    # 约定：源文件目录就是 .ziplist 文件所在的目录
    source_dir = os.path.dirname(ziplist_abs_path)
    
    # 根据 .ziplist 的文件名，生成对应的 .zip 文件名
    base_name = os.path.splitext(os.path.basename(ziplist_abs_path))[0]
    output_zip_path = os.path.join(source_dir, u"{0}.zip".format(base_name))

    print("="*60)
    print(u"源文件目录: {0}".format(source_dir))
    print(u"配置文件:     {0}".format(ziplist_abs_path))
    print(u"输出压缩包:   {0}".format(output_zip_path))
    print("="*60)

    # 调用核心功能函数
    create_zip_from_list(
        source_dir=source_dir,
        ziplist_path=ziplist_abs_path,
        output_zip_path=output_zip_path
    )
