# ============================================================
# mock_jira/models.py
# ============================================================

from django.db import models


class JiraMainTicket(models.Model):
    """
    Main Ticket dari DEVSMETS/JIRA
    Status: selalu Closed
    """
    STATUS_CHOICES = [('Closed', 'Closed')]

    issue_key        = models.CharField(max_length=50, unique=True)
    status           = models.CharField(max_length=50, choices=STATUS_CHOICES, default='Closed')
    created          = models.DateField()
    package          = models.CharField(max_length=100)
    process_required = models.JSONField(default=list)   # list of process names
    quantity         = models.IntegerField(default=0)
    class Meta:
        db_table = 'jira_main_ticket'
        ordering = ['issue_key']

    def __str__(self):
        return self.issue_key


class JiraSubTicket(models.Model):
    """
    Sub Ticket dari DEVSMETS/JIRA
    - start_date selalu benar
    - due_date bisa sengaja salah (error injection → CT negatif)
    """
    STATUS_CHOICES = [('Completed', 'Completed'), ('In Progress', 'In Progress')]

    issue_key          = models.CharField(max_length=60, unique=True)
    parent_key         = models.ForeignKey(
                            JiraMainTicket,
                            to_field='issue_key',
                            on_delete=models.CASCADE,
                            db_column='parent_key',
                            related_name='sub_tickets'
                         )
    status             = models.CharField(max_length=50, choices=STATUS_CHOICES, default='Completed')
    start_date         = models.DateField(null=True, blank=True)
    due_date           = models.DateField(null=True, blank=True)
    predefined_process = models.CharField(max_length=100)

    class Meta:
        db_table = 'jira_sub_ticket'
        ordering = ['parent_key', 'issue_key']

    def __str__(self):
        return self.issue_key