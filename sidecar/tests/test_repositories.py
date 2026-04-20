from __future__ import annotations

import json
import shutil
import tempfile
import unittest

from nsbot_sidecar.infrastructure.repositories import create_repositories
from nsbot_sidecar.infrastructure.storage import connect_database


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
            provider_data={
                "runtime_provider": "openai",
                "catalog_provider_id": "openai",
                "display_name": "OpenAI",
                "preferred_model_id": "gpt-5.4",
            },
            models=[],
        )

        session = self.repositories.sessions.create(
            workspace_id=workspace.id,
            active_provider_id=provider.provider.id,
            active_model_id="gpt-5.4",
        )

        event = self.repositories.acp_event_log.append(
            session_id=session.id,
            event_type="user_message_chunk",
            event_json=json.dumps(
                {"sessionUpdate": "user_message_chunk", "content": {"text": "Help me wire frontend and sidecar"}},
                ensure_ascii=False,
            ),
        )

        self.repositories.sessions.touch(
            session.id,
            message_count=1,
            last_message_preview="Help me wire frontend and sidecar",
            last_message_at=event.created_at,
            title="Wire frontend and sidecar",
            title_source="heuristic",
        )

        self.assertEqual(len(self.repositories.workspaces.list()), 1)
        self.assertEqual(len(self.repositories.providers.list_bundles()), 1)
        self.assertEqual(
            len(self.repositories.sessions.list_by_workspace_id(workspace.id)), 1
        )
        self.assertEqual(
            len(self.repositories.acp_event_log.list_by_session_id(session.id)), 1
        )
        self.assertTrue(provider.provider.secret_ref.startswith("sec_"))
        persisted_session = self.repositories.sessions.get_by_id(session.id)
        self.assertEqual(persisted_session.message_count, 1)
        self.assertEqual(
            persisted_session.last_message_preview,
            "Help me wire frontend and sidecar",
        )

    def test_session_list_survives_reopening_database(self) -> None:
        workspace = self.repositories.workspaces.create(
            name="nutstore-bot",
            path_label="C:/repo/nutstore-bot",
            real_path="C:/repo/nutstore-bot",
        )

        session = self.repositories.sessions.create(
            workspace_id=workspace.id,
            active_provider_id=None,
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

    def test_provider_fields_survive_reopening_database(self) -> None:
        provider = self.repositories.providers.save_bundle(
            provider_data={
                "runtime_provider": "openai",
                "catalog_provider_id": "openai",
                "display_name": "OpenAI",
                "preferred_model_id": "gpt-5.4",
            },
            models=[],
        )

        self.connection.close()
        reopened_connection = connect_database(self.temp_dir)
        self.addCleanup(reopened_connection.close)
        reopened = create_repositories(reopened_connection)

        reopened_provider = reopened.providers.get_bundle_by_id_or_raise(
            provider.provider.id
        )
        self.assertEqual(reopened_provider.provider.model_policy, "all_catalog")
        self.assertEqual(reopened_provider.provider.preferred_model_id, "gpt-5.4")
        self.assertEqual(reopened_provider.provider.api_key_configured, True)

    def test_default_model_selection_survives_reopening_database(self) -> None:
        self.repositories.default_model_selection.set("openai", "gpt-5.4")

        self.connection.close()
        reopened_connection = connect_database(self.temp_dir)
        self.addCleanup(reopened_connection.close)
        reopened = create_repositories(reopened_connection)

        selection = reopened.default_model_selection.get()
        self.assertIsNotNone(selection)
        assert selection is not None
        self.assertEqual(selection.provider_id, "openai")
        self.assertEqual(selection.model_id, "gpt-5.4")
