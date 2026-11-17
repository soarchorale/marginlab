"""Bounded activity memory with throttled persistence; DB revocation remains authoritative."""
from collections import OrderedDict
import threading
from .storage import connect


class SessionActivity:
    def __init__(self,path,interval=60,limit=4096):
        self.path=path;self.interval=interval;self.limit=limit
        self.lock=threading.Lock();self.recent=OrderedDict()

    def effective_seen(self,row):
        with self.lock:
            entry=self.recent.get(row['token_hash'])
            # A changed database timestamp invalidates this process's old memory.
            return max(row['last_seen'],entry[1]) if entry and entry[0]==row['last_seen'] else row['last_seen']

    def touch(self,row,now):
        with self.lock:
            key=row['token_hash'];saved=row['last_seen']
            if now-saved>=self.interval:
                with connect(self.path) as db:
                    db.execute('UPDATE sessions SET last_seen=MAX(last_seen,?) WHERE token_hash=?',(now,key))
                saved=now
            self.recent[key]=(saved,now);self.recent.move_to_end(key)
            while len(self.recent)>self.limit:self.recent.popitem(last=False)

    def cleanup(self,now,idle):
        with self.lock:
            # Flush activity before deleting idle rows. Requests use the same lock.
            with connect(self.path) as db:
                for key,(saved,seen) in list(self.recent.items()):
                    if seen>saved:
                        db.execute('UPDATE sessions SET last_seen=? WHERE token_hash=? AND last_seen=?',(seen,key,saved))
                    if seen<=now-idle:del self.recent[key]
                    else:self.recent[key]=(seen,seen)
                db.execute('DELETE FROM sessions WHERE expires<=? OR last_seen<=?',(now,now-idle))
                db.execute('DELETE FROM login_attempts WHERE attempted<=?',(now-900,))
