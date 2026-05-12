# ============================================================
# main/utils.py
#
# Pure utility functions — tidak bergantung pada model atau
# external services. Aman di-import dari mana saja.
# ============================================================

from datetime import datetime


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
        delta = (end - start).days + 1
        return float(delta)
    except Exception:
        return None


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