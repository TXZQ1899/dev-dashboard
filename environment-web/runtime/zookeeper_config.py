"""Identify ZooKeeper servers and their explicitly declared configuration."""
import posixpath
import re

MAIN_CLASS = 'org.apache.zookeeper.server.quorum.QuorumPeerMain'


def is_server(process):
    command = process.get('command') or ''
    return bool(re.search(r'(?<!\S)'+re.escape(MAIN_CLASS)+r'(?!\S)', command))


def discover(argv, cwd):
    if argv.count(MAIN_CLASS) != 1:
        return None, '实时启动参数无法唯一确认 ZooKeeper 服务端入口'
    index = argv.index(MAIN_CLASS)
    if index + 2 != len(argv):
        return None, '启动参数未唯一指定 ZooKeeper 主配置文件'
    path = argv[index + 1]
    if not path or '\0' in path or '$' in path or '\n' in path or '\r' in path or path.startswith('-'):
        return None, 'ZooKeeper 主配置路径无法解析'
    if not path.startswith('/'):
        if not cwd or not cwd.startswith('/'):
            return None, '相对配置路径无法解析：进程工作目录不可用'
        path = posixpath.join(cwd, path)
    path = posixpath.normpath(path)
    if path == '/' or path.startswith('//'):
        return None, 'ZooKeeper 主配置路径无效'
    return path, None
