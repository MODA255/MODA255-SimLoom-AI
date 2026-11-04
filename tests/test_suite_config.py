from orchestrator.suite_config import load_executable_suite


def test_suite_blocks_manual_chain_without_payload(tmp_path):
    p = tmp_path / 'suite.json'; p.write_text('{"chains":[{"chain_id":"manual_ecom","execution_mode":"manual"}]}')
    suite = load_executable_suite(p)
    assert suite['chains'][0]['blocked_reason'] == 'MANUAL_PAYLOAD_MISSING_FIELDS'
    assert 'master_asset_ids' in suite['chains'][0]['blocked_fields']


def test_suite_accepts_complete_manual_payload(tmp_path):
    p = tmp_path / 'suite.json'; p.write_text('{"chains":[{"chain_id":"manual_ecom","execution_mode":"manual","payload":{"recipe":"ecom_remix","master_asset_ids":["a"],"project_name":"x","num_versions":1,"bgm_mode":"none","auto_delivery_enabled":false}}]}')
    assert 'blocked_reason' not in load_executable_suite(p)['chains'][0]
