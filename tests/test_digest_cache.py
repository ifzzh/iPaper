import hashlib
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from ipaper.processing import digest_cache as cache
from ipaper.security.identity import Identity,run_as_identity


def test_stable_stat_reuses_digest_but_same_size_edit_and_replacement_invalidate(tmp_path,monkeypatch):
 p=tmp_path/'paper.pdf';p.write_bytes(b'first pdf')
 real=cache._scan;calls=[]
 def scan(f):calls.append(1);return real(f)
 monkeypatch.setattr(cache,'_scan',scan)
 owner=Identity('digest-one','one','user')
 read=lambda:run_as_identity(owner,cache.file_digest,p)
 first=read();assert read()==first and len(calls)==1
 before=p.stat();p.write_bytes(b'other pdf');os.utime(p,ns=(before.st_atime_ns,before.st_mtime_ns))
 assert read()==hashlib.sha256(b'other pdf').hexdigest() and len(calls)==2
 replacement=tmp_path/'new';replacement.write_bytes(b'third pdf');replacement.replace(p)
 assert read()==hashlib.sha256(b'third pdf').hexdigest() and len(calls)==3
 assert run_as_identity(Identity('digest-two','two','user'),cache.file_digest,p)==read()
 assert len(calls)==4


def test_range_request_burst_shares_one_hash_scan(tmp_path,monkeypatch):
 p=tmp_path/'large.pdf';p.write_bytes(b'pdf '*10000)
 real=cache._scan;entered=threading.Event();release=threading.Event();calls=[]
 def scan(f):calls.append(1);entered.set();assert release.wait(3);return real(f)
 monkeypatch.setattr(cache,'_scan',scan)
 owner=Identity('digest-burst','one','user')
 with ThreadPoolExecutor(max_workers=4) as pool:
  jobs=[pool.submit(run_as_identity,owner,cache.file_digest,p) for _ in range(4)]
  assert entered.wait(2);release.set()
  values=[f.result() for f in jobs]
 assert len(set(values))==1 and len(calls)==1


def test_without_watch_always_hashes_even_when_stat_is_unchanged(tmp_path,monkeypatch):
 p=tmp_path/'fallback.pdf';p.write_bytes(b'first pdf')
 real=cache._scan;calls=[]
 def scan(f):calls.append(1);return real(f)
 monkeypatch.setattr(cache,'_scan',scan)
 monkeypatch.setattr(cache,'_watch',lambda _:None)
 owner=Identity('digest-fallback','one','user')
 read=lambda:run_as_identity(owner,cache.file_digest,p)
 assert read()==read();assert len(calls)==2
 before=p.stat();p.write_bytes(b'other pdf');os.utime(p,ns=(before.st_atime_ns,before.st_mtime_ns))
 assert read()==hashlib.sha256(b'other pdf').hexdigest();assert len(calls)==3
