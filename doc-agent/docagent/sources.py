"""Authorized source registrations. Accessibility probes never traverse documents."""
import json
import os
import sqlite3
import uuid
from pathlib import Path, PureWindowsPath
from datetime import datetime, timezone


class SourcePolicy:
    def __init__(self, roots):
        if not isinstance(roots,list) or not roots or not all(isinstance(r,str) for r in roots):
            raise ValueError('Configure DOCAGENT_SOURCE_ROOTS as a JSON array of absolute allowed folders')
        self.roots=[self.absolute(r) for r in roots]

    @staticmethod
    def absolute(value):
        if not isinstance(value,str) or not value.strip() or any(ord(c)<32 for c in value):raise ValueError('Enter a valid absolute folder path')
        if value.startswith(('\\\\?\\','\\\\.\\')):raise ValueError('Windows device and extended paths are not supported')
        if os.name=='nt':
            win=PureWindowsPath(value)
            if not win.is_absolute() or ':' in str(win)[len(win.drive):]:raise ValueError('Use an absolute drive path or UNC folder path')
        path=Path(value)
        if not path.is_absolute():raise ValueError('Use an absolute folder path')
        return Path(os.path.abspath(path))

    @staticmethod
    def within(path,root):
        try:return os.path.commonpath([os.path.normcase(str(path)),os.path.normcase(str(root))])==os.path.normcase(str(root))
        except ValueError:return False

    def validate(self,value):
        candidate=self.absolute(value)
        matches=[root for root in self.roots if self.within(candidate,root)]
        if not matches:raise ValueError('Folder is outside the administrator-configured allowed roots')
        try:
            resolved=candidate.resolve(strict=True)
            allowed=[root.resolve(strict=True) for root in matches]
            if not any(self.within(resolved,root) for root in allowed):raise ValueError('A symbolic link or junction leaves the allowed root')
            if not resolved.is_dir():raise ValueError('The path must be an existing folder')
            # Opening a directory checks the running account's directory read access.
            # Do not iterate entries, descend into children, or open documents.
            with os.scandir(resolved):pass
            return str(candidate),str(resolved)
        except PermissionError:raise ValueError('Read access denied. Grant the Project RICH process account access to this folder')
        except FileNotFoundError:raise ValueError('Folder was not found or the network share is unavailable')
        except OSError:raise ValueError('Folder is inaccessible. Check the path, network connection, and process account permissions')


class Sources:
    def __init__(self,store,policy):
        self.store=store;self.policy=policy
        with store.db() as db:
            # Module-owned migration ledger avoids changing Library's legacy user_version.
            db.execute('CREATE TABLE IF NOT EXISTS source_schema_migrations (version INTEGER PRIMARY KEY,applied_at TEXT NOT NULL)')
            db.execute('BEGIN IMMEDIATE') if not db.in_transaction else None
            if not db.execute('SELECT 1 FROM source_schema_migrations WHERE version=1').fetchone():
                db.execute('''CREATE TABLE IF NOT EXISTS project_sources (
                  id TEXT PRIMARY KEY,name TEXT NOT NULL,source_type TEXT NOT NULL CHECK(source_type IN ('local','network')),
                  path TEXT NOT NULL,resolved_path TEXT NOT NULL,enabled INTEGER NOT NULL DEFAULT 1,
                  description TEXT NOT NULL DEFAULT '',created_at TEXT NOT NULL,updated_at TEXT NOT NULL,
                  last_scan_at TEXT,indexed_files INTEGER NOT NULL DEFAULT 0,UNIQUE(resolved_path))''')
                db.execute('INSERT INTO source_schema_migrations VALUES (?,?)',(1,self.now()))

    @staticmethod
    def now():return datetime.now(timezone.utc).isoformat()

    def get(self,identity):
        with self.store.db() as db:row=db.execute('SELECT * FROM project_sources WHERE id=?',(identity,)).fetchone()
        if not row:raise KeyError(identity)
        result=dict(row);result['enabled']=bool(result['enabled'])
        try:
            _,resolved=self.policy.validate(result['path'])
            if os.path.normcase(resolved)!=os.path.normcase(result['resolved_path']):raise ValueError('Folder target changed since registration; edit and revalidate before use')
            result.update(accessibility='accessible',accessibility_message='Folder is accessible to the Project RICH process')
        except ValueError as exc:result.update(accessibility='inaccessible',accessibility_message=str(exc))
        return result

    def list(self):
        with self.store.db() as db:ids=[r['id'] for r in db.execute('SELECT id FROM project_sources ORDER BY created_at,id')]
        return [self.get(identity) for identity in ids]

    def save(self,data,identity=None):
        if identity:
            with self.store.db() as db:
                if not db.execute('SELECT 1 FROM project_sources WHERE id=?',(identity,)).fetchone():raise KeyError(identity)
        name=data.get('name','').strip();kind=data.get('source_type');description=data.get('description','').strip()
        if not name or len(name)>120:raise ValueError('Source name must contain 1–120 characters')
        if kind not in ('local','network'):raise ValueError('Choose local folder or network/NAS folder')
        if len(description)>2000:raise ValueError('Description must be at most 2000 characters')
        if not isinstance(data.get('enabled',True),bool):raise ValueError('Enabled must be true or false')
        path,resolved=self.policy.validate(data.get('path'))
        now=self.now();source_id=identity or uuid.uuid4().hex
        try:
            with self.store.db() as db:
                if identity:db.execute('UPDATE project_sources SET name=?,source_type=?,path=?,resolved_path=?,enabled=?,description=?,updated_at=? WHERE id=?',(name,kind,path,resolved,int(data.get('enabled',True)),description,now,source_id))
                else:db.execute('INSERT INTO project_sources (id,name,source_type,path,resolved_path,enabled,description,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?)',(source_id,name,kind,path,resolved,int(data.get('enabled',True)),description,now,now))
        except sqlite3.IntegrityError:raise ValueError('This folder is already registered')
        return self.get(source_id)

    def set_enabled(self,identity,enabled):
        if not isinstance(enabled,bool):raise ValueError('Enabled must be true or false')
        with self.store.db() as db:
            result=db.execute('UPDATE project_sources SET enabled=?,updated_at=? WHERE id=?',(int(enabled),self.now(),identity))
            if not result.rowcount:raise KeyError(identity)
        return self.get(identity)

    def remove(self,identity):
        with self.store.db() as db:
            if not db.execute('DELETE FROM project_sources WHERE id=?',(identity,)).rowcount:raise KeyError(identity)
