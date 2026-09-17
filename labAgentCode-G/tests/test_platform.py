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


def test_waiting_bridge_does_not_duplicate_evaluate_trace(lab):
    client, store = lab
    task = client.post('/api/tasks', json={
        'scenario': 'piv', 'objective': 'wait once', 'mode': 'bridge',
    }).json()
    for _ in range(8):
        store.tick()
    runs = store.read(task['id'])['node_runs']
    evaluate_runs = [run for run in runs if run['node'] == 'evaluate']
    assert len(evaluate_runs) == 1
    assert evaluate_runs[0]['status'] == 'waiting'


def test_validation_cancel_auth(lab,monkeypatch):
    client,store=lab
    assert client.post('/api/tasks',json={'scenario':'field_generation','objective':'test','parameters':{'angle_deg':90}}).status_code==422
    task=client.post('/api/tasks',json={'scenario':'piv','objective':'test'}).json()
    assert client.post('/api/tasks/'+task['id']+'/cancel').status_code==200
    store.tick();assert store.read(task['id'])['status']=='cancelled'
    monkeypatch.setenv('LAB_API_TOKEN','test-private-token')
    assert client.get('/api/tasks').status_code==401
    assert client.get('/api/tasks',headers={'Authorization':'Bearer test-private-token'}).status_code==200


def test_pause_resume_with_human_intervention_and_node_traces(lab):
    client, store = lab
    task = client.post('/api/tasks', json={'scenario': 'piv', 'objective': 'capture'}).json()

    paused = client.post('/api/tasks/' + task['id'] + '/pause').json()
    assert paused['control_state'] == 'paused'
    store.tick()
    assert store.read(task['id'])['status'] == 'queued'

    resumed = client.post(
        '/api/tasks/' + task['id'] + '/resume',
        json={'message': '先检查标定板，再开始拍摄'},
    ).json()
    assert resumed['control_state'] == 'running'
    assert resumed['interventions'][-1]['message'] == '先检查标定板，再开始拍摄'

    store.tick()
    updated = store.read(task['id'])
    assert updated['status'] == 'planned'
    assert updated['node_runs'][-1]['node'] == 'plan'
    assert updated['node_runs'][-1]['status'] == 'completed'
    assert updated['node_runs'][-1]['duration_ms'] >= 0


def test_stop_agent_prevents_future_graph_and_is_not_hardware_stop(lab):
    client, store = lab
    task = client.post('/api/tasks', json={'scenario': 'flow_control', 'objective': 'control'}).json()
    store.tick()

    stopped = client.post('/api/tasks/' + task['id'] + '/stop-agent').json()
    assert stopped['status'] == 'stopped'
    assert stopped['control_state'] == 'stopped'
    assert stopped['hardware_stopped'] is False
    store.tick()
    assert store.read(task['id'])['status'] == 'stopped'


def test_emergency_stop_creates_priority_gateway_operation(lab):
    client, store = lab
    response = client.post(
        '/api/emergency-stop',
        json={'target': 'all', 'reason': 'operator pressed emergency stop'},
    )
    assert response.status_code == 202
    command = response.json()
    assert command['tool'] == 'system.emergency_stop'
    assert command['priority'] == 'critical'
    assert command['status'] == 'queued'

    claimed = store.claim('device', 'gateway-1')
    assert claimed['id'] == command['id']
    assert claimed['parameters']['target'] == 'all'
    stops = client.get('/api/dashboard').json()['emergency_stops']
    assert stops[0]['id'] == command['id']
    assert stops[0]['status'] == 'running'


def test_dashboard_health_model_usage_and_logs(lab):
    client, store = lab
    store.heartbeat('device_gateway', {'port': '/dev/mock'})
    store.record_usage('openai', 'gpt-test', 120, 30, 0.0025, 410)

    settings = client.put('/api/settings/model', json={
        'provider': 'openai', 'model': 'gpt-test', 'temperature': 0.2, 'max_tokens': 2048,
    }).json()
    assert settings['model'] == 'gpt-test'

    dashboard = client.get('/api/dashboard').json()
    assert dashboard['components']['fastapi']['status'] == 'ok'
    assert dashboard['components']['database']['status'] == 'ok'
    assert dashboard['components']['device_gateway']['status'] == 'ok'
    assert dashboard['usage']['total_tokens'] == 150
    assert dashboard['usage']['estimated_cost_usd'] == 0.0025
    assert dashboard['model']['api_key_configured'] is False

    logs = client.get('/api/logs?limit=20').json()
    assert any(row['event'] == 'model.settings.updated' for row in logs)

    heartbeat = store.read('heartbeat:device_gateway')
    heartbeat['at'] = time.time() - 10
    store.save('heartbeat', heartbeat)
    stale = client.get('/api/dashboard').json()
    assert stale['components']['device_gateway']['status'] == 'stale'


def test_dashboard_ui_exposes_monitoring_and_separate_stop_controls(lab):
    client, _ = lab
    page = client.get('/').text
    for label in ('系统总览', '流程监控', '实验任务', '设备与急停', 'Agent 与模型', '日志与设置'):
        assert label in page
    assert '停止 Agent' in page
    assert '设备急停' in page
    assert '人工补充' in page


def test_unavailable_ollama_keeps_deterministic_plan_and_records_failure(lab, monkeypatch):
    client, store = lab
    client.put('/api/settings/model', json={
        'provider': 'ollama', 'model': 'qwen2.5:1.5b', 'temperature': 0.2, 'max_tokens': 128,
    })
    monkeypatch.setattr('labagent.platform.consult', lambda *args: (_ for _ in ()).throw(ConnectionError('offline')))

    task = client.post('/api/tasks', json={'scenario': 'piv', 'objective': 'capture'}).json()
    store.tick()

    updated = store.read(task['id'])
    assert updated['status'] == 'planned'
    assert updated['plan']['planner'] == 'deterministic'
    assert any(row['event'] == 'model.advice.failed' for row in store.logs(20))
