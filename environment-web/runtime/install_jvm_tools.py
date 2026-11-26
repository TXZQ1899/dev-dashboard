"""把 JVM 堆分析依赖（MAT + JDK 21）安装到持久卷 /data/tools。

镜像保持精简，不内置 MAT/JDK；首次部署或更换数据卷后执行一次即可。
脚本可重复执行：目标已存在且结构完整时直接跳过。

    docker --context lenovo exec envscope-local python3 /app/runtime/install_jvm_tools.py

离线环境可先用 --jdk-archive / --mat-archive 指定本地压缩包。
"""

import argparse
import hashlib
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
import zipfile
from pathlib import Path

JDK_VERSION = 'jdk-21.0.13.1+2'
JDK_URL = ('https://mirrors.tuna.tsinghua.edu.cn/Adoptium/21/jdk/x64/linux/'
           'OpenJDK21U-jdk_x64_linux_hotspot_21.0.13.1_2.tar.gz')
JDK_SHA256 = 'bb8a93f9ae6e7a693b2b721e85dbeff26ae1a77447985a8d7afcdd9c7cf6222e'
MAT_URL = ('https://www.eclipse.org/downloads/download.php?file=/mat/1.17.0/rcp/'
           'MemoryAnalyzer-1.17.0.20241016-linux.gtk.x86_64.zip')
MAT_SHA256 = 'e1ea80a7984d411e4c4d7dfbab092da9255f7b772d681a256a61049bcc883767'


def digest(path):
    value = hashlib.sha256()
    with open(path, 'rb') as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b''):
            value.update(chunk)
    return value.hexdigest()


def download(url, destination):
    print('下载 %s' % url, flush=True)
    request = urllib.request.Request(url, headers={'User-Agent': 'envscope-jvm-tools'})
    with urllib.request.urlopen(request, timeout=60) as response, open(destination, 'wb') as output:
        total = int(response.headers.get('Content-Length') or 0)
        got = 0
        while True:
            buffer = response.read(1 << 20)
            if not buffer:
                break
            output.write(buffer)
            got += len(buffer)
            if got // (64 << 20) != (got - len(buffer)) // (64 << 20):
                print('  已下载 %d MB%s' % (got >> 20, ' / %d MB' % (total >> 20) if total else ''), flush=True)
    print('  下载完成 %d MB' % (os.path.getsize(destination) >> 20), flush=True)


def fetch(name, archive, url, expected, skip_verify):
    if archive:
        source = Path(archive)
        if not source.is_file():
            raise SystemExit('%s 压缩包不存在：%s' % (name, archive))
        print('使用本地压缩包 %s' % source, flush=True)
        return source
    handle, path = tempfile.mkstemp(prefix=name + '-', suffix='.download')
    os.close(handle)
    download(url, path)
    if not skip_verify:
        actual = digest(path)
        if actual != expected:
            os.unlink(path)
            raise SystemExit('%s 校验失败：期望 %s，实际 %s（可用 --%s-sha256 覆盖）'
                             % (name, expected, actual, name.lower()))
    return Path(path)


def valid_jdk(home):
    return (home / 'bin' / 'java').is_file()


def valid_mat(home):
    return (home / 'configuration').is_dir() and bool(list((home / 'plugins').glob(
        'org.eclipse.equinox.launcher_*.jar')))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--dir', default='/data/tools', help='安装根目录，默认 /data/tools')
    parser.add_argument('--jdk-archive', help='本地 JDK 21 tar.gz')
    parser.add_argument('--mat-archive', help='本地 MAT zip')
    parser.add_argument('--jdk-url', default=JDK_URL)
    parser.add_argument('--mat-url', default=MAT_URL)
    parser.add_argument('--jdk-sha256', default=JDK_SHA256)
    parser.add_argument('--mat-sha256', default=MAT_SHA256)
    parser.add_argument('--skip-verify', action='store_true', help='跳过 SHA-256 校验（不推荐）')
    parser.add_argument('--force', action='store_true', help='覆盖已安装目录')
    args = parser.parse_args()

    root = Path(args.dir).resolve()
    root.mkdir(parents=True, exist_ok=True)
    jdk_home, mat_root, mat_home = root / JDK_VERSION, root / 'mat', root / 'mat' / 'mat'

    if valid_jdk(jdk_home) and valid_mat(mat_home) and not args.force:
        print('已安装：%s 和 %s（--force 可重装）' % (jdk_home, mat_home))
        return 0

    jdk_archive = mat_archive = None
    try:
        if valid_jdk(jdk_home) and not args.force:
            print('JDK 已存在，跳过：%s' % jdk_home)
        else:
            jdk_archive = fetch('JDK 21', args.jdk_archive, args.jdk_url, args.jdk_sha256, args.skip_verify)
            staging = Path(tempfile.mkdtemp(prefix='jdk-', dir=root))
            with tarfile.open(jdk_archive) as archive:
                archive.extractall(staging)
            extracted = staging / JDK_VERSION
            if not valid_jdk(extracted):
                raise SystemExit('JDK 压缩包结构不符合预期（缺少 %s/bin/java）' % JDK_VERSION)
            if jdk_home.exists():
                shutil.rmtree(jdk_home)
            extracted.rename(jdk_home)
            shutil.rmtree(staging, ignore_errors=True)
            print('JDK 安装完成：%s' % jdk_home)

        if valid_mat(mat_home) and not args.force:
            print('MAT 已存在，跳过：%s' % mat_home)
        else:
            mat_archive = fetch('MAT', args.mat_archive, args.mat_url, args.mat_sha256, args.skip_verify)
            staging = Path(tempfile.mkdtemp(prefix='mat-', dir=root))
            with zipfile.ZipFile(mat_archive) as archive:
                archive.extractall(staging)
            if not valid_mat(staging / 'mat'):
                raise SystemExit('MAT 压缩包结构不符合预期（缺少 mat/plugins 启动器）')
            if mat_root.exists():
                shutil.rmtree(mat_root)
            staging.rename(mat_root)
            print('MAT 安装完成：%s' % mat_home)

        result = subprocess.run([str(jdk_home / 'bin' / 'java'), '-version'],
                                capture_output=True, text=True, timeout=60)
        version = (result.stderr or result.stdout).splitlines()[:1]
        if result.returncode != 0:
            raise SystemExit('JDK 21 无法运行：%s' % (version[0] if version else result.returncode))
        print('版本检查：%s' % (version[0] if version else 'JDK 21'))
        print('完成。JVM 堆分析将自动使用 %s 与 %s。' % (mat_home, jdk_home))
        return 0
    finally:
        for archive in (jdk_archive, mat_archive):
            if archive and archive.parent == Path(tempfile.gettempdir()):
                try:
                    archive.unlink()
                except OSError:
                    pass


if __name__ == '__main__':
    sys.exit(main())
