import base64
import hashlib

from engineering_agent.artifact_transfer import ArtifactTransferStore


def test_chunk_upload_is_resumable_and_hash_checked(tmp_path):
    source = b"0123456789" * 700000
    digest = hashlib.sha256(source).hexdigest()
    store = ArtifactTransferStore(tmp_path / "worker")
    transfer = "a" * 32
    started = store.begin(transfer, [{"path": "inputs/model.step", "bytes": len(source), "sha256": digest}])
    assert started["status"] == "open"
    first = source[:1_000_000]
    second = source[1_000_000:]
    store.put_chunk(transfer, "inputs/model.step", 0, base64.b64encode(first).decode(), hashlib.sha256(first).hexdigest())
    retry = store.put_chunk(transfer, "inputs/model.step", 0, base64.b64encode(first).decode(), hashlib.sha256(first).hexdigest())
    assert retry["next_offset"] == len(first)
    offset = len(first)
    for start in range(0, len(second), 4 * 1024 * 1024):
        chunk = second[start:start + 4 * 1024 * 1024]
        store.put_chunk(transfer, "inputs/model.step", offset, base64.b64encode(chunk).decode(), hashlib.sha256(chunk).hexdigest())
        offset += len(chunk)
    completed = store.complete(transfer)
    assert completed["status"] == "complete"
    assert store.read_file(transfer, "inputs/model.step").read_bytes() == source


def test_chunk_upload_rejects_offset_and_hash_errors(tmp_path):
    data = b"model"
    store = ArtifactTransferStore(tmp_path / "worker")
    transfer = "b" * 32
    store.begin(transfer, [{"path": "model.step", "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}])
    try:
        store.put_chunk(transfer, "model.step", 1, base64.b64encode(data).decode(), hashlib.sha256(data).hexdigest())
    except Exception as exc:
        assert getattr(exc, "code", "") == "TRANSFER_OFFSET_MISMATCH"
    else:
        raise AssertionError("offset mismatch was accepted")
    try:
        store.put_chunk(transfer, "model.step", 0, base64.b64encode(data).decode(), "0" * 64)
    except Exception as exc:
        assert getattr(exc, "code", "") == "TRANSFER_CHUNK_HASH_MISMATCH"
    else:
        raise AssertionError("bad chunk hash was accepted")
