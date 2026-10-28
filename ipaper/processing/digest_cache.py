"""Owner-scoped digest memoization for unchanged regular files, never file bytes.

Every read reopens/stats the file and drains filesystem change notifications.
Stat and watch generation must match; without reliable watches we scan normally.
Concurrent range requests share one scan. Confinement/authorization remain at
every caller; this cache retains digests, never PDF bytes or file descriptors.
"""
import ctypes
import struct
import hashlib
import os
import stat
import threading
from collections import OrderedDict
from concurrent.futures import Future
from pathlib import Path
from ipaper.security.identity import current_user_id

_LOCK = threading.Lock()
_CACHE = OrderedDict()
_FLIGHTS = {}
_LIMIT = 128
# inotify catches same-size edits even on mounts with coarse/frozen timestamps.
# If watches are unavailable, hash normally; stat alone is not sufficient.
try:
    _LIBC = ctypes.CDLL(None, use_errno=True)
    _FD = _LIBC.inotify_init1(os.O_NONBLOCK | os.O_CLOEXEC)
except (AttributeError, OSError):
    _FD = -1
_WATCHES = OrderedDict()
_VERSIONS = {}


def _drain():
    if _FD < 0:
        return
    while True:
        try:
            data = os.read(_FD, 65536)
        except BlockingIOError:
            break
        if not data:
            break
        offset = 0
        while offset < len(data):
            wd, mask, _, length = struct.unpack_from('iIII', data, offset)
            offset += 16 + length
            if mask & 0x4000:  # IN_Q_OVERFLOW: distrust every cached digest.
                _CACHE.clear()
                for watch in _VERSIONS:
                    _VERSIONS[watch] += 1
            elif wd in _VERSIONS:
                _VERSIONS[wd] += 1


def _watch(path):
    if _FD < 0:
        return None
    # Re-add to follow atomic replacements rather than the previous inode.
    wd = _LIBC.inotify_add_watch(_FD, os.fsencode(path), 2 | 4 | 8 | 0x400 | 0x800)
    if wd < 0:
        return None
    old = _WATCHES.get(path)
    _WATCHES[path] = wd; _WATCHES.move_to_end(path)
    if old is not None and old != wd and old not in _WATCHES.values():
        _LIBC.inotify_rm_watch(_FD, old); _VERSIONS.pop(old, None)
    _VERSIONS.setdefault(wd, 0)
    while len(_WATCHES) > _LIMIT:
        _, old = _WATCHES.popitem(last=False)
        if old not in _WATCHES.values():
            _LIBC.inotify_rm_watch(_FD, old); _VERSIONS.pop(old, None)
    return wd



def _identity(info):
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def _scan(handle):
    value = hashlib.sha256()
    for chunk in iter(lambda: handle.read(1024 ** 2), b''):
        value.update(chunk)
    return value.hexdigest()


def file_digest(path):
    owner = current_user_id(required=False)
    path = Path(path)
    with path.open('rb') as handle:
        before = os.fstat(handle.fileno())
        if not stat.S_ISREG(before.st_mode):
            raise ValueError('regular_file_required')
        if not owner:
            return _scan(handle)
        location = str(path.absolute())
        with _LOCK:
            watch = _watch(location)
            _drain()
            version = _VERSIONS.get(watch)
            key = (owner, location, watch, version, *_identity(before))
            if watch is not None:
                if key in _CACHE:
                    value = _CACHE.pop(key); _CACHE[key] = value
                    return value
                future = _FLIGHTS.get(key)
                leader = future is None
                if leader:
                    future = _FLIGHTS[key] = Future()
        if watch is None:
            return _scan(handle)
        if not leader:
            value = future.result()
            # A waiting descriptor may have changed while another reader hashed.
            with _LOCK:
                _drain()
                unchanged = _VERSIONS.get(watch) == version and _WATCHES.get(location) == watch
            if unchanged and _identity(os.fstat(handle.fileno())) == _identity(before):
                return value
            return _scan(handle)
        try:
            value = _scan(handle)
            after = os.fstat(handle.fileno())
            with _LOCK:
                _drain()
                if _VERSIONS.get(watch) == version and _WATCHES.get(location) == watch and _identity(before) == _identity(after):
                    _CACHE[key] = value
                    while len(_CACHE) > _LIMIT:
                        _CACHE.popitem(last=False)
                future.set_result(value)
            return value
        except BaseException as error:
            future.set_exception(error)
            raise
        finally:
            with _LOCK:
                _FLIGHTS.pop(key, None)
