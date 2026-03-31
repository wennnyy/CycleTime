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
    Tambahkan platform, process_stage, dan process_area ke dict ticket.
    Gunakan ini di views.py saat menyiapkan data untuk template.

    Kolom yang ditambahkan:
        - platform       : SOT / TO / SOP / QFP / QFN / BGA / Other
        - process_stage  : Pre Assembly / Assembly / Other
        - process_area   : Die Attach / Wire Bond / Testing / dll

    Contoh pemakaian di views.py:
        for t in page_obj.object_list:
            item = {
                'ticket_key':         t.ticket_key,
                'platform':           t.platform,        # nama package asli
                'predefined_process': t.predefined_process,
                ...
            }
            item = enrich_ticket(item)
            data.append(item)

    Contoh pemakaian di template:
        {{ item.platform }}        -> SOT-23  (nama package asli)
        {{ item.platform_group }}  -> SOT     (hasil grouping)
        {{ item.process_stage }}   -> Assembly
        {{ item.process_area }}    -> Die Attach
    """
    # Package → Platform group
    package = ticket_dict.get('platform', '')
    ticket_dict['platform_group'] = get_platform(package)

    # Predefined Process → Stage & Area
    proc = ticket_dict.get('predefined_process', '')
    stage, area = get_process_info(proc)
    ticket_dict['process_stage'] = stage
    ticket_dict['process_area']  = area

    return ticket_dict