# ============================================================
# main/utils.py
#
# Pure utility functions — tidak bergantung pada model atau
# external services. Aman di-import dari mana saja.
# ============================================================

from functools import wraps
from datetime  import datetime

from django.shortcuts import redirect
from django.http      import HttpResponseForbidden


MONTH_NAMES = {
    1: 'Januari',  2: 'Februari', 3: 'Maret',    4: 'April',
    5: 'Mei',      6: 'Juni',     7: 'Juli',      8: 'Agustus',
    9: 'September',10: 'Oktober', 11: 'November', 12: 'Desember',
}


# ======================================================
# AUTH
# ======================================================

def role_required(role_name):
    def decorator(view_func):
        @wraps(view_func)
        def wrapper(request, *args, **kwargs):
            if not request.user.is_authenticated:
                return redirect('login')
            if request.user.role != role_name:
                return HttpResponseForbidden("⛔ Anda tidak memiliki akses ke halaman ini")
            return view_func(request, *args, **kwargs)
        return wrapper
    return decorator


# ======================================================
# DATE
# ======================================================

def format_date_range(start_date, end_date):
    if not start_date or not end_date:
        return None
    try:
        return (
            f"{start_date.day} {MONTH_NAMES[start_date.month]} {start_date.year}"
            f" — {end_date.day} {MONTH_NAMES[end_date.month]} {end_date.year}"
        )
    except Exception:
        return None


def hitung_cycle_time(start_date_str, due_date_str):
    """
    CycleTime = (EndDate - StartDate) + 1
    +1 karena hari pertama (StartDate) dihitung sebagai hari kerja penuh.
    Contoh: start=2024-01-01, end=2024-01-01 -> CT = 1 hari
            start=2024-01-01, end=2024-01-03 -> CT = 3 hari
    Nilai negatif = data error (due < start).
    Return: float | None
    """
    if not start_date_str or not due_date_str:
        return None
    try:
        start = datetime.strptime(str(start_date_str), '%Y-%m-%d').date()
        end   = datetime.strptime(str(due_date_str),   '%Y-%m-%d').date()
        return float((end - start).days + 1)
    except Exception:
        return None


# ======================================================
# PAGINATION
# ======================================================

def get_page_range(current, total_pages, window=2):
    """
    Buat list nomor halaman dengan ellipsis untuk pagination.
    Contoh output: [1, '...', 4, 5, 6, '...', 16]
    Args:
        current     : halaman aktif saat ini
        total_pages : total halaman
        window      : jumlah halaman di kiri/kanan current
    Return: list[int | str]
    """
    pages = [1]
    start = max(2, current - window)
    end   = min(total_pages - 1, current + window)

    if start > 2:
        pages.append('...')
    for p in range(start, end + 1):
        pages.append(p)
    if end < total_pages - 1:
        pages.append('...')
    if total_pages > 1:
        pages.append(total_pages)

    return pages


# ======================================================
# MATH
# ======================================================

def median(values):
    """
    Median dari list of numbers.
    Return: float | None
    """
    if not values:
        return None
    s   = sorted(values)
    n   = len(s)
    mid = n // 2
    return (s[mid - 1] + s[mid]) / 2 if n % 2 == 0 else float(s[mid])


# ======================================================
# PDF UTILITIES
# ======================================================

def pdf_round_1(value):
    """Round to 1 decimal place, None → None."""
    return round(value, 1) if value is not None else None


def pdf_average(values):
    """Calculate average rounded to 1 decimal, empty list → None."""
    return pdf_round_1(sum(values) / len(values)) if values else None


def pdf_format_column_label(col_key, use_yearly):
    """Format column key untuk display di table header."""
    if use_yearly:
        return col_key
    try:
        year, month = col_key.split('-')
        names = ['Jan','Feb','Mar','Apr','May','Jun',
                 'Jul','Aug','Sep','Oct','Nov','Dec']
        return f"{names[int(month) - 1]} {year}"
    except Exception:
        return col_key


def pdf_value_to_string(value):
    """Convert value to string, None → '—'."""
    return str(value) if value is not None else '—'