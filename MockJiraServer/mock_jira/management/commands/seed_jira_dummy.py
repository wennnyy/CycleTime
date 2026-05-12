# ============================================================
# mock_jira/management/commands/seed_jira_dummy.py
#
# Cara pakai:
#   python manage.py seed_jira_dummy
#
# Konfigurasi:
#   - Main ticket per bulan : 25 - 35 ticket
#   - Sub ticket per bulan  : 350 - 450 ticket
#   - Status main ticket    : Closed
#   - Status sub ticket     : Completed / In Progress (sesuai tanggal)
#   - Package               : 13 jenis
#   - Predefined Process    : 45 jenis
#   - Rentang data          : Januari 2024 — Desember 2026
#
# Konfigurasi Error Injection:
#   - Total error           : 5 error
#   - Bulan error           : Januari, Februari, Maret 2025 SAJA
#   - Distribusi            : acak oleh sistem (minimal 1 per bulan)
#   - Tipe A : due_date SEBELUM start_date, bulan SAMA
#              cth: start 9 Jan 2025 -> due 5 Jan 2025  (CT = -4 hari)
#   - Tipe B : due_date di bulan SEBELUMNYA
#              cth: start 9 Feb 2025 -> due 10 Jan 2025 (CT = -30 hari)
# ============================================================

import random
from datetime import date, timedelta
from django.core.management.base import BaseCommand
from mock_jira.models import JiraMainTicket, JiraSubTicket


# ── 13 Jenis Package ─────────────────────────────────────────

PACKAGES = [
    "SOT-23",
    "SOT-89",
    "SOT-223",
    "TO-252 (DPAK)",
    "TO-263 (D2PAK)",
    "SOP-8",
    "SOP-16",
    "TSOP-48",
    "QFP-44",
    "QFP-64",
    "QFN-16",
    "QFN-32",
    "BGA-144",
]


# ── 45 Predefined Process ────────────────────────────────────

ALL_PROCESSES = [
    "Wafer Incoming Inspection",
    "Wafer Backgrinding",
    "Wafer Mounting",
    "Wafer Sawing",
    "Die Inspection",
    "Die Attach",
    "Die Attach Cure",
    "Wire Bond",
    "Wire Bond Inspection",
    "Mold",
    "Mold Cure",
    "Dejunk & Trim",
    "Trim & Form",
    "Singulation",
    "Marking",
    "Marking Inspection",
    "Plating",
    "Plating Inspection",
    "Lead Finish",
    "Ball Mount (BGA)",
    "Ball Attach Reflow",
    "Flux Cleaning",
    "X-Ray Inspection",
    "Visual Inspection",
    "Automated Optical Inspection (AOI)",
    "Electrical Test (ET)",
    "Final Test",
    "Burn-In Test",
    "High Temperature Storage (HTS)",
    "Temperature Cycling Test",
    "Moisture Sensitivity Test",
    "Board Level Reliability Test",
    "ESD Test",
    "Solderability Test",
    "Package Integrity Test",
    "Leak Test",
    "Tape & Reel",
    "Tray Packing",
    "Tube Packing",
    "Label & Barcode",
    "Outgoing Quality Control (OQC)",
    "Dry Pack",
    "Humidity Indicator Check",
    "Documentation Review",
    "Shipment Preparation",
]

# Proses inti yang hampir selalu ada di setiap ticket
CORE_PROCESSES = [
    "Die Attach",
    "Wire Bond",
    "Mold",
    "Trim & Form",
    "Marking",
    "Final Test",
    "Visual Inspection",
]

# ── Durasi tiap proses (min_hari, max_hari) ──────────────────

PROCESS_DURATION = {
    "Wafer Incoming Inspection":          (1, 3),
    "Wafer Backgrinding":                 (1, 3),
    "Wafer Mounting":                     (1, 3),
    "Wafer Sawing":                       (1, 3),
    "Die Inspection":                     (1, 3),
    "Die Attach":                         (1, 3),
    "Die Attach Cure":                    (1, 3),
    "Wire Bond":                          (1, 5),
    "Wire Bond Inspection":               (1, 3),
    "Mold":                               (1, 3),
    "Mold Cure":                          (1, 3),
    "Dejunk & Trim":                      (1, 3),
    "Trim & Form":                        (1, 3),
    "Singulation":                        (1, 3),
    "Marking":                            (1, 3),
    "Marking Inspection":                 (1, 3),
    "Plating":                            (1, 5),
    "Plating Inspection":                 (1, 3),
    "Lead Finish":                        (1, 3),
    "Ball Mount (BGA)":                   (1, 3),
    "Ball Attach Reflow":                 (1, 3),
    "Flux Cleaning":                      (1, 3),
    "X-Ray Inspection":                   (1, 3),
    "Visual Inspection":                  (1, 3),
    "Automated Optical Inspection (AOI)": (1, 3),
    "Electrical Test (ET)":               (1, 5),
    "Final Test":                         (1, 7),
    "Burn-In Test":                       (1, 10),
    "High Temperature Storage (HTS)":     (1, 10),
    "Temperature Cycling Test":           (1, 10),
    "Moisture Sensitivity Test":          (1, 7),
    "Board Level Reliability Test":       (1, 10),
    "ESD Test":                           (1, 3),
    "Solderability Test":                 (1, 3),
    "Package Integrity Test":             (1, 3),
    "Leak Test":                          (1, 3),
    "Tape & Reel":                        (1, 3),
    "Tray Packing":                       (1, 3),
    "Tube Packing":                       (1, 3),
    "Label & Barcode":                    (1, 1),
    "Outgoing Quality Control (OQC)":     (1, 3),
    "Dry Pack":                           (1, 3),
    "Humidity Indicator Check":           (1, 1),
    "Documentation Review":               (1, 3),
    "Shipment Preparation":               (1, 3),
}


# ── Konfigurasi Jumlah Ticket ─────────────────────────────────

MAIN_PER_MONTH_MIN = 25
MAIN_PER_MONTH_MAX = 35
SUB_PER_MONTH_MIN  = 350
SUB_PER_MONTH_MAX  = 450

# ── Konfigurasi Error Injection ───────────────────────────────
# Total 5 error dibagi acak ke 3 bulan (Jan, Feb, Mar 2025)
# Minimal 1 error per bulan yang terpilih
# Bulan lain = 0 error
ERROR_MONTHS = {
    (2025, 1): 0,   # akan diisi oleh distribute_errors()
    (2025, 2): 0,
    (2025, 3): 0,
}
ERROR_TOTAL = 5

# ── Konfigurasi Quantity per Order ────────────────────────────
QTY_MIN = 55
QTY_MAX = 1000

# ── Tanggal hari ini sebagai batas status ─────────────────────
TODAY = date.today()


# ── Helper: distribusi error ke bulan terpilih ───────────────

def distribute_errors(months_dict, total):
    """
    Bagikan sejumlah `total` error secara acak ke bulan-bulan
    yang ada di months_dict. Setiap bulan minimal dapat 1.

    Contoh output untuk total=5, 3 bulan:
        {(2025,1): 2, (2025,2): 2, (2025,3): 1}
    atau
        {(2025,1): 1, (2025,2): 2, (2025,3): 2}
    """
    keys   = list(months_dict.keys())
    result = {k: 1 for k in keys}  # minimal 1 per bulan
    sisa   = total - len(keys)      # sisa setelah bagi rata minimal

    # Bagikan sisa secara acak
    for _ in range(sisa):
        pilihan = random.choice(keys)
        result[pilihan] += 1

    return result


# ── Helper: durasi dengan bias kuat ke 1 hari ────────────────

def random_duration(min_d, max_d):
    """
    Distribusi berbobot:
      1 hari  : bobot 7  (~70%)
      2 hari  : bobot 2  (~20%)
      3+ hari : bobot 1 masing-masing (~10% dibagi sisa range)
    """
    if min_d == max_d:
        return min_d
    pool = []
    for day in range(min_d, max_d + 1):
        if day == 1:
            pool.extend([1] * 7)
        elif day == 2:
            pool.extend([2] * 2)
        else:
            pool.append(day)
    return random.choice(pool)


# ── Helper: inject error tanggal ─────────────────────────────

def inject_date_error(start_date, due_date):
    """
    Kembalikan (start_date, due_date_salah).
    start_date selalu benar — hanya due_date yang salah.

    Tipe A — due_date mundur 2-10 hari, masih bulan yang sama:
        start : 9 Jan 2025 -> due : 5 Jan 2025  (CT = -4 hari)

    Tipe B — due_date mundur 30-45 hari (pindah bulan):
        start : 9 Feb 2025 -> due : 10 Jan 2025 (CT = -30 hari)
    """
    error_type = random.choice(['A', 'B'])

    if error_type == 'A':
        mundur  = random.randint(2, 10)
        bad_due = start_date - timedelta(days=mundur)
        if bad_due.month == start_date.month and bad_due.year == start_date.year:
            return start_date, bad_due
        error_type = 'B'

    mundur  = random.randint(30, 45)
    bad_due = start_date - timedelta(days=mundur)
    return start_date, bad_due


# ── Helper: tanggal random dalam 1 bulan ─────────────────────

def random_date_in_month(year, month):
    if month == 12:
        next_month = date(year + 1, 1, 1)
    else:
        next_month = date(year, month + 1, 1)
    first_day = date(year, month, 1)
    delta     = (next_month - first_day).days
    return first_day + timedelta(days=random.randint(0, delta - 1))


# ── Helper: generate daftar proses untuk 1 main ticket ───────

def generate_process_list(num_processes):
    num_core = min(random.randint(3, 6), num_processes)
    core     = random.sample(CORE_PROCESSES, num_core)
    optional = [p for p in ALL_PROCESSES if p not in core]
    random.shuffle(optional)
    all_proc = core[:]
    for p in optional:
        if len(all_proc) >= num_processes:
            break
        all_proc.append(p)
    return all_proc[:num_processes]


# ── Helper: distribusi sub ticket ke main ticket ─────────────

def distribute_sub_counts(num_main, target_total):
    base      = target_total // num_main
    remainder = target_total  % num_main
    counts    = [base] * num_main
    for i in random.sample(range(num_main), remainder):
        counts[i] += 1
    max_proc = len(ALL_PROCESSES)
    for _ in range(num_main * 2):
        i = random.randint(0, num_main - 1)
        j = random.randint(0, num_main - 1)
        if i != j and counts[i] > 3 and counts[j] < max_proc:
            counts[i] -= 1
            counts[j] += 1
    return counts


# ── Main Command ──────────────────────────────────────────────

class Command(BaseCommand):
    help = (
        'Generate dummy JIRA data: '
        '25-35 main ticket/bulan, 350-450 sub ticket/bulan, '
        '13 package, 45 predefined process, '
        'Januari 2024 - Desember 2026. '
        'Durasi mayoritas 1 hari, max 10 hari. '
        'Total 5 error hanya di Jan-Mar 2025.'
    )

    def handle(self, *args, **options):

        # ── Hitung distribusi error sebelum loop ─────────────
        error_distribution = distribute_errors(ERROR_MONTHS, ERROR_TOTAL)
        self.stdout.write(self.style.WARNING('Distribusi error yang akan di-inject:'))
        for (y, m), jumlah in error_distribution.items():
            self.stdout.write(
                f'  {y}-{str(m).zfill(2)} → {jumlah} error'
            )
        self.stdout.write('')

        # ── Hapus data lama ──────────────────────────────────
        self.stdout.write(self.style.WARNING('Menghapus data lama...'))
        JiraSubTicket.objects.all().delete()
        JiraMainTicket.objects.all().delete()
        self.stdout.write(self.style.SUCCESS('Data lama dihapus.'))

        # ── Rentang waktu: Januari 2024 - Desember 2026 ──────
        months = []
        for m in range(1, 13):
            months.append((2024, m))
        for m in range(1, 13):
            months.append((2025, m))
        for m in range(1, 13):
            months.append((2026, m))

        main_batch           = []
        sub_batch            = []
        ticket_counter       = 1
        total_error_injected = 0

        self.stdout.write(self.style.WARNING('Generate data...'))
        self.stdout.write('')

        for (year, month) in months:

            num_main   = random.randint(MAIN_PER_MONTH_MIN, MAIN_PER_MONTH_MAX)
            target_sub = random.randint(SUB_PER_MONTH_MIN, SUB_PER_MONTH_MAX)
            sub_counts = distribute_sub_counts(num_main, target_sub)

            month_sub_count   = 0
            completed_indices = []

            # ── Pass 1: Generate semua ticket bulan ini ───────
            for i in range(num_main):
                issue_key    = f"DEVSMETS-{str(ticket_counter).zfill(5)}"
                created_date = random_date_in_month(year, month)
                package      = random.choice(PACKAGES)
                num_sub      = sub_counts[i]
                process_list = generate_process_list(num_sub)

                main_obj = JiraMainTicket(
                    issue_key        = issue_key,
                    status           = "Closed",
                    created          = created_date,
                    package          = package,
                    process_required = process_list,
                    quantity         = random.randint(QTY_MIN, QTY_MAX),
                )
                main_batch.append(main_obj)

                current_start = created_date + timedelta(days=random.randint(1, 3))

                for idx, process in enumerate(process_list):
                    sub_key      = f"{issue_key}-{str(idx + 1).zfill(2)}"
                    min_d, max_d = PROCESS_DURATION.get(process, (1, 3))
                    duration     = random_duration(min_d, max_d)
                    calc_due     = current_start + timedelta(days=duration)

                    if calc_due <= TODAY:
                        status     = "Completed"
                        start_date = current_start
                        due_date   = calc_due
                        completed_indices.append(len(sub_batch))
                    elif current_start <= TODAY:
                        status     = "In Progress"
                        start_date = current_start
                        due_date   = None
                    else:
                        status     = "In Progress"
                        start_date = None
                        due_date   = None

                    current_start = calc_due + timedelta(days=random.randint(0, 1))

                    sub_batch.append(JiraSubTicket(
                        issue_key          = sub_key,
                        parent_key         = main_obj,
                        status             = status,
                        start_date         = start_date,
                        due_date           = due_date,
                        predefined_process = process,
                    ))
                    month_sub_count += 1

                ticket_counter += 1

            # ── Pass 2: Inject error hanya di bulan terpilih ──
            # Bulan selain Jan-Mar 2025 → error_quota = 0
            error_quota  = error_distribution.get((year, month), 0)
            actual_quota = min(error_quota, len(completed_indices))

            if actual_quota > 0:
                chosen = random.sample(completed_indices, actual_quota)
                for ci in chosen:
                    sub            = sub_batch[ci]
                    sd, bad_due    = inject_date_error(sub.start_date, sub.due_date)
                    sub.start_date = sd
                    sub.due_date   = bad_due

            total_error_injected += actual_quota

            self.stdout.write(
                f'  ✓ {year}-{str(month).zfill(2)} -> '
                f'{num_main} main tickets, '
                f'{month_sub_count} sub tickets, '
                f'{actual_quota} error injected'
            )

        # ── Bulk Insert ke PostgreSQL ─────────────────────────
        self.stdout.write('')
        self.stdout.write(self.style.WARNING('Menyimpan ke PostgreSQL...'))

        JiraMainTicket.objects.bulk_create(main_batch, batch_size=500)
        self.stdout.write(self.style.SUCCESS(
            f'  ✓ {len(main_batch)} main tickets tersimpan'
        ))

        main_map = {m.issue_key: m for m in JiraMainTicket.objects.all()}
        for sub in sub_batch:
            sub.parent_key = main_map[sub.parent_key.issue_key]

        JiraSubTicket.objects.bulk_create(sub_batch, batch_size=1000)
        self.stdout.write(self.style.SUCCESS(
            f'  ✓ {len(sub_batch)} sub tickets tersimpan'
        ))

        # ── Summary ───────────────────────────────────────────
        total_main      = JiraMainTicket.objects.count()
        total_sub       = JiraSubTicket.objects.count()
        total_completed = JiraSubTicket.objects.filter(status='Completed').count()
        total_progress  = JiraSubTicket.objects.filter(status='In Progress').count()
        avg_main        = total_main / len(months)
        avg_sub         = total_sub  / len(months)

        self.stdout.write('')
        self.stdout.write('=' * 60)
        self.stdout.write(self.style.SUCCESS('SELESAI!'))
        self.stdout.write(f'  Total Main Tickets   : {total_main} (rata-rata {avg_main:.0f}/bulan)')
        self.stdout.write(f'  Total Sub Tickets    : {total_sub} (rata-rata {avg_sub:.0f}/bulan)')
        self.stdout.write(f'  Sub - Completed      : {total_completed}')
        self.stdout.write(f'  Sub - In Progress    : {total_progress}')
        self.stdout.write(f'  Error Injected       : {total_error_injected} total')
        self.stdout.write(f'  Bulan Error          : Jan, Feb, Mar 2025 saja')
        self.stdout.write(f'  Distribusi Error     : {dict(error_distribution)}')
        self.stdout.write(f'    Tipe A : due < start, bulan sama  -> CT negatif kecil')
        self.stdout.write(f'    Tipe B : due di bulan sebelumnya  -> CT negatif besar')
        self.stdout.write(f'  Total Package        : {len(PACKAGES)} jenis')
        self.stdout.write(f'  Total Process        : {len(ALL_PROCESSES)} jenis')
        self.stdout.write(f'  Rentang Data         : Januari 2024 - Desember 2026')
        self.stdout.write('=' * 60)