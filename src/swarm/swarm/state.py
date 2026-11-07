import json
from pathlib import Path
import sqlite3
import os
from datetime import datetime, timezone


def now():
    return datetime.now(timezone.utc).isoformat()


class State:
    """One authoritative transactional event journal; projections are persisted too."""
    def __init__(self, directory):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        if os.geteuid()==0:
            os.chown(self.directory,int(os.getenv('SWARM_OUTPUT_UID','1000')),int(os.getenv('SWARM_OUTPUT_GID','1000')))
        self.db = sqlite3.connect(self.directory / 'state.sqlite')
        self.db.row_factory = sqlite3.Row
        self.db.executescript('''
        PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS snapshot (id INTEGER PRIMARY KEY CHECK(id=1), body TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY AUTOINCREMENT,
          at TEXT NOT NULL, kind TEXT NOT NULL, body TEXT NOT NULL, logged INTEGER NOT NULL DEFAULT 0);
        ''')
        self.data = dict(status='created', round=0, completed_rounds=0, tasks={}, assignments={},
                         submissions=[], decisions=[], public=[], private=[], scratchpads={}, turns=[])
        saved = self.db.execute('SELECT body FROM snapshot WHERE id=1').fetchone()
        if saved:
            self.data = json.loads(saved['body'])

    def save(self):
        with self.db:
            self.db.execute('INSERT OR REPLACE INTO snapshot VALUES (1,?)', (json.dumps(self.data),))

    def event(self, kind, body):
        with self.db:
            cursor = self.db.execute('INSERT INTO events(at,kind,body) VALUES (?,?,?)',
                                     (now(), kind, json.dumps(body)))
            self.db.execute('INSERT OR REPLACE INTO snapshot VALUES (1,?)', (json.dumps(self.data),))
        return cursor.lastrowid

    def public(self, kind, body):
        message = dict(kind=kind, round=self.data['round'], **body)
        self.data['public'].append(message)
        return message

    def board(self, workers):
        rows = []
        for worker in workers:
            submissions = [s for s in self.data['submissions'] if s['worker'] == worker]
            decisions = [d for d in self.data['decisions'] if d['worker'] == worker]
            rows.append(dict(worker=worker, submissions=len(submissions),
                             accepted=sum(d['decision']=='accept' for d in decisions),
                             rejected=sum(d['decision']=='reject' for d in decisions),
                             average_rating=round(sum(d['rating'] for d in decisions)/len(decisions),3) if decisions else None))
        return rows
