"""Shared filename and directory rules for live log lists and downloads."""
import fnmatch
import posixpath
import re
import shlex

from application_config import ignore as config_ignore

CURRENT = r'.*\.(log|out|txt)'
HISTORY = r'.*\.(log|out|txt)(\.[0-9]+([.-][0-9]+)*)?(\.(gz|tar\.gz|bz2|xz|zip))?'
SCOPED_ARCHIVE = r'.*\.(gz|bz2|xz|zip|tgz)'
EXCLUDED_DIRECTORIES = ('apache-flume-*',)


def group_for(path, scoped=False):
    if any(fnmatch.fnmatchcase(part, pattern)
           for part in path.split('/')[:-1] for pattern in EXCLUDED_DIRECTORIES):
        return None
    name = posixpath.basename(path)
    if re.fullmatch(CURRENT, name, re.S):
        return 'current'
    if re.fullmatch(HISTORY, name, re.S) or (scoped and re.fullmatch(SCOPED_ARCHIVE, name, re.S)):
        return 'history'
    return None


def probe_script(name):
    script = config_ignore.probe_script(name)
    if name not in ('application_files.sh', 'application_logs.sh'):
        return script
    prune = ['(', '-type', 'd', '(']
    for i, pattern in enumerate(EXCLUDED_DIRECTORIES):
        if i:
            prune.append('-o')
        prune.extend(['-name', pattern])
    prune.extend([')', ')', '-prune', '-o'])
    # Both probes use these exact expressions before sorting/limits or packing.
    return '\n'.join([
        'current_log_regex='+shlex.quote(CURRENT),
        'history_log_regex='+shlex.quote(HISTORY),
        'scoped_archive_regex='+shlex.quote(SCOPED_ARCHIVE),
        'log_prune=('+shlex.join(prune)+')',
        r'''log_filter() {
  log_names=(-regex "$current_log_regex")
  if [[ "$1" != current ]]; then
    log_names=(\( -regex "$history_log_regex")
    if [[ "$2" != '*' ]]; then log_names+=(-o -regex "$scoped_archive_regex"); fi
    log_names+=(\))
    if [[ "$1" == history ]]; then log_names+=(! -regex "$current_log_regex"); fi
  fi
}
# Empty files are valid; reject binary content in the first 4 KiB of text logs.
log_text_test=(-exec bash -c '[[ ! -s "$1" ]] || head -c 4096 -- "$1" | grep -Iq ""' _ '{}' ';')''',
        script,
    ])
