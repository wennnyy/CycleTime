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
#   - Rentang data          : April 2024 — Maret 2026
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

# Durasi tiap proses (min_hari, max_hari)
PROCESS_DURATION = {
    "Wafer Incoming Inspection":        (1, 2),
    "Wafer Backgrinding":               (1, 3),
    "Wafer Mounting":                   (1, 2),
    "Wafer Sawing":                     (1, 2),
    "Die Inspection":                   (1, 2),
    "Die Attach":                       (1, 3),
    "Die Attach Cure":                  (1, 2),
    "Wire Bond":                        (2, 5),
    "Wire Bond Inspection":             (1, 2),
    "Mold":                             (1, 4),
    "Mold Cure":                        (1, 2),
    "Dejunk & Trim":                    (1, 2),
    "Trim & Form":                      (1, 3),
    "Singulation":                      (1, 2),
    "Marking":                          (1, 2),
    "Marking Inspection":               (1, 2),
    "Plating":                          (2, 4),
    "Plating Inspection":               (1, 2),
    "Lead Finish":                      (1, 3),
    "Ball Mount (BGA)":                 (1, 3),
    "Ball Attach Reflow":               (1, 2),
    "Flux Cleaning":                    (1, 2),
    "X-Ray Inspection":                 (1, 3),
    "Visual Inspection":                (1, 3),
    "Automated Optical Inspection (AOI)":(1, 2),
    "Electrical Test (ET)":             (2, 4),
    "Final Test":                       (2, 6),
    "Burn-In Test":                     (2, 5),
    "High Temperature Storage (HTS)":   (2, 5),
    "Temperature Cycling Test":         (3, 7),
    "Moisture Sensitivity Test":        (2, 5),
    "Board Level Reliability Test":     (3, 7),
    "ESD Test":                         (1, 3),
    "Solderability Test":               (1, 3),
    "Package Integrity Test":           (1, 3),
    "Leak Test":                        (1, 2),
    "Tape & Reel":                      (1, 2),
    "Tray Packing":                     (1, 2),
    "Tube Packing":                     (1, 2),
    "Label & Barcode":                  (1, 1),
    "Outgoing Quality Control (OQC)":   (1, 2),
    "Dry Pack":                         (1, 2),
    "Humidity Indicator Check":         (1, 1),
    "Documentation Review":             (1, 2),
    "Shipment Preparation":             (1, 2),
}


# ── Konfigurasi Jumlah Ticket ─────────────────────────────────

MAIN_PER_MONTH_MIN = 25
MAIN_PER_MONTH_MAX = 35
SUB_PER_MONTH_MIN  = 350
SUB_PER_MONTH_MAX  = 450


# ── Tanggal hari ini sebagai batas status ─────────────────────
TODAY = date.today()


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
    """
    Generate daftar proses sejumlah num_processes.
    Proses inti diutamakan, sisanya random dari 45 proses.
    """
    # Pilih beberapa proses inti secara random
    num_core  = min(random.randint(3, 6), num_processes)
    core      = random.sample(CORE_PROCESSES, num_core)

    # Tambah dari proses optional sampai mencapai num_processes
    optional  = [p for p in ALL_PROCESSES if p not in core]
    random.shuffle(optional)

    all_proc  = core[:]
    for p in optional:
        if len(all_proc) >= num_processes:
            break
        all_proc.append(p)

    return all_proc[:num_processes]


# ── Helper: distribusi sub ticket ke main ticket ─────────────

def distribute_sub_counts(num_main, target_total):
    """
    Distribusikan target_total sub ticket ke num_main main ticket
    secara merata dengan sedikit variasi.
    """
    base      = target_total // num_main
    remainder = target_total  % num_main
    counts    = [base] * num_main

    for i in random.sample(range(num_main), remainder):
        counts[i] += 1

    # Variasi kecil supaya tidak monoton
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
        'April 2024 — Maret 2026'
    )

    def handle(self, *args, **options):

        # ── Hapus data lama ──────────────────────────────────
        self.stdout.write(self.style.WARNING('Menghapus data lama...'))
        JiraSubTicket.objects.all().delete()
        JiraMainTicket.objects.all().delete()
        self.stdout.write(self.style.SUCCESS('Data lama dihapus.'))

        # ── Rentang waktu: April 2024 — Maret 2026 ──────────
        months = []
        for m in range(4, 13):    # April - Desember 2024
            months.append((2024, m))
        for m in range(1, 13):    # Januari - Desember 2025
            months.append((2025, m))
        for m in range(1, 4):     # Januari - Maret 2026
            months.append((2026, m))

        main_batch     = []
        sub_batch      = []
        ticket_counter = 1

        self.stdout.write(self.style.WARNING('Generate data...'))
        self.stdout.write('')

        for (year, month) in months:

            num_main   = random.randint(MAIN_PER_MONTH_MIN, MAIN_PER_MONTH_MAX)
            target_sub = random.randint(SUB_PER_MONTH_MIN, SUB_PER_MONTH_MAX)
            sub_counts = distribute_sub_counts(num_main, target_sub)

            month_sub_count = 0

            for i in range(num_main):
                issue_key    = f"DEVSMETS-{str(ticket_counter).zfill(5)}"
                created_date = random_date_in_month(year, month)
                package      = random.choice(PACKAGES)
                num_sub      = sub_counts[i]
                process_list = generate_process_list(num_sub)

                # ── Main Ticket ── status selalu Closed
                main_obj = JiraMainTicket(
                    issue_key        = issue_key,
                    status           = "Closed",
                    created          = created_date,
                    package          = package,
                    process_required = process_list,
                )
                main_batch.append(main_obj)

                # ── Sub Tickets ──
                current_start = created_date + timedelta(days=random.randint(1, 3))

                for idx, process in enumerate(process_list):
                    sub_key        = f"{issue_key}-{str(idx + 1).zfill(2)}"
                    min_d, max_d   = PROCESS_DURATION.get(process, (1, 3))
                    duration       = random.randint(min_d, max_d)
                    calculated_due = current_start + timedelta(days=duration)

                    # Status & due_date berdasarkan tanggal hari ini
                    if calculated_due <= TODAY:
                        status     = "Completed"
                        start_date = current_start
                        due_date   = calculated_due
                    elif current_start <= TODAY:
                        status     = "In Progress"
                        start_date = current_start
                        due_date   = None
                    else:
                        status     = "In Progress"
                        start_date = None
                        due_date   = None

                    # Proses berikutnya mulai setelah ini selesai
                    current_start = calculated_due + timedelta(days=random.randint(0, 1))

                    sub_obj = JiraSubTicket(
                        issue_key          = sub_key,
                        parent_key         = main_obj,
                        status             = status,
                        start_date         = start_date,
                        due_date           = due_date,
                        predefined_process = process,
                    )
                    sub_batch.append(sub_obj)
                    month_sub_count += 1

                ticket_counter += 1

            self.stdout.write(
                f'  ✓ {year}-{str(month).zfill(2)} → '
                f'{num_main} main tickets, '
                f'{month_sub_count} sub tickets'
            )

        # ── Bulk Insert ke PostgreSQL ─────────────────────────
        self.stdout.write('')
        self.stdout.write(self.style.WARNING('Menyimpan ke PostgreSQL...'))

        JiraMainTicket.objects.bulk_create(main_batch, batch_size=500)
        self.stdout.write(self.style.SUCCESS(f'  ✓ {len(main_batch)} main tickets tersimpan'))

        # Ambil mapping issue_key → DB object untuk parent_key
        main_map = {m.issue_key: m for m in JiraMainTicket.objects.all()}
        for sub in sub_batch:
            sub.parent_key = main_map[sub.parent_key.issue_key]

        JiraSubTicket.objects.bulk_create(sub_batch, batch_size=1000)
        self.stdout.write(self.style.SUCCESS(f'  ✓ {len(sub_batch)} sub tickets tersimpan'))

        # ── Summary ──────────────────────────────────────────
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
        self.stdout.write(f'  Total Package        : {len(PACKAGES)} jenis')
        self.stdout.write(f'  Total Process        : {len(ALL_PROCESSES)} jenis')
        self.stdout.write(f'  Rentang Data         : April 2024 — Maret 2026')
        self.stdout.write('=' * 60)