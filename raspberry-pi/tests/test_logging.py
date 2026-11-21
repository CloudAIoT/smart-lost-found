"""Pi 로그(INFO 포함)가 서비스 로그에 보이는지 확인한다."""

import logging

from app.main import create_app
from conftest import make_settings


def test_info_logs_are_shown(capsys):
    create_app(make_settings("locker"))
    logging.getLogger("pi").info("MQTT 연결됨: 100.73.97.89:1883")
    assert "INFO: MQTT 연결됨: 100.73.97.89:1883" in capsys.readouterr().err


def test_output_is_added_only_once(capsys):
    create_app(make_settings("locker"))
    create_app(make_settings("capture"))
    logging.getLogger("pi").warning("경고 TAMPER")
    assert capsys.readouterr().err.count("경고 TAMPER") == 1
