"""Recognize MyBatis mapper XML without excluding general XML configuration."""
import re
from pathlib import PurePosixPath


def mapper_directory(path):
    return any(part.lower() in ('mapper', 'mappers') for part in PurePosixPath(path).parts)


def mapper_path(path):
    if not isinstance(path, str) or not path.lower().endswith('.xml'):
        return False
    parts = PurePosixPath(path.lower()).parts
    return bool(parts) and (any(part in ('mapper', 'mappers') for part in parts[:-1]) or parts[-1].endswith('mapper.xml'))


def mapper_content(data):
    # MyBatis mapper documents declare a mapper namespace or mapper DTD;
    # mybatis-config.xml has a configuration root and remains a config file.
    head = data[:65536]
    return bool(re.search(rb'<!DOCTYPE\s+mapper\b', head, re.I) or
                re.search(rb'<mapper\s+namespace\s*=', head, re.I))
