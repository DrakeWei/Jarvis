from __future__ import annotations

import os
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from unittest import TestCase, skipUnless

import psycopg
from psycopg import sql
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

import app.services.approval_service as approval_service
import app.services.lease_service as lease_service
from app.db.base import Base
from app.models import SessionRecord

TEST_POSTGRES_URL = os.getenv("JARVIS_TEST_POSTGRES_URL", "").strip()


@skipUnless(TEST_POSTGRES_URL, "Set JARVIS_TEST_POSTGRES_URL to run Postgres concurrency tests.")
class PostgresConcurrencyTests(TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        base_url = make_url(TEST_POSTGRES_URL)
        cls._database_name = f"jarvis_test_{uuid.uuid4().hex[:12]}"
        admin_url = base_url.set(database="postgres")
        database_url = base_url.set(database=cls._database_name)

        with psycopg.connect(admin_url.render_as_string(hide_password=False), autocommit=True) as conn:
            with conn.cursor() as cur:
                cur.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(cls._database_name)))

        cls.engine = create_engine(
            database_url.render_as_string(hide_password=False),
            future=True,
            pool_pre_ping=True,
        )
        cls.SessionLocal = sessionmaker(bind=cls.engine, autoflush=False, autocommit=False, future=True)
        Base.metadata.create_all(bind=cls.engine)

    @classmethod
    def tearDownClass(cls) -> None:
        try:
            cls.engine.dispose()
            base_url = make_url(TEST_POSTGRES_URL)
            admin_url = base_url.set(database="postgres")
            with psycopg.connect(admin_url.render_as_string(hide_password=False), autocommit=True) as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        SELECT pg_terminate_backend(pid)
                        FROM pg_stat_activity
                        WHERE datname = %s AND pid <> pg_backend_pid()
                        """,
                        (cls._database_name,),
                    )
                    cur.execute(sql.SQL("DROP DATABASE IF EXISTS {}").format(sql.Identifier(cls._database_name)))
        finally:
            super().tearDownClass()

    def setUp(self) -> None:
        super().setUp()
        with self._create_session() as db:
            db.query(SessionRecord).delete()
            db.add(
                SessionRecord(
                    id="session-1",
                    title="Session",
                    workspace_mode="bound",
                    canonical_workspace_path="/tmp/workspace",
                    workspace_fingerprint="workspace-fp",
                    workspace_label="workspace",
                    status="idle",
                )
            )
            db.commit()

    @contextmanager
    def _create_session(self):
        db = self.SessionLocal()
        try:
            yield db
        finally:
            db.close()

    def test_postgres_concurrent_lease_acquire_has_single_winner(self) -> None:
        barrier = threading.Barrier(2)

        def attempt(owner_id: str) -> bool:
            barrier.wait(timeout=5)
            return lease_service.try_acquire("turn", "postgres-concurrent-1", owner_id, ttl_seconds=60)

        with ThreadPoolExecutor(max_workers=2) as pool, patch.object(lease_service, "create_session", self._create_session):
            futures = [
                pool.submit(attempt, "owner-a"),
                pool.submit(attempt, "owner-b"),
            ]
            results = [future.result(timeout=5) for future in futures]
            leases = lease_service.list_leases(scope_type="turn", status="active")

        self.assertEqual(sum(1 for result in results if result), 1)
        active = next((lease for lease in leases if lease.scope_key == "postgres-concurrent-1"), None)
        self.assertIsNotNone(active)
        self.assertIn(active.owner_id, {"owner-a", "owner-b"})

    def test_postgres_concurrent_approval_decisions_allow_single_state_change(self) -> None:
        with patch.object(approval_service, "create_session", self._create_session):
            approval = approval_service.create_approval(
                session_id="session-1",
                approval_type="bash",
                prompt="bash\nls",
                context={"turn_id": 12},
            )
            barrier = threading.Barrier(2)

            def decide(approve: bool) -> tuple[str | None, bool]:
                barrier.wait(timeout=5)
                summary, changed = approval_service.apply_approval_decision(approval.id, approve, "feedback")
                return (summary.status if summary else None, changed)

            with ThreadPoolExecutor(max_workers=2) as pool:
                futures = [
                    pool.submit(decide, True),
                    pool.submit(decide, False),
                ]
                results = [future.result(timeout=5) for future in futures]
                resolved = approval_service.get_approval(approval.id)

        self.assertEqual(sum(1 for _status, changed in results if changed), 1)
        self.assertEqual(len({status for status, _changed in results}), 1)
        self.assertIn(resolved.status if resolved else None, {"approved", "rejected"})
