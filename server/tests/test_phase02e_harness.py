"""Local checks for the isolated acceptance center, never Linux acceptance evidence."""
import importlib.util

import pytest
from fastapi.testclient import TestClient

from conftest import PASSWORD, ROOT


@pytest.mark.parametrize('mode,expected', [('',401), ('503',503), ('timeout',503)])
def test_isolated_fault_control(database,tmp_path,monkeypatch,mode,expected):
    spec=importlib.util.spec_from_file_location('mhm_phase02e_center',ROOT/'deploy/phase02e/center.py')
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    control=tmp_path/'fault-control'
    control.write_text(mode)
    monkeypatch.setenv('MHM_P2E_CONTROL',str(control))
    monkeypatch.setenv('MHM_ADMIN_PASSWORD',PASSWORD)
    application=module.create_center()
    # Use zero delay only in this harness unit check; real timeout verification
    # will use the unmodified 20-second gate with the actual Linux Agent.
    if mode=='timeout':
        original_sleep=module.asyncio.sleep
        async def no_delay(seconds):
            return await original_sleep(0)
        monkeypatch.setattr(module.asyncio,'sleep',no_delay)
    with TestClient(application) as client:
        assert client.get('/api/health/live').status_code==200
        assert client.get('/api/v1/agent/config?host_id=1').status_code==expected
