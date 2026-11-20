import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from docagent.library import Store
from docagent.sources import Sources,SourcePolicy


class SourceTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.base=Path(self.temp.name);self.root=self.base/'allowed';self.root.mkdir()
        self.folder=self.root/'engineering';self.folder.mkdir()
        self.original=self.folder/'original.txt';self.original.write_text('Never change this document')
        self.store=Store(self.base/'db');self.policy=SourcePolicy([str(self.root)])
        self.service=Sources(self.store,self.policy)
        self.data={'name':'Engineering','path':str(self.folder),'source_type':'local','enabled':True,'description':'Rescon demo'}

    def test_create_edit_disable_remove_persist_without_processing(self):
        row=self.service.save(self.data)
        self.assertEqual(row['accessibility'],'accessible');self.assertIsNone(row['last_scan_at']);self.assertEqual(row['indexed_files'],0)
        restored=Sources(Store(self.base/'db'),self.policy)
        self.assertEqual(restored.list()[0]['id'],row['id'])
        changed=restored.save(dict(self.data,name='Updated',description='New description'),row['id'])
        self.assertEqual(changed['name'],'Updated')
        self.assertFalse(restored.set_enabled(row['id'],False)['enabled'])
        self.assertEqual(self.store.search(),[])
        restored.remove(row['id']);self.assertEqual(restored.list(),[])
        self.assertEqual(self.original.read_text(),'Never change this document')
        self.assertEqual(list(self.folder.iterdir()),[self.original])

    def test_reject_relative_outside_sibling_and_file(self):
        sibling=self.base/'allowed-other';sibling.mkdir()
        for candidate in ['relative/path',str(self.base),str(sibling),str(self.original),str(self.root/'missing'),str(self.root/'..'/'outside')]:
            with self.subTest(candidate=candidate),self.assertRaises(ValueError):self.policy.validate(candidate)
        self.assertEqual(self.service.list(),[])

    def test_permission_denial_and_offline_status_can_be_disabled(self):
        row=self.service.save(self.data)
        with patch('docagent.sources.os.scandir',side_effect=PermissionError):
            with self.assertRaisesRegex(ValueError,'Read access denied'):self.service.save(self.data)
            inaccessible=self.service.get(row['id']);self.assertEqual(inaccessible['accessibility'],'inaccessible')
            self.assertFalse(self.service.set_enabled(row['id'],False)['enabled'])
        self.original.unlink();self.folder.rmdir()
        self.assertEqual(self.service.get(row['id'])['accessibility'],'inaccessible')
        self.service.remove(row['id'])

    def test_duplicate_validation_and_idempotent_migration(self):
        row=self.service.save(self.data)
        with self.assertRaisesRegex(ValueError,'already registered'):self.service.save(dict(self.data,name='Duplicate'))
        for data in [dict(self.data,name=' '),dict(self.data,source_type='command'),dict(self.data,enabled='yes')]:
            with self.assertRaises(ValueError):self.service.save(data)
        Sources(self.store,self.policy)
        with self.store.db() as db:self.assertEqual(db.execute('SELECT count(*) FROM source_schema_migrations').fetchone()[0],1)
        self.assertEqual(self.service.get(row['id'])['name'],'Engineering')

    def test_symlink_escape_rejected(self):
        outside=self.base/'outside';outside.mkdir();link=self.root/'escape'
        try:link.symlink_to(outside,target_is_directory=True)
        except OSError:
            # Windows may require developer mode to create a symlink. Exercise the
            # same canonical-path guard without requiring that privilege.
            with patch.object(Path,'resolve',side_effect=[outside,self.root]):
                with self.assertRaisesRegex(ValueError,'junction leaves'):self.policy.validate(str(link))
        else:
            with self.assertRaisesRegex(ValueError,'junction leaves'):self.policy.validate(str(link))

    def test_retargeted_registration_and_removed_allowed_root(self):
        row=self.service.save(self.data)
        with patch.object(self.policy,'validate',return_value=(str(self.folder),str(self.root))):
            self.assertEqual(self.service.get(row['id'])['accessibility'],'inaccessible')
        other=self.base/'new-root';other.mkdir()
        changed=Sources(self.store,SourcePolicy([str(other)]))
        self.assertEqual(changed.get(row['id'])['accessibility'],'inaccessible')

    @unittest.skipUnless(os.name=='nt','Windows-specific path syntax')
    def test_windows_drive_unc_and_device_syntax(self):
        self.assertTrue(SourcePolicy.absolute(r'\\nas\engineering\project').is_absolute())
        for path in [r'C:relative',r'\relative',r'\\?\C:\data',r'\\.\C:\data',str(self.folder)+':stream']:
            with self.subTest(path=path),self.assertRaises(ValueError):SourcePolicy.absolute(path)


try:
    from fastapi import HTTPException
    from docagent.sources_api import authorized
except ModuleNotFoundError:
    authorized=None


@unittest.skipIf(authorized is None,'Run authorization tests in the app venv')
class SourceAuthorizationTests(unittest.TestCase):
    def test_explicit_demo_mode_bypasses_token_only(self):
        with patch.dict(os.environ,{'DOCAGENT_DEMO_MODE':'1'},clear=True):
            self.assertIsNone(authorized(''))
            with self.assertRaises(ValueError):SourcePolicy([])
    def test_fail_closed_and_authorized_admin(self):
        with patch.dict(os.environ,{},clear=True):
            with self.assertRaises(HTTPException) as error:authorized('anything')
            self.assertEqual(error.exception.status_code,503)
        with patch.dict(os.environ,{'DOCAGENT_SOURCES_ADMIN_TOKEN':'synthetic-admin-token'}):
            for token in ('','incorrect'):
                with self.assertRaises(HTTPException) as error:authorized(token)
                self.assertEqual(error.exception.status_code,403)
            self.assertIsNone(authorized('synthetic-admin-token'))
