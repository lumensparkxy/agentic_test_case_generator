import json
import unittest
from pathlib import Path

from scripts.deploy_firestore_indexes import IndexSpec, load_manifest, normalize_remote_index, normalize_remote_indexes


REPO_ROOT = Path(__file__).resolve().parents[2]


class FirestoreIndexManifestTests(unittest.TestCase):
    def test_firebase_config_targets_only_the_versioned_index_manifest(self) -> None:
        config = json.loads((REPO_ROOT / "firebase.json").read_text(encoding="utf-8"))

        self.assertEqual(config, {"firestore": {"indexes": "firestore.indexes.json"}})

    def test_manifest_matches_the_three_bounded_workspace_queries(self) -> None:
        indexes = load_manifest(REPO_ROOT / "firestore.indexes.json")

        self.assertEqual(
            indexes,
            [
                IndexSpec(
                    collection_group="qa_projects",
                    query_scope="COLLECTION",
                    fields=(
                        ("owner_user_id", "ASCENDING"),
                        ("status", "ASCENDING"),
                        ("updated_at", "DESCENDING"),
                        ("project_id", "ASCENDING"),
                    ),
                ),
                IndexSpec(
                    collection_group="qa_projects",
                    query_scope="COLLECTION",
                    fields=(
                        ("owner_user_id", "ASCENDING"),
                        ("updated_at", "DESCENDING"),
                        ("project_id", "ASCENDING"),
                    ),
                ),
                IndexSpec(
                    collection_group="execution_runs",
                    query_scope="COLLECTION",
                    fields=(
                        ("actor_user_id", "ASCENDING"),
                        ("project_id", "ASCENDING"),
                        ("created_at", "DESCENDING"),
                        ("run_record_id", "ASCENDING"),
                    ),
                ),
            ],
        )

    def test_gcloud_create_command_is_additive_and_preserves_field_order(self) -> None:
        index = load_manifest(REPO_ROOT / "firestore.indexes.json")[2]

        self.assertEqual(
            index.create_command(project_id="example-project", database="(default)"),
            [
                "gcloud",
                "firestore",
                "indexes",
                "composite",
                "create",
                "--project=example-project",
                "--database=(default)",
                "--collection-group=execution_runs",
                "--query-scope=collection",
                "--field-config=field-path=actor_user_id,order=ascending",
                "--field-config=field-path=project_id,order=ascending",
                "--field-config=field-path=created_at,order=descending",
                "--field-config=field-path=run_record_id,order=ascending",
                "--async",
                "--quiet",
            ],
        )

    def test_remote_index_normalization_ignores_implicit_document_name(self) -> None:
        index, state = normalize_remote_index(
            {
                "name": "projects/example/databases/(default)/collectionGroups/qa_projects/indexes/index-1",
                "queryScope": "COLLECTION",
                "state": "READY",
                "fields": [
                    {"fieldPath": "owner_user_id", "order": "ASCENDING"},
                    {"fieldPath": "updated_at", "order": "DESCENDING"},
                    {"fieldPath": "project_id", "order": "ASCENDING"},
                    {"fieldPath": "__name__", "order": "ASCENDING"},
                ],
            }
        )

        self.assertEqual(state, "READY")
        self.assertEqual(
            index.fields,
            (
                ("owner_user_id", "ASCENDING"),
                ("updated_at", "DESCENDING"),
                ("project_id", "ASCENDING"),
            ),
        )

    def test_remote_inventory_is_keyed_like_the_versioned_manifest(self) -> None:
        expected = load_manifest(REPO_ROOT / "firestore.indexes.json")[1]

        inventory, opaque_count = normalize_remote_indexes(
            [
                {
                    "name": "projects/example/databases/(default)/collectionGroups/qa_projects/indexes/index-1",
                    "queryScope": "COLLECTION",
                    "state": "READY",
                    "fields": [
                        {"fieldPath": "owner_user_id", "order": "ASCENDING"},
                        {"fieldPath": "updated_at", "order": "DESCENDING"},
                        {"fieldPath": "project_id", "order": "ASCENDING"},
                        {"fieldPath": "__name__", "order": "ASCENDING"},
                    ],
                }
            ]
        )

        self.assertEqual(inventory, {expected.key: "READY"})
        self.assertEqual(opaque_count, 0)

    def test_remote_inventory_preserves_unmanaged_array_vector_and_recursive_indexes(self) -> None:
        inventory, opaque_count = normalize_remote_indexes(
            [
                {
                    "name": "projects/example/databases/(default)/collectionGroups/tags/indexes/array-index",
                    "queryScope": "COLLECTION",
                    "state": "READY",
                    "fields": [
                        {"fieldPath": "tags", "arrayConfig": "CONTAINS"},
                        {"fieldPath": "created_at", "order": "DESCENDING"},
                    ],
                },
                {
                    "name": "projects/example/databases/(default)/collectionGroups/embeddings/indexes/vector-index",
                    "queryScope": "COLLECTION_GROUP",
                    "state": "READY",
                    "fields": [{"fieldPath": "embedding", "vectorConfig": {"dimension": 768, "flat": {}}}],
                },
                {
                    "name": "projects/example/databases/(default)/collectionGroups/tree/indexes/recursive-index",
                    "queryScope": "COLLECTION_RECURSIVE",
                    "state": "READY",
                    "fields": [
                        {"fieldPath": "owner", "order": "ASCENDING"},
                        {"fieldPath": "updated_at", "order": "DESCENDING"},
                    ],
                },
            ]
        )

        self.assertEqual(inventory, {})
        self.assertEqual(opaque_count, 3)


if __name__ == "__main__":
    unittest.main()
