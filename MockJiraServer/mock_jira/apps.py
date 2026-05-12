from django.apps import AppConfig

class MockJiraConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'mock_jira'