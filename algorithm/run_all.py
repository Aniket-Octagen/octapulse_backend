#!/usr/bin/env python3
"""
Master Runner - All Trackers
============================
Single file to run ALL tracker generators at once.
All outputs go to a date-wise folder (e.g., 01-03-26).

Usage:
    python run_all.py
    python run_all.py <input_file>
    python run_all.py <input_file> <output_folder>
"""

import sys
import os
import time
from pathlib import Path
from datetime import datetime

# ============================================================================
# DEFAULT PATHS
# ============================================================================
DEFAULT_INPUT_FILE = r"2026.01.30_Borouge EU3 H2 Extraction Project PMS-Rev1 dates (1).xlsx"
DEFAULT_OUTPUT_FOLDER = "01-03-26"
# ============================================================================

# Import all processor classes
from ho_procurements import HOProcurementsProcessor
from commissioning_rfsu import CommissioningRFSUProcessor
from const_precomm import ConstPreCommProcessor
from ho_subcontract import HOSubcontractProcessor
from manufacture import ManufactureProcessor
from project_management import ProjectManagementProcessor
from main1 import EDDRProcessor
from eddr_cntr import EDDRCNTRProcessor
from weekly_eddr_cont import WeeklyEDDRContProcessor
from revised_bl_overall_progress import RevisedBLProgressProcessor
from bl_overall_progress_lv2 import BLProgressLv2Processor
from overall_s_curve import OverallSCurveProcessor
from pm_s_curve import PMSCurveProcessor


# All trackers: (Name, ProcessorClass, output_filename)
ALL_TRACKERS = [
    ("HO-Procurements",              HOProcurementsProcessor,      "ho_procurements_tracker.xlsx"),
    ("Commissioning RFSU",           CommissioningRFSUProcessor,   "commissioning_rfsu_tracker.xlsx"),
    ("Const & Pre-Comm",             ConstPreCommProcessor,        "const_precomm_tracker.xlsx"),
    ("HO-Subcontract",               HOSubcontractProcessor,       "ho_subcontract_tracker.xlsx"),
    ("Manufacture",                  ManufactureProcessor,         "manufacture_tracker.xlsx"),
    ("Project Management",           ProjectManagementProcessor,   "project_management_tracker.xlsx"),
    ("EDDR (Main1)",                 EDDRProcessor,                "p6_consideration_output_updated_v2.xlsx"),
    ("EDDR CNTR",                    EDDRCNTRProcessor,            "eddr_cntr_tracker.xlsx"),
    ("Weekly EDDR Cont.",            WeeklyEDDRContProcessor,      "weekly_eddr_cont_tracker.xlsx"),
    ("Revised BL Overall Progress",  RevisedBLProgressProcessor,   "revised_bl_overall_progress_tracker.xlsx"),
    ("BL Overall Progress Lv2",      BLProgressLv2Processor,       "bl_overall_progress_lv2_tracker.xlsx"),
    ("Overall S-Curve",              OverallSCurveProcessor,       "overall_s_curve_tracker.xlsx"),
    ("PM S-Curve",                   PMSCurveProcessor,            "pm_s_curve_tracker.xlsx"),
]


def print_banner():
    print()
    print("=" * 70)
    print("   MASTER RUNNER - ALL TRACKERS")
    print("   Generates all 13 tracker outputs in one go")
    print("=" * 70)
    print()


def run_all(input_file, output_folder):
    """Run all tracker processors and save outputs to the folder."""

    # Ensure output folder exists
    os.makedirs(output_folder, exist_ok=True)
    print(f"  Input File:     {input_file}")
    print(f"  Output Folder:  {output_folder}")
    print(f"  Total Trackers: {len(ALL_TRACKERS)}")
    print()
    print("-" * 70)

    success_count = 0
    fail_count = 0
    results = []
    total_start = time.time()

    for idx, (name, processor_class, output_filename) in enumerate(ALL_TRACKERS, 1):
        output_path = os.path.join(output_folder, output_filename)
        print(f"\n[{idx}/{len(ALL_TRACKERS)}] {name}")
        print(f"     -> {output_path}")
        
        start_time = time.time()
        try:
            processor = processor_class(input_file)
            processor.process(output_path)
            elapsed = time.time() - start_time
            status = "OK"
            success_count += 1
            print(f"     [OK] Done in {elapsed:.1f}s")
        except Exception as e:
            elapsed = time.time() - start_time
            status = f"FAIL: {str(e)}"
            fail_count += 1
            print(f"     [FAIL] {str(e)}")
        
        results.append((name, output_filename, status, elapsed))

    total_elapsed = time.time() - total_start

    # Final Summary
    print()
    print("=" * 70)
    print("   EXECUTION SUMMARY")
    print("=" * 70)
    print(f"{'#':<4} {'Tracker':<35} {'Status':<8} {'Time':>6}")
    print("-" * 70)
    
    for idx, (name, filename, status, elapsed) in enumerate(results, 1):
        st = "OK" if status == "OK" else "FAIL"
        color_st = st
        print(f"{idx:<4} {name:<35} {color_st:<8} {elapsed:>5.1f}s")
    
    print("-" * 70)
    print(f"Total:  {success_count} OK  |  {fail_count} FAILED  |  {total_elapsed:.1f}s")
    print(f"Output: {os.path.abspath(output_folder)}")
    print("=" * 70)
    
    if fail_count > 0:
        print("\nFailed trackers:")
        for name, filename, status, _ in results:
            if status != "OK":
                print(f"  - {name}: {status}")
    
    print()
    return fail_count == 0


def main():
    print_banner()

    # Parse arguments
    if len(sys.argv) >= 2:
        input_file = sys.argv[1]
        output_folder = sys.argv[2] if len(sys.argv) > 2 else DEFAULT_OUTPUT_FOLDER
    else:
        print(f"DEFAULT INPUT FILE:")
        print(f"  {DEFAULT_INPUT_FILE}")
        print()
        user_input = input("Press ENTER to use default, or type new path: ").strip()
        input_file = user_input.strip('"').strip("'") if user_input else DEFAULT_INPUT_FILE

        print()
        print(f"DEFAULT OUTPUT FOLDER:")
        print(f"  {DEFAULT_OUTPUT_FOLDER}")
        print()
        user_output = input("Press ENTER to use default, or type new folder name: ").strip()
        output_folder = user_output.strip('"').strip("'") if user_output else DEFAULT_OUTPUT_FOLDER
        print()

    # Validate input file
    if not Path(input_file).exists():
        print(f"ERROR: Input file not found: {input_file}")
        input("\nPress ENTER to exit...")
        sys.exit(1)

    print("-" * 70)
    success = run_all(input_file, output_folder)
    
    if len(sys.argv) < 2:
        input("\nPress ENTER to exit...")
    
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
