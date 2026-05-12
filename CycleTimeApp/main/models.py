from django.contrib.auth.models import AbstractUser
from django.db import models


# ======================
# USER
# ======================
class User(AbstractUser):

    class Role(models.TextChoices):
        ADMIN      = 'admin',      'Admin'
        STAFF      = 'staff',      'Staf Development'
        MANAGEMENT = 'management', 'Manajemen'

    role = models.CharField(
        max_length=20,
        choices=Role.choices,
        default=Role.STAFF
    )

    def __str__(self):
        return self.username


# ======================
# SYNC LOG
# ======================
class SyncLog(models.Model):
    admin       = models.ForeignKey(User, on_delete=models.SET_NULL, null=True)
    started_at  = models.DateTimeField()
    finished_at = models.DateTimeField(null=True, blank=True)

    total_fetched   = models.IntegerField(default=0)
    total_processed = models.IntegerField(default=0)
    total_skipped   = models.IntegerField(default=0)  # ticket yang di-skip karena sudah ada di DB
    total_errors    = models.IntegerField(default=0)  # khusus errors

    status = models.CharField(max_length=50)

    def __str__(self):
        return f"Sync {self.id} - {self.status}"


# ======================
# RAW TICKETS (DATA UTAMA)
# ======================
class RawTicket(models.Model):
    ticket_key = models.CharField(max_length=50, unique=True)
    parent_key = models.CharField(max_length=50, null=True, blank=True)

    platform   = models.CharField(max_length=50)
    summary    = models.TextField()

    assignee = models.CharField(max_length=100, null=True, blank=True)
    status   = models.CharField(max_length=50)

    start_date    = models.DateField(null=True, blank=True)
    resolved_date = models.DateField(null=True, blank=True)
    due_date      = models.DateField(null=True, blank=True)

    cycle_time = models.FloatField(null=True, blank=True)
    
    
    quantity           = models.IntegerField(null=True, blank=True)  # jumlah unit dari main ticket
    package_name       = models.CharField(max_length=100, null=True, blank=True)
    predefined_process = models.CharField(max_length=100, null=True, blank=True)

    sync_log = models.ForeignKey(SyncLog, on_delete=models.SET_NULL, null=True)

    def __str__(self):
        return self.ticket_key


# ======================
# FLAG HISTORY
# ======================
class FlagHistory(models.Model):
    ticket     = models.ForeignKey(RawTicket, on_delete=models.CASCADE)
    flagged_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True)

    flagged_at = models.DateTimeField(auto_now_add=True)
    action     = models.CharField(max_length=50)  # Flag / Resolve
    comment    = models.TextField(null=True, blank=True)

    def __str__(self):
        return f"{self.ticket.ticket_key} - {self.action}"


# ======================
# ERROR TICKETS
# ======================
class ErrorTicket(models.Model):
    ticket        = models.ForeignKey(RawTicket, on_delete=models.CASCADE)
    error_message = models.TextField()

    flagged_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True)
    flagged_at = models.DateTimeField(auto_now_add=True)

    raw_payload = models.JSONField(null=True, blank=True)

    def __str__(self):
        return f"Error - {self.ticket.ticket_key}"