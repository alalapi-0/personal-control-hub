"""Finite, payload-free projections of independently overwritten saved batches."""
import re
from pathlib import Path
from hub.connection_records import content_hash
from hub.metric_documents import Projection
from hub.metric_sources import metadata_path, read_metadata

MAX_BYTES = 512 * 1024
MAX_LINES = 10000
MAX_RECORDS = 2000
CODE = re.compile(r'(?:1PON-\d{6}_\d{3}|[A-Z]{2,6}-\d{2,4})', re.I)


def _read(p, name, parser):
    try:
        path = metadata_path(p.root, name)
        before = path.stat()
        if before.st_size > MAX_BYTES:
            raise ValueError('budget')
        raw, _ = read_metadata(p.root, name)
        after = path.stat()
        identity = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns)
        if identity(before) != identity(after) or len(raw) > MAX_BYTES:
            raise ValueError('identity')
        lines = raw.decode('utf-8').splitlines()
        if len(lines) > MAX_LINES:
            raise ValueError('budget')
        return parser(lines)
    except (OSError, ValueError, TypeError, UnicodeError):
        p.problem(name)
        return None


def _id(s):
    # The producer keeps printable interior spaces and deduplicates by upper().
    if not s or len(s) > 180 or not s.isprintable():
        raise ValueError('identity')
    return content_hash(s.upper())


def _codes(lines, jav=False, retry=False):
    if retry and (not lines or lines[0] != '# 通过 --retry-failed 重试成功的番号'):
        raise ValueError('header')
    selected = [s.strip().upper() for s in lines if s.strip() and not s.strip().startswith('#')]
    ids = [_id(s) for s in selected if not jav or CODE.fullmatch(s)]
    if len(ids) > MAX_RECORDS or (retry and len(set(ids)) != len(ids)):
        raise ValueError('records')
    return sorted(set(ids))


def _blocks(lines, heading):
    result, current = [], None
    for line in lines:
        if not line.strip():
            continue
        match = re.fullmatch(heading, line)
        if match:
            current = (_id(match[1]), {})
            result.append(current)
        else:
            if current is None or ': ' not in line:
                raise ValueError('record format')
            key, value = line.split(': ', 1)
            if key in current[1] or not value:
                raise ValueError('field')
            current[1][key] = value
    if len(result) > MAX_RECORDS or len({k for k, _ in result}) != len(result):
        raise ValueError('record identity')
    return result


def _magnet(lines):
    if len(lines) < 2 or lines[0] != '# Magnet Fetcher 输出结果':
        raise ValueError('header')
    m = re.fullmatch(r'# 总数: (\d+) \| 成功: (\d+) \| 未找到: (\d+) \| 异常: (\d+)', lines[1])
    if not m:
        raise ValueError('summary')
    rows = _blocks(lines[2:], r'\[([^\]]+)\]')
    statuses = {'成功': 'success', '未找到': 'not_found', '请求异常': 'error'}
    out = []
    for key, fields in rows:
        if set(fields) != {'状态', '标题', '大小', '磁力链'} or fields['状态'] not in statuses:
            raise ValueError('fields')
        out.append((key, statuses[fields['状态']]))
    if list(map(int, m.groups())) != [len(out), *[sum(s == v for _, s in out) for v in statuses.values()]]:
        raise ValueError('summary disagreement')
    return sorted(out)


def _details(lines, retry=False):
    marker = lines.index('# 详细信息')
    prefix = lines[:marker]
    if not prefix:
        raise ValueError('missing summary header')
    if retry:
        match = re.fullmatch(r'# 重试抓取成功的磁力链接（共 (\d+) 个）', prefix[0])
        if not match:
            raise ValueError('header')
        declared = int(match[1])
    else:
        if prefix[:2] != ['# 每行一个磁力链接，已排除标题含 Reducing Mosaic 的结果', '# 选择规则: 文件体积最大，其次分辨率/码率更高']:
            raise ValueError('header')
        declared = None
    # Accept only the writer's complete prefix grammar, not arbitrary payload.
    if retry:
        pending = None
        prefix_ids = []
        for line in prefix[1:]:
            if not line:
                continue
            if line.startswith('# '):
                if pending is not None:
                    raise ValueError('retry pair')
                pending = _id(line[2:])
            elif line.startswith('magnet:?') and pending is not None:
                prefix_ids.append(pending)
                pending = None
            else:
                raise ValueError('retry prefix')
        if pending is not None or len(prefix_ids) != len(set(prefix_ids)):
            raise ValueError('retry identity')
    else:
        section, section_links = None, {'首次': [], '重试': []}
        section_counts = {}
        for line in prefix[2:]:
            if not line:
                continue
            match = re.fullmatch(r'# === (首次|重试)抓取成功 \((\d+)\) ===', line)
            if match:
                if match[1] in section_counts:
                    raise ValueError('duplicate section')
                section = match[1]
                section_counts[section] = int(match[2])
            elif line.startswith('magnet:?') and section is not None:
                section_links[section].append(content_hash(line))
            else:
                raise ValueError('prefix')
        if set(section_counts) != {'首次', '重试'} or any(section_counts[k] != len(section_links[k]) for k in section_counts):
            raise ValueError('section size')
    rows, links = [], {}
    for key, fields in _blocks(lines[marker+1:], r'## (.+)'):
        if set(fields) == {'状态'} and re.fullmatch(r'未找到可用资源 \(.+\)', fields['状态']) and not retry:
            rows.append((key, 'fail_or_unprocessed', None))
        elif set(fields) in ({'标题', '大小', '磁力'}, {'来源', '标题', '大小', '磁力'}):
            source = fields.get('来源')
            if not fields['磁力'].startswith('magnet:?'):
                raise ValueError('fields')
            rows.append((key, 'success', {'首次抓取': 'initial', '重试抓取': 'retry'}.get(source)))
            links[key] = content_hash(fields['磁力'])
        else:
            raise ValueError('fields')
    # Links are compared in memory, never exported or used as metric versions.
    listed = [content_hash(s) for s in prefix if s.startswith('magnet:?')]
    if sorted(listed) != sorted(links.values()) or (retry and (declared != len(rows) or sorted(prefix_ids) != sorted(links))):
        raise ValueError('link summary')
    if not retry:
        headings = [re.fullmatch(r'# === (首次|重试)抓取成功 \((\d+)\) ===', s) for s in prefix]
        headings = [m for m in headings if m]
        if len(headings) != 2 or [m[1] for m in headings] != ['首次', '重试'] or sum(int(m[2]) for m in headings) != len(links):
            raise ValueError('section summary')
        if all(source is not None for _, status, source in rows if status == 'success'):
            if [int(m[2]) for m in headings] != [sum(source == s for _, _, source in rows) for s in ('initial', 'retry')]:
                raise ValueError('source summary')
        for source, label in [('initial', '首次'), ('retry', '重试')]:
            selected = [links[k] for k, status, src in rows if status == 'success' and src == source]
            if all(src is not None for _, status, src in rows if status == 'success') and sorted(selected) != sorted(section_links[label]):
                raise ValueError('section membership')
    return sorted(rows)


def _log(lines):
    patterns = [r'总计: (\d+) 个番号', r'成功: (\d+)', r'  首次抓取: (\d+)', r'  重试抓取: (\d+)', r'失败: (\d+)']
    matches = [re.fullmatch(pat, s) for pat, s in zip(patterns, lines[:5])]
    if len(matches) != 5 or not all(matches):
        raise ValueError('header')
    rows = []
    for line in lines[5:]:
        if not line:
            continue
        m = re.fullmatch(r'\[(OK/首次|OK/重试|OK|FAIL)\] ([^ ]+) -> .+', line)
        if not m:
            raise ValueError('record')
        rows.append((_id(m[2]), 'fail_or_unprocessed' if m[1] == 'FAIL' else 'success', {'OK/首次': 'initial', 'OK/重试': 'retry'}.get(m[1])))
    if len(rows) > MAX_RECORDS or len({r[0] for r in rows}) != len(rows):
        raise ValueError('identity')
    counts = [int(m[1]) for m in matches]
    if counts[0] != len(rows) or counts[1]+counts[4] != counts[0] or counts[2]+counts[3] != counts[1] or counts[1] != sum(s == 'success' for _, s, _ in rows):
        raise ValueError('summary')
    if all(src is not None for _, s, src in rows if s == 'success') and counts[2:4] != [sum(src == v for _, _, src in rows) for v in ('initial', 'retry')]:
        raise ValueError('source summary')
    return sorted(rows)


def _count(p, name, ids, path, basis=None):
    p.emit(name, len(ids) if ids is not None else None, 'records', path, ids, basis=basis)


def _unknowns(p):
    for name in ('business_timestamp', 'current_code_bound', 'current_run_state', 'input_same_run_bound'):
        p.emit(name, None, 'unix_seconds' if name == 'business_timestamp' else 'bindings', 'adapter:'+p.prefix,
               reason='Saved files do not establish business time, producing code, current execution, or common run identity; mtime is not business time.')


def collect_magnet(root, pid, observed_at, spec=None):
    p = Projection(root, pid, observed_at, 'magnet')
    p.dimensions = {'scope': 'saved_batch'}
    ids = _read(p, 'input/codes.txt', _codes)
    _count(p, 'input_unique_codes', ids, 'input/codes.txt', 'Distinct current input identities; not proven to be the saved output batch.')
    rows = _read(p, 'output/magnets.txt', _magnet)
    _count(p, 'saved_total', [k for k, _ in rows] if rows is not None else None, 'output/magnets.txt')
    for status in ('success', 'not_found', 'error'):
        _count(p, 'saved_'+status, [k for k,s in rows if s == status] if rows is not None else None, 'output/magnets.txt#status')
    _unknowns(p)
    return p.finish()


def collect_jav(root, pid, observed_at, spec=None):
    base = (spec or {}).get('data_root')
    p = Projection(Path(base) if isinstance(base, str) else root, pid, observed_at, 'jav')
    p.dimensions = {'scope': 'saved_batch'}
    if not isinstance(base, str) or not Path(base).is_absolute() or Path(base).is_symlink():
        p.problem('adapter:jav.data_root')
        return p.finish()
    inputs = _read(p, 'jav_codes.txt', lambda ls: _codes(ls, jav=True))
    detail = _read(p, 'magnets.txt', _details)
    log = _read(p, 'magnets_log.txt', _log)
    retry = _read(p, 'magnets_retry.txt', lambda ls: _details(ls, retry=True))
    codes = _read(p, 'magnets_retry_codes.txt', lambda ls: _codes(ls, jav=True, retry=True))
    _count(p, 'input_unique_codes', inputs, 'jav_codes.txt')
    for label, rows, path in [('saved', detail, 'magnets.txt'), ('log', log, 'magnets_log.txt')]:
        _count(p, label+'_total', [k for k,_,_ in rows] if rows is not None else None, path)
        for status in ('success', 'fail_or_unprocessed'):
            _count(p, label+'_'+status, [k for k,s,_ in rows if s == status] if rows is not None else None, path,
                   'Saved result classification; failures include unprocessed records and do not establish attempted failures.')
        valid = rows is not None and all(src is not None for _,s,src in rows if s == 'success')
        for source in ('initial', 'retry'):
            _count(p, label+'_'+source+'_success', [k for k,s,src in rows if s == 'success' and src == source] if valid else None, path,
                   'Mutually exclusive success subsets in this file. Retry success is included in total success and must not be added again.')
    retry_ids = [k for k,_,_ in retry] if retry is not None else None
    _count(p, 'retry_file_success', retry_ids, 'magnets_retry.txt')
    _count(p, 'retry_code_records', codes, 'magnets_retry_codes.txt')
    pairs = [('detail_log', detail, log, lambda a,b: [(k,s) for k,s,_ in a] == [(k,s) for k,s,_ in b] and all(sa is None or sb is None or sa == sb for (_,_,sa),(_,_,sb) in zip(a,b))),
             ('input_detail_membership', inputs, detail, lambda a,b: a == [k for k,_,_ in b]),
             ('retry_membership', retry_ids, codes, lambda a,b: a == b),
             ('retry_success_subset', retry_ids, detail, lambda a,b: set(a) <= {k for k,s,_ in b if s == 'success'}),
             ('retry_partition', codes, log, lambda a,b: all(src is not None for _,s,src in b if s == 'success') and a == [k for k,s,src in b if src == 'retry'])]
    for label, a, b, check in pairs:
        value = int(check(a,b)) if a is not None and b is not None else None
        if label == 'retry_partition' and b is not None and any(src is None for _,status,src in b if status == 'success'):
            value = None
        p.emit(label+'_consistent', value, 'indicators', 'adapter:jav.'+label, [a,b], basis='Saved file membership/status agreement only; independently overwritten files have no transactional run identity.')
        if value == 0:
            p.problem('adapter:jav.'+label, kind='inconsistent_metadata')
    _unknowns(p)
    return p.finish()
