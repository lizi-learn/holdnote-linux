"""Encrypted durable outbox, stable remote IDs and bounded automatic retries."""
import json
import os
from pathlib import Path
import sqlite3
import threading
import time
import uuid
from cryptography.fernet import Fernet
from .config import private_dir
from .client import DeliveryError


class Outbox:
    def __init__(self,path,key):
        self.path=Path(path);private_dir(self.path.parent);self.cipher=Fernet(key.encode() if isinstance(key,str) else key)
        if self.path.is_symlink():raise RuntimeError('Outbox cannot be a symlink')
        with self.connect() as db:
            db.execute('PRAGMA journal_mode=WAL')
            db.execute('CREATE TABLE IF NOT EXISTS captures (id TEXT PRIMARY KEY,payload BLOB NOT NULL,state TEXT NOT NULL,attempts INTEGER NOT NULL DEFAULT 0,next_at REAL NOT NULL DEFAULT 0,error TEXT,receipt TEXT)')
        self.path.chmod(0o600)
    def connect(self):
        db=sqlite3.connect(self.path,timeout=10);db.row_factory=sqlite3.Row;return db
    def add(self,text):
        capture_id=uuid.uuid4().hex
        payload=self.cipher.encrypt(json.dumps({'text':text},ensure_ascii=False).encode())
        with self.connect() as db:db.execute('INSERT INTO captures(id,payload,state) VALUES(?,?,?)',(capture_id,payload,'pending'))
        return capture_id
    def due(self):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute("SELECT * FROM captures WHERE state IN ('pending','sending') AND next_at<=? ORDER BY rowid LIMIT 1",(time.time(),)).fetchone()
            if row:db.execute("UPDATE captures SET state='sending',next_at=? WHERE id=?",(time.time()+90,row['id']))
        return dict(row) if row else None
    def decode(self,row):return json.loads(self.cipher.decrypt(row['payload']))['text']
    def done(self,capture_id,receipt):
        # Successful text no longer needs to remain in the local outbox.
        empty=self.cipher.encrypt(b'{}')
        with self.connect() as db:db.execute("UPDATE captures SET state='done',payload=?,receipt=?,error=NULL WHERE id=?",(empty,receipt,capture_id))
    def fail(self,capture_id,error,retryable,attempts):
        delay=min(600,2**min(attempts,9))
        with self.connect() as db:db.execute('UPDATE captures SET state=?,attempts=?,next_at=?,error=? WHERE id=?',('pending' if retryable else 'failed',attempts,time.time()+delay,error,capture_id))
    def retry(self):
        with self.connect() as db:db.execute("UPDATE captures SET state='pending',next_at=0 WHERE state IN ('failed','pending')")
    def stats(self):
        with self.connect() as db:return {row['state']:row['n'] for row in db.execute('SELECT state,COUNT(*) n FROM captures GROUP BY state')}
    def wait_seconds(self):
        with self.connect() as db:next_at=db.execute("SELECT MIN(next_at) FROM captures WHERE state IN ('pending','sending')").fetchone()[0]
        return 60 if next_at is None else max(0.1,min(60,next_at-time.time()))


class Worker:
    def __init__(self,outbox,destination,notify):
        self.outbox=outbox;self.destination=destination;self.notify=notify;self.wake=threading.Event();self.stop=threading.Event()
    def once(self):
        row=self.outbox.due()
        if not row:return False
        try:
            text=self.outbox.decode(row);receipt=self.destination.send(text,row['id']);self.outbox.done(row['id'],receipt.url)
            self.notify('saved',len(text))
        except DeliveryError as e:
            attempts=row['attempts']+1;self.outbox.fail(row['id'],e.kind,e.retryable,attempts)
            if attempts==1 or not e.retryable:self.notify('queued' if e.retryable else 'failed',e.kind)
        except Exception:
            # Never print the note, server response or credentials in logs.
            self.outbox.fail(row['id'],'internal',False,row['attempts']+1);self.notify('failed','internal')
        return True
    def run(self):
        while not self.stop.is_set():
            if not self.once():self.wake.wait(self.outbox.wait_seconds());self.wake.clear()
