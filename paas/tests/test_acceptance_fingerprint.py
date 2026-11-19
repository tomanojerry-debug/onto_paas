"""Bind release acceptance to authority and schema without freezing business rows."""
from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import unittest

from paas.acceptance import database_fingerprint


class AcceptanceFingerprintTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory(prefix='onto-acceptance-fingerprint-')
        self.addCleanup(folder.cleanup)
        self.database = Path(folder.name) / 'candidate.db'

    def test_generic_fingerprint_ignores_business_rows_and_binds_schema_and_ledger(self):
        build = {'tables': ['work'], 'model_hash': 'expected', 'migration': '001_initial.sql'}
        with closing(sqlite3.connect(self.database)) as connection:
            connection.executescript(
                "CREATE TABLE work (id INTEGER PRIMARY KEY);"
                "CREATE TABLE runtime_migrations (version TEXT PRIMARY KEY, model_hash TEXT);"
                "INSERT INTO runtime_migrations VALUES ('001', 'expected');"
            )
            initial = database_fingerprint(build, self.database)
            connection.execute('INSERT INTO work VALUES (1)')
            connection.commit()
            self.assertEqual(initial, database_fingerprint(build, self.database))
            connection.execute('ALTER TABLE work ADD COLUMN quantity INTEGER')
            self.assertNotEqual(initial, database_fingerprint(build, self.database))
            connection.execute("UPDATE runtime_migrations SET model_hash='other'")
            connection.commit()
            with self.assertRaisesRegex(ValueError, '迁移'):
                database_fingerprint(build, self.database)

    def test_contract_fingerprint_ignores_audit_logs_and_detects_changed_role_membership(self):
        build = {'runtime_kind': 'contract-reference-v9', 'tables': ['contract'], 'model_hash': 'expected'}
        root = Path(__file__).resolve().parents[1]
        schema = root / 'upstream/contract_reference/code-app-example/backend/schema.sql'
        with closing(sqlite3.connect(self.database)) as connection:
            connection.executescript(schema.read_text(encoding='utf-8'))
            connection.executescript(
                "CREATE TABLE onto_contract_release (model_hash TEXT NOT NULL);"
                "INSERT INTO onto_contract_release VALUES ('expected');"
                "INSERT INTO sys_user (username, password) VALUES ('candidate', 'fixture-hash');"
                "INSERT INTO sys_role (name, code) VALUES ('Approver', 'ROLE-APPROVE');"
                "INSERT INTO sys_user_role VALUES (1, 1);"
            )
            accepted = database_fingerprint(build, self.database)
            connection.execute("INSERT INTO audit_logs (username, action) VALUES ('candidate', 'LOGIN')")
            connection.commit()
            self.assertEqual(accepted, database_fingerprint(build, self.database))
            connection.execute("DELETE FROM sys_user_role WHERE user_id = 1")
            connection.commit()
            self.assertNotEqual(accepted, database_fingerprint(build, self.database))


if __name__ == '__main__':
    unittest.main()
