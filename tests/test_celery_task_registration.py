from grc_policy_server.worker import celery_app


def test_refresh_accuracy_report_task_is_registered():
    assert "grc_policy_server.tasks.refresh_accuracy_report" in celery_app.tasks

