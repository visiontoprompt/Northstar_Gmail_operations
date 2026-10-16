import pytest
from sqlalchemy.dialects import postgresql

from app import service


class _CapturedQuery(Exception):
    pass


class _QueryCapture:
    statement = ""

    def scalar(self, statement):
        self.statement = str(statement.compile(dialect=postgresql.dialect()))
        raise _CapturedQuery


def test_archive_lock_targets_only_account_row_for_postgres():
    db = _QueryCapture()

    with pytest.raises(_CapturedQuery):
        service.archive_account(db, "org-test", "operator-test", "acct-test", "test@example.com")

    assert "LEFT OUTER JOIN browser_profiles" in db.statement
    assert "FOR UPDATE OF gmail_accounts" in db.statement
