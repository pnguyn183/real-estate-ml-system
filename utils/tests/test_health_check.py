from unittest.mock import Mock

from scripts import health_check


def test_health_failure_has_nonzero_exit_and_checks_mongo_once(monkeypatch):
    http = Mock(return_value=True)
    mongo = Mock(return_value=False)
    kafka = Mock(return_value=True)
    monkeypatch.setattr(health_check, 'check_service_health', http)
    monkeypatch.setattr(health_check, 'check_mongodb', mongo)
    monkeypatch.setattr(health_check, 'check_kafka', kafka)
    assert health_check.main([]) == 1
    mongo.assert_called_once()
    kafka.assert_called_once()
    urls = [call.args[1] for call in http.call_args_list]
    assert 'http://localhost:8004/metrics' in urls
    assert 'http://localhost:8005/metrics' in urls


def test_readiness_is_explicit_and_checked(monkeypatch):
    http = Mock(side_effect=lambda name, url: not url.endswith('/ready'))
    monkeypatch.setattr(health_check, 'check_service_health', http)
    monkeypatch.setattr(health_check, 'check_mongodb', lambda: True)
    monkeypatch.setattr(health_check, 'check_kafka', lambda: True)
    assert health_check.main([]) == 0
    assert health_check.main(['--require-model']) == 1


def test_database_exception_does_not_log_credentials(monkeypatch, caplog):
    monkeypatch.setattr(health_check, 'MongoClient', Mock(side_effect=RuntimeError('private-connection-secret')))
    assert not health_check.check_mongodb()
    assert 'private-connection-secret' not in caplog.text
