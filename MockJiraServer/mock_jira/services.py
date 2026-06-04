# ============================================================
# mock_jira/services.py
# 
# Service Layer untuk MockJiraServer
# Berisi semua business logic untuk query dan format data
# Views hanya handle HTTP request/response
# ============================================================

from django.core.paginator import Paginator
from django.db.models import Min, Max
from .models import JiraMainTicket, JiraSubTicket


# ──────────────────────────────────────────────────────────────
# FORMATTERS - Convert model instance ke dict
# ──────────────────────────────────────────────────────────────

def format_sub_ticket(sub):
    """Format JiraSubTicket model instance ke API response format"""
    if sub.start_date and sub.due_date:
        cycle_time_days = (sub.due_date - sub.start_date).days
    else:
        cycle_time_days = None

    return {
        "id": sub.id,
        "key": sub.issue_key,
        "fields": {
            "parent": {"key": sub.parent_key.issue_key},
            "status": {"name": sub.status},
            "start_date": str(sub.start_date) if sub.start_date else None,
            "due_date": str(sub.due_date) if sub.due_date else None,
            "predefined_process": sub.predefined_process,
            "cycle_time_days": cycle_time_days,
        }
    }


def format_main_ticket(main, include_subtasks=False):
    """Format JiraMainTicket model instance ke API response format"""
    data = {
        "id": main.id,
        "key": main.issue_key,
        "fields": {
            "status": {"name": main.status},
            "created": str(main.created),
            "package": main.package,
            "process_required": main.process_required,
            "subtasks_count": main.sub_tickets.count(),
            "quantity": main.quantity,
        }
    }
    if include_subtasks:
        data["fields"]["subtasks"] = [
            format_sub_ticket(s) for s in main.sub_tickets.all()
        ]
    return data


# ──────────────────────────────────────────────────────────────
# SERVICE: Main Tickets
# ──────────────────────────────────────────────────────────────

def fetch_main_tickets(filters):
    """
    Fetch main tickets dengan filtering dan pagination
    
    Args:
        filters: dict dengan keys:
            - issue_keys: list[str] - filter by issue keys
            - status: str - filter by status
            - package: str - filter by package name (icontains)
            - start_after: str - filter created >= date
            - start_before: str - filter created <= date
            - page: int - page number (default 1)
            - page_size: int - items per page (default 500, max 500)
    
    Returns:
        dict dengan keys: total, page, page_size, total_pages, issues
    """
    # Build queryset dengan filters
    qs = JiraMainTicket.objects.all()

    if filters.get('issue_keys'):
        qs = qs.filter(issue_key__in=filters['issue_keys'])
    
    if filters.get('status'):
        qs = qs.filter(status=filters['status'])
    
    if filters.get('package'):
        qs = qs.filter(package__icontains=filters['package'])
    
    if filters.get('start_after'):
        qs = qs.filter(created__gte=filters['start_after'])
    
    if filters.get('start_before'):
        qs = qs.filter(created__lte=filters['start_before'])

    # Pagination
    page_size = min(int(filters.get('page_size', 500)), 500)
    paginator = Paginator(qs, page_size)
    page_obj = paginator.get_page(int(filters.get('page', 1)))

    return {
        'total': paginator.count,
        'page': page_obj.number,
        'page_size': page_size,
        'total_pages': paginator.num_pages,
        'issues': [format_main_ticket(m) for m in page_obj.object_list]
    }


def fetch_main_ticket_detail(issue_key):
    """
    Fetch detail main ticket dengan subtasks
    
    Args:
        issue_key: str - issue key untuk dicari
    
    Returns:
        dict: formatted main ticket dengan subtasks
    
    Raises:
        JiraMainTicket.DoesNotExist: jika tidak ditemukan
    """
    main = JiraMainTicket.objects.get(issue_key=issue_key)
    return format_main_ticket(main, include_subtasks=True)


# ──────────────────────────────────────────────────────────────
# SERVICE: Sub Tickets
# ──────────────────────────────────────────────────────────────

def fetch_sub_tickets(filters):
    """
    Fetch sub tickets dengan filtering dan pagination
    
    Args:
        filters: dict dengan keys:
            - status: str - filter by status
            - parent_key: str - filter by parent issue key
            - process: str - filter by process name (icontains)
            - due_after: str - filter due_date >= date
            - due_before: str - filter due_date <= date
            - page: int - page number (default 1)
            - page_size: int - items per page (default 500, max 500)
    
    Returns:
        dict dengan keys: total, page, page_size, total_pages, sub_issues
    """
    # Build queryset dengan filters
    qs = JiraSubTicket.objects.select_related('parent_key').all()

    # Filter status, parent, process
    if filters.get('status'):
        qs = qs.filter(status=filters['status'])
    
    if filters.get('parent_key'):
        qs = qs.filter(parent_key__issue_key=filters['parent_key'])
    
    if filters.get('process'):
        qs = qs.filter(predefined_process__icontains=filters['process'])

    # Filter utama: due_date (kapan proses selesai)
    if filters.get('due_after'):
        qs = qs.filter(due_date__gte=filters['due_after'])
    
    if filters.get('due_before'):
        qs = qs.filter(due_date__lte=filters['due_before'])

    # Pagination
    page_size = min(int(filters.get('page_size', 500)), 500)
    paginator = Paginator(qs, page_size)
    page_obj = paginator.get_page(int(filters.get('page', 1)))

    return {
        'total': paginator.count,
        'page': page_obj.number,
        'page_size': page_size,
        'total_pages': paginator.num_pages,
        'sub_issues': [format_sub_ticket(s) for s in page_obj.object_list]
    }


def fetch_sub_tickets_date_range():
    """
    Fetch earliest dan latest due_date untuk sub-tickets
    yang sudah completed
    
    Returns:
        dict dengan keys:
            - earliest_due_date: str | None
            - latest_due_date: str | None
            - total_completed: int
    """
    qs = JiraSubTicket.objects.filter(
        status='Completed',
        due_date__isnull=False,
    )
    
    agg = qs.aggregate(
        earliest=Min('due_date'),
        latest=Max('due_date'),
    )
    
    return {
        'earliest_due_date': str(agg['earliest']) if agg['earliest'] else None,
        'latest_due_date': str(agg['latest']) if agg['latest'] else None,
        'total_completed': qs.count(),
    }
