import logging

from grc_policy_server.core.logging import log_runtime_environment


def test_log_runtime_environment_logs_each_variable(caplog):
    with caplog.at_level(logging.INFO):
        log_runtime_environment(
            {
                "API_BEARER_TOKEN": "dummy-token",
                "PORT": 8500,
            },
            logger_name="tests.runtime_env",
        )

    assert "runtime env API_BEARER_TOKEN=dummy-token" in caplog.text
    assert "runtime env PORT=8500" in caplog.text
