from libraryos.operations import operation_contract


def test_source_acquire_contract_documents_identity_evidence_shape():
    schema = operation_contract("source.acquire")["arguments_schema"]
    identity = schema["properties"]["identity_evidence"]["anyOf"][0]

    assert identity["additionalProperties"] is False
    assert set(identity["properties"]) == {
        "candidate_id",
        "expected_sha256",
        "identifiers",
        "method",
        "title",
    }
