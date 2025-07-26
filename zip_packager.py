import os
import glob
import zipfile
import shutil
from pathlib import Path

def create_zip_from_list(source_dir, ziplist_path, output_zip_path):
    """
    根据 .ziplist 文件的规则，从源目录打包文件到 ZIP 压缩包。

    :param source_dir: 要打包文件的来源目录。
    :param ziplist_path: .ziplist 配置文件的路径。
    :param output_zip_path: 输出的 ZIP 文件路径。
    """
    # 确保源目录和配置文件存在
    if not os.path.isdir(source_dir):
        print(f"错误：源目录 '{source_dir}' 不存在。")
        return
    if not os.path.isfile(ziplist_path):
        print(f"错误：配置文件 '{ziplist_path}' 不存在。")
        return

    # --- 1. 解析 .ziplist 文件 ---
    rules = []
    with open(ziplist_path, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            # 忽略注释行和空行
            if not line or line.startswith('#'):
                continue
            
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

            rules.append({'source': source_pattern, 'dest': dest_pattern})

    # --- 2. 根据规则搜集文件 ---
    # 使用字典来存储最终要添加的文件，键是源文件绝对路径，值是压缩包内的目标路径(arcname)
    # 这样可以自然地处理规则覆盖的问题（后面的规则会覆盖前面的）
    files_to_add = {}
    source_dir_abs = os.path.abspath(source_dir)

    print("--- 开始处理打包规则 ---")
    for rule in rules:
        source_pattern = rule['source']
        dest_pattern = rule['dest']
        
        # 构建完整的 glob 搜索模式
        glob_pattern = os.path.join(source_dir_abs, source_pattern)
        
        # 使用 glob 查找匹配的文件和目录
        # recursive=True 使得 ** 能正常工作
        matched_paths = glob.glob(glob_pattern, recursive=True)

        if not matched_paths:
            print(f"警告：规则 '{rule['source']}' 没有匹配到任何文件。")
            continue

        for found_abs_path in matched_paths:
            # 只处理文件，跳过目录
            if not os.path.isfile(found_abs_path):
                continue
            
            # --- 3. 计算文件在压缩包内的目标路径 (arcname) ---
            arcname = ''
            relative_found_path = os.path.relpath(found_abs_path, source_dir_abs)

            if dest_pattern is None:
                # <<< ==================== MODIFIED BLOCK START ==================== >>>
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
                # <<< ===================== MODIFIED BLOCK END ===================== >>>
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
            files_to_add[found_abs_path] = arcname
            print(f"  [匹配] '{relative_found_path}' -> '{arcname}'")

    # --- 4. 执行打包 ---
    if not files_to_add:
        print("\n没有需要打包的文件，操作终止。")
        return
        
    print(f"\n--- 开始创建 ZIP 文件: {output_zip_path} ---")
    # 确保输出目录存在
    os.makedirs(os.path.dirname(output_zip_path) or '.', exist_ok=True)

    # 使用集合来检测最终是否有重名的目标路径
    added_arcnames = set()
    has_duplicates = False

    with zipfile.ZipFile(output_zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
        for source_path, arcname in files_to_add.items():
            if arcname in added_arcnames:
                print(f"警告：压缩包内路径 '{arcname}' 重复，源文件 '{source_path}' 将会覆盖之前的文件。")
                has_duplicates = True
            zipf.write(source_path, arcname)
            added_arcnames.add(arcname)
    
    if has_duplicates:
         print("\n提示：打包过程中存在同名文件覆盖，请检查您的 .ziplist 规则。")
    
    print(f"\n成功！总共打包了 {len(files_to_add)} 个文件到 '{output_zip_path}'。")


# (此部分用于测试，无需修改)
def setup_test_environment(base_dir="test_project"):
    """创建一个用于测试的文件结构。"""
    print(f"--- 正在创建测试环境 at '{base_dir}' ---")
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
        os.makedirs(os.path.dirname(full_path), exist_ok=True)
        with open(full_path, 'w') as f:
            f.write(f"this is {p}")
    print("测试文件创建完毕。")

def create_test_ziplist(filepath=".ziplist"):
    """创建一个用于测试的 .ziplist 文件。"""
    print(f"--- 正在创建测试配置文件 at '{filepath}' ---")
    content = """
# 这是一个示例 .ziplist 文件
# 语法类似于 .gitignore，并增加了 '->' 重定向功能

# 1. 直接打包文件到压缩包根目录
SipVoice.dll
Ping.dll

# 2. 打包指定路径的文件，但只保留文件名到压缩包根目录
Debug/TrayIconDll.dll

# 3. 将文件重命名/重定向到压缩包内指定路径
Debug/AgentExe.exe -> Release/AgentExe.exe
res/AgentExe.ico

# 4. 将 Sounds 目录下的所有文件 (*.*) 打包到压缩包的 Sounds1 目录下
Sounds/*.* -> Sounds1/*.*

# 5. 递归打包 Sounds 目录下所有文件 (**) 到 NewSounds 目录下，并保持目录结构
Sounds/** -> NewSounds/**
"""
    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(content)
    print("测试配置文件创建完毕。")


if __name__ == '__main__':
    # 定义测试环境的目录和文件
    SOURCE_PROJECT_DIR = "test_project"
    ZIPLIST_FILE = ".ziplist"
    OUTPUT_ZIP = os.path.join("build", "MyPackage.zip")
    
    # 1. 准备测试环境
    setup_test_environment(SOURCE_PROJECT_DIR)
    create_test_ziplist(ZIPLIST_FILE)
    
    print("\n" + "="*50)
    # 2. 执行打包程序
    create_zip_from_list(
        source_dir=SOURCE_PROJECT_DIR,
        ziplist_path=ZIPLIST_FILE,
        output_zip_path=OUTPUT_ZIP
    )
    print("="*50 + "\n")

    # (可选) 清理测试环境
    # print("--- 正在清理测试环境 ---")
    # shutil.rmtree(SOURCE_PROJECT_DIR)
    # os.remove(ZIPLIST_FILE)
    # print("清理完毕。")