"""Shared configuration exclusions for live catalogues and endpoint collection."""
import fnmatch
import os
import posixpath
import shlex
from pathlib import Path

RUNTIME = Path(__file__).parent
DEFAULT_IGNORELIST = RUNTIME/'application-config.ignorelist'


def ignore_patterns():
    # Optional operator additions live in the persistent container data volume.
    extra = Path(os.environ.get('ENVSCOPE_DATA', '/data'))/'application-config.ignorelist'
    patterns = []
    for path in (DEFAULT_IGNORELIST, extra):
        if path == extra and not path.exists():
            continue
        for line in path.read_text().splitlines():
            pattern = line.strip()
            if pattern and not pattern.startswith('#') and pattern not in patterns:
                patterns.append(pattern)
    return patterns


def is_ignored(path):
    patterns = ignore_patterns()
    # A directory rule also applies to reads from an older saved catalogue.
    while path and path != '/':
        if any(fnmatch.fnmatchcase(path, pattern) for pattern in patterns):
            return True
        path = posixpath.dirname(path)
    return False


def probe_script(name):
    script = (RUNTIME/'probes'/name).read_text()
    if name not in ('application_files.sh', 'application_configs.sh'):
        return script
    expression = []
    for pattern in ignore_patterns():
        if expression:
            expression.append('-o')
        expression.extend(['-path', pattern])
    prune = ['(', *expression, ')', '-prune', '-o'] if expression else []
    # Pass literals in a Bash array; patterns are never evaluated as shell code.
    return 'config_prune=('+shlex.join(prune)+')\n'+script
