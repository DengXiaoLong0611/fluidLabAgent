import time

import pytest
from fastapi.testclient import TestClient

from labagent.api import create_app
from labagent.platform import Platform


@pytest.fixture
def lab(tmp_path):
    app=create_app('sqlite:///'+str(tmp_path/'lab.db'),run_scheduler=False)
    with TestClient(app) as client:
        yield client, app.state.store


@pytest.mark.parametrize('scenario',['field_generation','piv','flow_control'])
def test_end_to_end(lab,scenario):
    client,store=lab
    r=client.post('/api/tasks',json={'scenario':scenario,'objective':'test'})
    assert r.status_code==201
    for _ in range(3): store.tick()
    task=client.get('/api/tasks/'+r.json()['id']).json()
    assert task['status']=='completed'
    assert task['result']['simulated'] is True
    assert len(client.get('/api/memories').json())==1


def test_claim_receipt_and_durable_restart(lab):
    client,store=lab
    task=client.post('/api/tasks',json={'scenario':'piv','objective':'capture','mode':'bridge'}).json()
    store.tick();store.tick()
    restarted=Platform(str(store.engine.url))
    op=restarted.claim('desktop','worker-a')
    assert op['task_id']==task['id']
    assert restarted.claim('desktop','worker-b') is None
    assert client.post('/api/operations/'+op['id']+'/complete',json={'receipt':'wrong','status':'completed','result':{}}).status_code==409
    body={'receipt':op['receipt'],'status':'completed','result':{'simulated':True}}
    assert client.post('/api/operations/'+op['id']+'/complete',json=body).status_code==200
    assert client.post('/api/operations/'+op['id']+'/complete',json=body).status_code==200
    restarted.tick()
    assert restarted.read(task['id'])['status']=='completed'


def test_timeout_never_reissues_motion(lab):
    client,store=lab
    task=client.post('/api/tasks',json={'scenario':'field_generation','objective':'test','mode':'bridge'}).json()
    store.tick();store.tick()
    op=store.claim('device','a');op['deadline']=time.time()-1;store.save('operation',op)
    store.tick()
    assert store.read(task['id'])['status']=='unknown'
    assert store.claim('device','b') is None


def test_validation_cancel_auth(lab,monkeypatch):
    client,store=lab
    assert client.post('/api/tasks',json={'scenario':'field_generation','objective':'test','parameters':{'angle_deg':90}}).status_code==422
    task=client.post('/api/tasks',json={'scenario':'piv','objective':'test'}).json()
    assert client.post('/api/tasks/'+task['id']+'/cancel').status_code==200
    store.tick();assert store.read(task['id'])['status']=='cancelled'
    monkeypatch.setenv('LAB_API_TOKEN','test-private-token')
    assert client.get('/api/tasks').status_code==401
    assert client.get('/api/tasks',headers={'Authorization':'Bearer test-private-token'}).status_code==200
