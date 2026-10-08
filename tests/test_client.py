"""Tests for federation node selection (include/exclude routing).

These cover the logic that lets the client infer which node(s) hold matching
data from the discovery responses (Beacon / clinical) and exclude the rest from
the subsequent, expensive DRS fanout calls.
"""

from unittest.mock import MagicMock, patch

from client import clinical_helpers, download_helpers, genomics_helpers


# ---------------------------------------------------------------------------
# has_data predicates
# ---------------------------------------------------------------------------


def test_beacon_node_has_data_true():
    node = {
        "location": {"name": "UHN"},
        "results": {"estimatedResults": {"PROG": [{"submitter_sample_id": "s1"}]}},
    }
    assert genomics_helpers.beacon_node_has_data(node) is True


def test_beacon_node_has_data_false_when_empty():
    assert genomics_helpers.beacon_node_has_data(
        {"location": {"name": "UHN"}, "results": {"estimatedResults": {}}}
    ) is False
    assert genomics_helpers.beacon_node_has_data(
        {"location": {"name": "UHN"}, "results": {"estimatedResults": {"PROG": []}}}
    ) is False
    assert genomics_helpers.beacon_node_has_data({"location": {"name": "UHN"}}) is False


def test_clinical_node_has_data_normal_run():
    node = {"location": {"name": "UHN"}, "results": {"data": {"donors": [{"id": "d1"}]}}}
    assert clinical_helpers.clinical_node_has_data(node) is True


def test_clinical_node_has_data_empty_category():
    node = {"location": {"name": "UHN"}, "results": {"data": {"donors": []}}}
    assert clinical_helpers.clinical_node_has_data(node) is False


def test_clinical_node_has_data_dry_run_summary():
    with_counts = {
        "location": {"name": "UHN"},
        "results": {"summary": {"record_counts": {"donors": 3}}},
    }
    zero_counts = {
        "location": {"name": "UHN"},
        "results": {"summary": {"record_counts": {"donors": 0}}},
    }
    assert clinical_helpers.clinical_node_has_data(with_counts) is True
    assert clinical_helpers.clinical_node_has_data(zero_counts) is False


# ---------------------------------------------------------------------------
# classify_federation_nodes
# ---------------------------------------------------------------------------


def test_classify_federation_nodes_splits_sets():
    results = [
        {"location": {"name": "UHN"}, "status": 200,
         "results": {"estimatedResults": {"P": [{"submitter_sample_id": "s"}]}}},
        {"location": {"name": "BCGSC"}, "status": 200,
         "results": {"estimatedResults": {}}},
        {"location": {"name": "C3G"}, "status": 500, "message": "failed"},
    ]
    contacted, with_data, errored = download_helpers.classify_federation_nodes(
        results, genomics_helpers.beacon_node_has_data
    )
    assert contacted == {"UHN", "BCGSC", "C3G"}
    assert with_data == {"UHN"}
    assert errored == {"C3G"}


def test_classify_ignores_unnamed_nodes():
    results = [{"status": 200, "results": {"estimatedResults": {}}}]
    contacted, with_data, errored = download_helpers.classify_federation_nodes(
        results, genomics_helpers.beacon_node_has_data
    )
    assert contacted == set()


def test_classify_handles_none():
    assert download_helpers.classify_federation_nodes(
        None, genomics_helpers.beacon_node_has_data
    ) == (set(), set(), set())


# ---------------------------------------------------------------------------
# compute_servers_to_exclude
# ---------------------------------------------------------------------------


def _beacon(name, has_data=False, errored=False):
    if errored:
        return {"location": {"name": name}, "status": 500, "message": "failed"}
    est = {"P": [{"submitter_sample_id": "s"}]} if has_data else {}
    return {"location": {"name": name}, "status": 200, "results": {"estimatedResults": est}}


def test_exclude_empty_and_errored_keep_data_node():
    results = [
        _beacon("UHN", has_data=True),
        _beacon("BCGSC", has_data=False),   # clean empty -> excluded
        _beacon("C3G", errored=True),       # errored -> excluded
    ]
    exclude, data_nodes = download_helpers.compute_servers_to_exclude(
        results, genomics_helpers.beacon_node_has_data
    )
    assert exclude == ["BCGSC", "C3G"]
    assert data_nodes == {"UHN"}


def test_exclude_errored_logs_warning(caplog):
    results = [_beacon("UHN", has_data=True), _beacon("C3G", errored=True)]
    with caplog.at_level("WARNING"):
        exclude, _ = download_helpers.compute_servers_to_exclude(
            results, genomics_helpers.beacon_node_has_data
        )
    assert exclude == ["C3G"]
    assert any("C3G" in rec.message and "error" in rec.message.lower()
               for rec in caplog.records)


def test_exclude_accumulates_existing():
    results = [_beacon("UHN", has_data=True), _beacon("BCGSC", has_data=False)]
    exclude, _ = download_helpers.compute_servers_to_exclude(
        results, genomics_helpers.beacon_node_has_data,
        existing_exclude=["OLD_NODE"],
    )
    assert exclude == ["BCGSC", "OLD_NODE"]


def test_protected_node_never_excluded():
    # UHN confirmed in an earlier (Beacon) step; now its clinical response is empty.
    clinical_results = [
        {"location": {"name": "UHN"}, "results": {"data": {"donors": []}}},
    ]
    exclude, data_nodes = download_helpers.compute_servers_to_exclude(
        clinical_results, clinical_helpers.clinical_node_has_data,
        protected_nodes={"UHN"},
    )
    assert "UHN" not in exclude
    assert exclude == []
    assert "UHN" in data_nodes


def test_protected_node_removed_from_existing_exclude():
    results = [_beacon("UHN", has_data=True)]
    exclude, _ = download_helpers.compute_servers_to_exclude(
        results, genomics_helpers.beacon_node_has_data,
        existing_exclude=["UHN"],  # wrongly excluded earlier
    )
    # UHN now returns data, so it must not remain excluded
    assert exclude == []


# ---------------------------------------------------------------------------
# execute_federation_call injection
# ---------------------------------------------------------------------------


def _mock_httpx_client(return_json):
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = return_json
    mock_response.raise_for_status.return_value = None
    mock_client = MagicMock()
    mock_client.post.return_value = mock_response
    mock_cm = MagicMock()
    mock_cm.__enter__.return_value = mock_client
    return mock_cm, mock_client


def test_execute_federation_call_injects_exclude_servers():
    payload = {"path": "beacon/v2/g_variants", "payload": {"gene_id": "SLX9"},
               "method": "POST", "service": "htsget"}
    mock_cm, mock_client = _mock_httpx_client([])
    with patch("client.download_helpers.httpx.Client", return_value=mock_cm):
        download_helpers.execute_federation_call(
            "http://fed/fanout", {}, payload, exclude_servers=["BCGSC", "C3G"]
        )
    sent = mock_client.post.call_args.kwargs["json"]
    assert sent["payload"]["exclude_servers"] == "BCGSC|C3G"
    # original inner-payload content is preserved
    assert sent["payload"]["gene_id"] == "SLX9"


def test_execute_federation_call_no_injection_when_empty():
    payload = {"path": "p", "payload": {"program_id": "X"}, "method": "GET", "service": "drs"}
    mock_cm, mock_client = _mock_httpx_client([])
    with patch("client.download_helpers.httpx.Client", return_value=mock_cm):
        download_helpers.execute_federation_call("http://fed/fanout", {}, payload)
    sent = mock_client.post.call_args.kwargs["json"]
    assert "exclude_servers" not in sent["payload"]
