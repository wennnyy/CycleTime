# ============================================================
# main/process_groups.py
#
# Lookup grouping untuk:
#   1. Package  → Platform
#   2. Predefined Process → Process Stage, Process Area
# ============================================================


# ── Package → Platform ───────────────────────────────────────

PACKAGE_PLATFORM = {
    "SOT-23":          "SOT",
    "SOT-89":          "SOT",
    "SOT-223":         "SOT",
    "TO-252 (DPAK)":   "TO",
    "TO-263 (D2PAK)":  "TO",
    "SOP-8":           "SOP",
    "SOP-16":          "SOP",
    "TSOP-48":         "Other",
    "QFP-44":          "QFP",
    "QFP-64":          "QFP",
    "QFN-16":          "QFN",
    "QFN-32":          "QFN",
    "BGA-144":         "BGA",
}


# ── Predefined Process → Process Stage, Process Area ─────────
# Format: 'Predefined Process': ('Process Stage', 'Process Area')

PROCESS_GROUP = {
    # ── Pre Assembly — Wafer Preparation ─────────────────────
    "Wafer Incoming Inspection":          ("Pre Assembly", "Wafer Preparation"),
    "Wafer Backgrinding":                 ("Pre Assembly", "Wafer Preparation"),
    "Wafer Mounting":                     ("Pre Assembly", "Wafer Preparation"),
    "Wafer Sawing":                       ("Pre Assembly", "Wafer Preparation"),
    "Die Inspection":                     ("Pre Assembly", "Wafer Preparation"),

    # ── Assembly — Die Attach ─────────────────────────────────
    "Die Attach":                         ("Assembly", "Die Attach"),
    "Die Attach Cure":                    ("Assembly", "Die Attach"),

    # ── Assembly — Wire Bond ──────────────────────────────────
    "Wire Bond":                          ("Assembly", "Wire Bond"),
    "Wire Bond Inspection":               ("Assembly", "Wire Bond"),

    # ── Assembly — Molding ────────────────────────────────────
    "Mold":                               ("Assembly", "Molding"),
    "Mold Cure":                          ("Assembly", "Molding"),

    # ── Assembly — Trim & Form ────────────────────────────────
    "Dejunk & Trim":                      ("Assembly", "Trim & Form"),
    "Trim & Form":                        ("Assembly", "Trim & Form"),
    "Singulation":                        ("Assembly", "Trim & Form"),

    # ── Assembly — Marking ────────────────────────────────────
    "Marking":                            ("Assembly", "Marking"),
    "Marking Inspection":                 ("Assembly", "Marking"),

    # ── Assembly — Plating ────────────────────────────────────
    "Plating":                            ("Assembly", "Plating"),
    "Plating Inspection":                 ("Assembly", "Plating"),
    "Lead Finish":                        ("Assembly", "Plating"),

    # ── Assembly — Ball Mount ─────────────────────────────────
    "Ball Mount (BGA)":                   ("Assembly", "Ball Mount"),
    "Ball Attach Reflow":                 ("Assembly", "Ball Mount"),
    "Flux Cleaning":                      ("Assembly", "Ball Mount"),

    # ── Other — Quality Control ───────────────────────────────
    "X-Ray Inspection":                   ("Other", "Quality Control"),
    "Visual Inspection":                  ("Other", "Quality Control"),
    "Automated Optical Inspection (AOI)": ("Other", "Quality Control"),

    # ── Other — Testing ───────────────────────────────────────
    "Electrical Test (ET)":               ("Other", "Testing"),
    "Final Test":                         ("Other", "Testing"),
    "Burn-In Test":                       ("Other", "Testing"),

    # ── Other — Reliability Test ──────────────────────────────
    "High Temperature Storage (HTS)":     ("Other", "Reliability Test"),
    "Temperature Cycling Test":           ("Other", "Reliability Test"),
    "Moisture Sensitivity Test":          ("Other", "Reliability Test"),
    "Board Level Reliability Test":       ("Other", "Reliability Test"),
    "ESD Test":                           ("Other", "Reliability Test"),
    "Solderability Test":                 ("Other", "Reliability Test"),
    "Package Integrity Test":             ("Other", "Reliability Test"),
    "Leak Test":                          ("Other", "Reliability Test"),

    # ── Other — Packing ───────────────────────────────────────
    "Tape & Reel":                        ("Other", "Packing"),
    "Tray Packing":                       ("Other", "Packing"),
    "Tube Packing":                       ("Other", "Packing"),
    "Label & Barcode":                    ("Other", "Packing"),
    "Outgoing Quality Control (OQC)":     ("Other", "Packing"),
    "Dry Pack":                           ("Other", "Packing"),
    "Humidity Indicator Check":           ("Other", "Packing"),

    # ── Other — Shipment ─────────────────────────────────────
    "Documentation Review":               ("Other", "Shipment"),
    "Shipment Preparation":               ("Other", "Shipment"),
}


# ── Helper Functions ─────────────────────────────────────────

def get_platform(package):
    """Kembalikan Platform dari nama Package."""
    return PACKAGE_PLATFORM.get(package, "Other")


def get_process_info(predefined_process):
    """Kembalikan (Process Stage, Process Area) dari nama process."""
    return PROCESS_GROUP.get(predefined_process, ("—", "—"))


def enrich_ticket(ticket_dict):
    """
    Tambahkan platform_group, process_stage, dan process_area ke dict ticket.
    Gunakan ini di views.py saat menyiapkan data untuk template.

    Kolom yang ditambahkan:
        - platform_group : SOT / TO / SOP / QFP / QFN / BGA / Other
        - process_stage  : Pre Assembly / Assembly / Other
        - process_area   : Die Attach / Wire Bond / Testing / dll

    Strategi resolusi platform_group (berurutan, pakai yang pertama berhasil):
        1. package_name → lookup PACKAGE_PLATFORM (nama package asli, e.g. "SOT-23")
        2. platform     → langsung pakai jika sudah berupa group value yang valid
                          (e.g. "SOT", "BGA" — nilai yang disimpan saat sync ke DB)
        3. platform     → lookup PACKAGE_PLATFORM sebagai fallback terakhir
        4. Default "Other"

    Latar belakang bug:
        RawTicket.platform menyimpan hasil grouping ("SOT", "BGA", dst), BUKAN
        nama package asli ("SOT-23", "BGA-144"). Karena PACKAGE_PLATFORM dikunci
        berdasarkan nama package asli, `get_platform("SOT")` menghasilkan "Other".
        Fix: utamakan package_name untuk lookup; gunakan platform langsung jika
        sudah berupa group value yang valid.

    Contoh pemakaian di views.py:
        for t in page_obj.object_list:
            item = {
                'ticket_key':         t.ticket_key,
                'platform':           t.platform,      # group value dari DB: "SOT"
                'package_name':       t.package_name,  # nama asli: "SOT-23"
                'predefined_process': t.predefined_process,
                ...
            }
            item = enrich_ticket(item)
            data.append(item)

    Contoh pemakaian di template:
        {{ item.platform }}        -> SOT      (group value dari DB)
        {{ item.platform_group }}  -> SOT      (hasil enrich, sama)
        {{ item.process_stage }}   -> Assembly
        {{ item.process_area }}    -> Die Attach
    """
    # ── Semua group values yang valid ────────────────────────────────────────
    _VALID_GROUPS = set(PACKAGE_PLATFORM.values())  # {'SOT','TO','SOP','QFP','QFN','BGA','Other'}

    # ── Package → Platform group ─────────────────────────────────────────────
    # Prioritas 1: package_name (nama package asli → lookup dict)
    package_name = ticket_dict.get('package_name') or ''
    if package_name in PACKAGE_PLATFORM:
        platform_group = PACKAGE_PLATFORM[package_name]
    else:
        platform = ticket_dict.get('platform') or ''
        if platform in _VALID_GROUPS:
            # Prioritas 2: platform sudah berupa group value yang valid (hasil sync)
            platform_group = platform
        else:
            # Prioritas 3: coba platform sebagai nama package (legacy / edge case)
            platform_group = PACKAGE_PLATFORM.get(platform, 'Other')

    ticket_dict['platform_group'] = platform_group

    # ── Predefined Process → Stage & Area ────────────────────────────────────
    proc = ticket_dict.get('predefined_process') or ''
    stage, area = get_process_info(proc)
    ticket_dict['process_stage'] = stage
    ticket_dict['process_area']  = area

    return ticket_dict