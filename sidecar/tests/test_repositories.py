from __future__ import annotations

import shutil
import tempfile
import unittest

from python_runtime.repositories import create_repositories
from python_runtime.storage import connect_database


class RepositoriesTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp(prefix="sidecar-repositories-")
        self.connection = connect_database(self.temp_dir)
        self.repositories = create_repositories(self.connection)

    def tearDown(self) -> None:
        self.connection.close()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_repositories_persist_records(self) -> None:
        workspace = self.repositories.workspaces.create(
            name="nutstore-bot",
            path_label="C:/repo/nutstore-bot",
            real_path="C:/repo/nutstore-bot",
        )

        provider = self.repositories.providers.save_bundle(
            connection_data={
                "kind": "builtin",
                "runtime_provider": "openai",
                "catalog_provider_id": "openai",
                "display_name": "OpenAI",
                "api_key_configured": True,
                "preferred_model_id": "gpt-5.4",
            },
            models=[
                {
                    "source": "catalog",
                    "model_id": "gpt-5.4",
                }
            ],
            headers=[
                {
                    "name": "X-Org",
                    "value_kind": "plain",
                    "plain_value": "team-a",
                }
            ],
        )

        session = self.repositories.sessions.create(
            workspace_id=workspace.id,
            active_connection_id=provider.connection.id,
            active_model_id="gpt-5.4",
        )

        message = self.repositories.messages.append(
            session_id=session.id,
            role="user",
            content="Help me wire frontend and sidecar",
        )

        self.repositories.sessions.touch(
            session.id,
            message_count=1,
            last_message_preview=message.content,
            last_message_at=message.created_at,
            title="Wire frontend and sidecar",
            title_source="heuristic",
        )

        run = self.repositories.runs.create(
            session_id=session.id,
            workspace_id=workspace.id,
            connection_id=provider.connection.id,
            model_id="gpt-5.4",
            input_text=message.content,
        )
        updated_run = self.repositories.runs.update(
            run.id,
            status="completed",
            final_answer="Done",
            completed_at=message.created_at,
        )

        self.assertEqual(len(self.repositories.workspaces.list()), 1)
        self.assertEqual(len(self.repositories.providers.list_bundles()), 1)
        self.assertEqual(
            len(self.repositories.sessions.list_by_workspace_id(workspace.id)), 1
        )
        self.assertEqual(
            len(self.repositories.messages.list_by_session_id(session.id)), 1
        )
        self.assertTrue(provider.connection.secret_ref.startswith("sec_"))
        self.assertEqual(updated_run.status, "completed")
        self.assertEqual(updated_run.final_answer, "Done")

    def test_session_list_survives_reopening_database(self) -> None:
        workspace = self.repositories.workspaces.create(
            name="nutstore-bot",
            path_label="C:/repo/nutstore-bot",
            real_path="C:/repo/nutstore-bot",
        )

        session = self.repositories.sessions.create(
            workspace_id=workspace.id,
            active_connection_id=None,
            active_model_id=None,
        )
        self.repositories.sessions.touch(
            session.id,
            title="Persisted session title",
            title_source="manual",
        )

        self.connection.close()
        reopened_connection = connect_database(self.temp_dir)
        self.addCleanup(reopened_connection.close)
        reopened = create_repositories(reopened_connection)

        sessions = reopened.sessions.list_by_workspace_id(workspace.id)
        self.assertEqual(len(sessions), 1)
        self.assertEqual(sessions[0].id, session.id)
        self.assertEqual(sessions[0].title, "Persisted session title")
        self.assertEqual(sessions[0].title_source, "manual")

    def test_provider_health_fields_survive_reopening_database(self) -> None:
        provider = self.repositories.providers.save_bundle(
            connection_data={
                "kind": "builtin",
                "runtime_provider": "openai",
                "catalog_provider_id": "openai",
                "display_name": "OpenAI",
                "api_key_configured": True,
                "health_status": "connected",
                "health_message": "Validation succeeded",
                "last_validated_at": "2026-03-24T12:00:00Z",
                "preferred_model_id": "gpt-5.4",
            },
            models=[{"source": "catalog", "model_id": "gpt-5.4"}],
        )

        self.connection.close()
        reopened_connection = connect_database(self.temp_dir)
        self.addCleanup(reopened_connection.close)
        reopened = create_repositories(reopened_connection)

        reopened_provider = reopened.providers.get_bundle_by_id_or_raise(
            provider.connection.id
        )
        self.assertEqual(reopened_provider.connection.health_status, "connected")
        self.assertEqual(
            reopened_provider.connection.health_message, "Validation succeeded"
        )
        self.assertEqual(
            reopened_provider.connection.last_validated_at, "2026-03-24T12:00:00Z"
        )
