import json
import threading
import urllib.error
import urllib.request

import pytest

from libraryos import create_collection, create_work, initialize_library, put_collection
from libraryos.operations import OPERATIONS, describe_operations, invoke, operation_contract
from libraryos.server import create_server, openapi_document
from libraryos.storage import StorageError


def test_python_operation_envelope(tmp_path):
    root = tmp_path / "library"
    initialize_library(root)
    result = invoke("work.create", {
        "library": root,
        "work_type": "book",
        "title": "History",
    })
    assert result["operation"] == "libraryos.work.create"
    assert result["effects"] == {"mutation": True, "network": False}


def test_library_initialization_is_available_through_shared_operation(tmp_path):
    root = tmp_path / "library"

    result = invoke(
        "library.initialize",
        {"path": root, "title": "Shared operation library"},
        capabilities=["library.maintain"],
    )

    assert result["operation"] == "libraryos.library.initialize"
    assert result["effects"] == {"mutation": True, "network": False}
    assert result["result"]["path"] == str(root)
    status = invoke("library.status", {"library": root})
    assert status["result"]["descriptor"]["title"] == "Shared operation library"


def test_operation_contracts_describe_every_public_operation():
    catalog = describe_operations()

    assert list(catalog["operations"]) == sorted(OPERATIONS)
    create = operation_contract("work.create")
    assert create["arguments_schema"]["additionalProperties"] is False
    assert create["arguments_schema"]["required"] == ["library", "work_type", "title"]
    assert create["arguments_schema"]["properties"]["library"]["type"] == "string"
    assert create["arguments_schema"]["properties"]["identifiers"]["default"] is None
    assert create["effects"] == {"mutation": True, "network": False}
    assert create["required_capability"] == "library.maintain"
    assert create["description"] == "Create an empty authoritative work bundle atomically."
    relations = operation_contract("relations.openalex.discover")
    assert relations["effects"] == {"mutation": False, "network": True}
    assert relations["required_capability"] == "library.read"
    acquire = operation_contract("source.acquire")
    actor = acquire["arguments_schema"]["properties"]["requested_by"]
    assert actor["anyOf"][0]["properties"]["kind"]["enum"] == [
        "human",
        "agent",
        "service",
        "import",
    ]
    assert actor["anyOf"][0]["required"] == ["kind", "id"]


def test_operation_arguments_are_validated_before_dispatch(tmp_path):
    root = tmp_path / "library"
    initialize_library(root)

    with pytest.raises(StorageError) as missing:
        invoke("work.create", {"library": root, "title": "Missing type"})
    assert missing.value.code == "arguments_invalid"

    with pytest.raises(StorageError) as unknown:
        invoke(
            "work.create",
            {
                "library": root,
                "work_type": "book",
                "title": "Unknown argument",
                "typo": True,
            },
        )
    assert unknown.value.code == "arguments_invalid"

    with pytest.raises(StorageError) as wrong_type:
        invoke("search", {"library": root, "query": "x", "limit": "ten"})
    assert wrong_type.value.code == "arguments_invalid"
    assert wrong_type.value.path == "limit"


def test_openapi_has_one_typed_path_per_operation():
    document = openapi_document("127.0.0.1", 8765)

    assert set(document["paths"]) == {
        f"/v1/operations/{name}" for name in OPERATIONS
    }
    create = document["paths"]["/v1/operations/work.create"]["post"]
    schema = create["requestBody"]["content"]["application/json"]["schema"]
    assert create["operationId"] == "work_create"
    assert create["x-libraryos-required-capability"] == "library.maintain"
    assert schema["required"] == ["library", "work_type", "title"]
    assert create["responses"]["200"]["content"]["application/json"]["schema"][
        "properties"
    ]["effects"]["const"] == {"mutation": True, "network": False}


def test_capabilities_enforce_least_authority_and_collection_scoped_reads(tmp_path):
    root = tmp_path / "library"
    initialize_library(root)
    included = create_work(root, work_type="article", title="Included")
    excluded = create_work(root, work_type="article", title="Excluded")
    collection = create_collection(
        root,
        collection_id="project-a",
        title="Project A",
    )
    collection["members"] = [{"work": included["id"]}]
    put_collection(root, collection)
    grants = ["collection.read:project-a"]

    fetched = invoke(
        "collection.get",
        {"library": root, "collection_id": "project-a"},
        capabilities=grants,
    )
    assert fetched["result"]["collection"]["id"] == "project-a"
    shown = invoke(
        "work.show",
        {"library": root, "work_id": included["id"]},
        capabilities=grants,
    )
    assert shown["result"]["work"]["title"] == "Included"

    with pytest.raises(StorageError) as global_list:
        invoke("work.list", {"library": root}, capabilities=grants)
    assert global_list.value.code == "capability_denied"
    with pytest.raises(StorageError) as outside:
        invoke(
            "work.show",
            {"library": root, "work_id": excluded["id"]},
            capabilities=grants,
        )
    assert outside.value.code == "capability_denied"
    with pytest.raises(StorageError) as mutation:
        invoke(
            "collection.put",
            {"library": root, "record": collection},
            capabilities=grants,
        )
    assert mutation.value.code == "capability_denied"


def test_collection_write_implies_read_only_for_same_collection(tmp_path):
    root = tmp_path / "library"
    initialize_library(root)
    collection = create_collection(
        root,
        collection_id="project-a",
        title="Project A",
    )

    result = invoke(
        "collection.get",
        {"library": root, "collection_id": "project-a"},
        capabilities=["collection.write:project-a"],
    )
    assert result["result"]["collection"] == collection
    with pytest.raises(StorageError) as other:
        invoke(
            "collection.get",
            {"library": root, "collection_id": "project-b"},
            capabilities=["collection.write:project-a"],
        )
    assert other.value.code == "capability_denied"


def test_collection_membership_operation_is_collection_scoped(tmp_path):
    root = tmp_path / "library"
    initialize_library(root)
    work = create_work(root, work_type="article", title="A paper")
    create_collection(root, collection_id="project-a", title="Project A")
    create_collection(root, collection_id="project-b", title="Project B")

    result = invoke(
        "collection.membership.set",
        {
            "library": root,
            "collection_id": "project-a",
            "work_id": work["id"],
            "included": True,
        },
        capabilities=["collection.write:project-a"],
    )

    assert result["result"]["included"] is True
    assert result["result"]["changed"] is True
    with pytest.raises(StorageError) as denied:
        invoke(
            "collection.membership.set",
            {
                "library": root,
                "collection_id": "project-b",
                "work_id": work["id"],
                "included": True,
            },
            capabilities=["collection.write:project-a"],
        )
    assert denied.value.code == "capability_denied"


def test_local_http_uses_same_operation(tmp_path):
    root = tmp_path / "library"
    initialize_library(root)
    server, token = create_server()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    url = f"http://{host}:{port}/v1/operations/work.create"
    body = json.dumps({
        "library": str(root), "work_type": "standard", "title": "Local API",
    }).encode()
    try:
        unauthorized = urllib.request.Request(url, data=body, method="POST")
        with pytest.raises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(unauthorized)
        assert caught.value.code == 401

        request = urllib.request.Request(
            url, data=body, method="POST",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        )
        result = json.loads(urllib.request.urlopen(request).read())
        assert result["operation"] == "libraryos.work.create"
        assert result["result"]["work"]["title"] == "Local API"

        invalid = urllib.request.Request(
            url,
            data=json.dumps({"library": str(root), "title": "Missing type"}).encode(),
            method="POST",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
        )
        with pytest.raises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(invalid)
        assert caught.value.code == 400
        error = json.loads(caught.value.read())
        assert error["error"]["code"] == "arguments_invalid"
    finally:
        server.shutdown()
        server.server_close()


def test_nonlocal_bind_is_rejected():
    with pytest.raises(StorageError) as caught:
        create_server(host="0.0.0.0")
    assert caught.value.code == "nonlocal_bind_rejected"


def test_http_session_capabilities_are_enforced(tmp_path):
    root = tmp_path / "library"
    initialize_library(root)
    server, token = create_server(capabilities=["library.read"])
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    url = f"http://{host}:{port}/v1/operations/work.create"
    request = urllib.request.Request(
        url,
        data=json.dumps(
            {"library": str(root), "work_type": "book", "title": "Denied"}
        ).encode(),
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
    )
    try:
        with pytest.raises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(request)
        assert caught.value.code == 400
        payload = json.loads(caught.value.read())
        assert payload["error"]["code"] == "capability_denied"
    finally:
        server.shutdown()
        server.server_close()
