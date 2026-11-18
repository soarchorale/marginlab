"""Bounded per-process analysis cache with coalesced identical requests."""
from collections import OrderedDict
from concurrent.futures import Future, TimeoutError
import json
import threading
import time


class AnalysisCache:
    def __init__(self,max_bytes=32*1024*1024,max_entries=16,ttl=300,max_computations=2,wait_seconds=20):
        self.max_bytes=max_bytes;self.max_entries=max_entries;self.ttl=ttl
        self.max_computations=max_computations;self.wait_seconds=wait_seconds
        self.lock=threading.Lock();self.entries=OrderedDict();self.pending={};self.bytes=0

    def get(self,key,compute):
        now=time.monotonic()
        with self.lock:
            for k,(until,data) in list(self.entries.items()):
                if until<=now:
                    self.bytes-=len(data);del self.entries[k]
            if key in self.entries:
                _,data=self.entries[key];self.entries.move_to_end(key)
                future=None;owner=False
            else:
                data=None;future=self.pending.get(key);owner=future is None
                if owner:
                    if len(self.pending)>=self.max_computations:
                        raise RuntimeError('Analysis is busy; retry shortly')
                    future=Future();self.pending[key]=future
        if data is not None:return json.loads(data),'hit'
        if not owner:
            try:return json.loads(future.result(timeout=self.wait_seconds)),'shared'
            except TimeoutError:raise RuntimeError('Analysis is still running; retry shortly') from None
        try:
            data=json.dumps(compute(),ensure_ascii=False,allow_nan=False,separators=(',',':')).encode()
            with self.lock:
                if len(data)<=self.max_bytes:
                    while self.entries and (self.bytes+len(data)>self.max_bytes or len(self.entries)>=self.max_entries):
                        _,(_,old)=self.entries.popitem(last=False);self.bytes-=len(old)
                    self.entries[key]=(time.monotonic()+self.ttl,data);self.bytes+=len(data)
            future.set_result(data)
            return json.loads(data),'miss'
        except BaseException as exc:
            future.set_exception(exc)
            raise
        finally:
            with self.lock:self.pending.pop(key,None)
