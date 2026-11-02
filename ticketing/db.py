import os

import psycopg
from psycopg.rows import dict_row


def connect(*, dbname=None, user=None, password=None, application_name="ticketing"):
    """自动提交连接；业务操作使用显式 transaction()，避免隐式长事务。"""
    return psycopg.connect(
        host=os.environ.get("PGHOST", "postgres"),
        port=int(os.environ.get("PGPORT", "5432")),
        dbname=dbname or os.environ.get("PGDATABASE", "ticketing"),
        user=user or os.environ.get("PGUSER", "ticketing_app"),
        password=password if password is not None else os.environ.get("PGPASSWORD"),
        autocommit=True,
        row_factory=dict_row,
        connect_timeout=10,
        application_name=application_name,
        options="-c timezone=UTC -c lock_timeout=5000 -c statement_timeout=30000",
    )
