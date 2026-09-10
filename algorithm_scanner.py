"""
Dynamic Algorithm Scanner
=========================
Automatically scans the algorithm folder and registers available processors.
This allows new algorithms to be added without modifying code.

Author: PMO Team
Date: 2026-02-18
"""

import os
import sys
import importlib
import importlib.util
import inspect
from pathlib import Path


class AlgorithmScanner:
    """Scans and registers available processors from the algorithm folder."""

    # Keep all algorithm files in the directory, but only these are active for processing.
    ACTIVE_ALGORITHM_STEMS = {
        'project_management',
        'main1',
        'ho_procurements',
        'ho_subcontract',
        'ho_as_builts',
        'manufacture',
        'const_precomm',
        'commissioning_rfsu',
        'newalgo',
        'schedule_feb_update',
    }
    
    def __init__(self, algorithm_folder='algorithm'):
        """
        Initialize the algorithm scanner.
        
        Args:
            algorithm_folder: Path to the algorithm folder
        """
        self.algorithm_folder = Path(algorithm_folder)
        self.available_algorithms = {}
        
    def scan_algorithms(self):
        """
        Scan the algorithm folder and detect all processor classes.
        
        Returns:
            dict: Available algorithms with their metadata
        """
        if not self.algorithm_folder.exists():
            raise FileNotFoundError(f"Algorithm folder not found: {self.algorithm_folder}")
        
        print(f"[SCANNER] Scanning algorithm folder: {self.algorithm_folder}")
        
        # Get all Python files in the algorithm folder
        python_files = list(self.algorithm_folder.glob('*.py'))
        print(f"[SCANNER] Found {len(python_files)} algorithm file(s)")
        
        algorithms = {}
        
        for py_file in python_files:
            # Skip __init__.py and utility files without Processor classes
            if py_file.stem.startswith('_') or py_file.stem.startswith('extract_dates'):
                continue
            if py_file.stem not in self.ACTIVE_ALGORITHM_STEMS:
                continue
            try:
                # Use spec_from_file_location to support filenames with spaces
                safe_stem = py_file.stem.replace(' ', '_')
                module_name = f"algorithm.{safe_stem}"
                if module_name not in sys.modules:
                    spec = importlib.util.spec_from_file_location(module_name, py_file)
                    module = importlib.util.module_from_spec(spec)
                    sys.modules[module_name] = module
                    spec.loader.exec_module(module)
                else:
                    module = sys.modules[module_name]
                
                # Look for Processor classes in the module
                for name, obj in inspect.getmembers(module, inspect.isclass):
                    # Check if it's a processor class (ends with 'Processor')
                    if name.endswith('Processor') and obj.__module__ == module_name:
                        # Extract metadata from the class
                        metadata = self._extract_processor_metadata(obj, py_file.stem)
                        
                        algo_key = safe_stem.lower()
                        algorithms[algo_key] = {
                            'id': algo_key,
                            'name': metadata['name'],
                            'description': metadata['description'],
                            'processor_class': obj,
                            'class_name': name,
                            'file': py_file.name,
                            'module': module_name,
                            'detection_config': metadata.get('detection_config', {})
                        }
                        
                        print(f"[SCANNER] OK Registered: {name} from {py_file.name}")
                        break  # one Processor class per file
                        
            except Exception as e:
                print(f"[SCANNER] ERROR loading {py_file.name}: {str(e)}")
                continue
        
        self.available_algorithms = algorithms
        print(f"[SCANNER] Total algorithms registered: {len(algorithms)}")
        
        return algorithms
    
    def _extract_processor_metadata(self, processor_class, file_stem):
        """
        Extract metadata from a processor class.
        
        Args:
            processor_class: The processor class object
            file_stem: The file name stem (e.g., 'App', 'App2')
            
        Returns:
            dict: Processor metadata
        """
        # Get the class docstring
        docstring = inspect.getdoc(processor_class) or ""
        
        # Extract name from class name (e.g., EDDRProcessor -> EDDR)
        class_name = processor_class.__name__
        if class_name.endswith('Processor'):
            name = class_name[:-9]  # Remove 'Processor' suffix
        else:
            name = class_name
        
        # Try to get description from docstring or use default
        description = docstring.split('\n')[0] if docstring else f"{name} Processor"
        
        # Map known processors to their detection configs (keyed by lowercased safe stem).
        # detection_columns: column indices that MUST have data in detection_row.
        # Values are derived from each algorithm's actual COL_* and DATA_START_ROW constants.
        detection_configs = {
            # main1.py  COL_ACTIVITY_CODE=4, COL_ACTIVITY_NAME=10, COL_STAGE_GATE=23, data from row 12
            'main1': {
                'detection_columns': [4, 10, 23],
                'detection_row': 12,
                'output_suffix': 'EDDR_Timeline'
            },
            # project_management.py  COL_ACTIVITY_CODE=4, COL_ACTIVITY_NAME=6, COL_STAGE_GATE=11, data row 10
            'project_management': {
                'detection_columns': [4, 6, 11],
                'detection_row': 10,
                'output_suffix': 'Project_Management_Timeline'
            },
            # weekly_eddr_cont.py  COL_DISCIPLINE=6, COL_DELIVERABLES=7, DATA_START_ROW=4
            'weekly_eddr_cont': {
                'detection_columns': [6, 7],
                'detection_row': 4,
                'output_suffix': 'Weekly_EDDR_Cont'
            },
            # ho_subcontract.py  COL_ACTIVITY_CODE=2, COL_ACTIVITY_NAME=3, COL_STAGE_GATE=7, data row 8
            'ho_subcontract': {
                'detection_columns': [2, 3, 7],
                'detection_row': 8,
                'output_suffix': 'HO_Subcontract'
            },
            # ho_as_builts.py uses the same structural layout as HO subcontract
            'ho_as_builts': {
                'detection_columns': [2, 3, 7],
                'detection_row': 8,
                'output_suffix': 'HO_As_Builts'
            },
            # ho_procurements.py  COL_ACTIVITY_NAME=3, COL_ACTIVITY_CODE=4, COL_STAGE_GATE=9, DATA_START_ROW=12
            'ho_procurements': {
                'detection_columns': [3, 4, 9],   # FIXED: was [2,3,7] row 8
                'detection_row': 12,
                'output_suffix': 'HO_Procurements'
            },
            # manufacture.py  COL_LEVEL=2, COL_CODE_NAME=3, COL_ACTIVITY_NAME=4, COL_STAGE_GATE=8, DATA_START_ROW=6
            'manufacture': {
                'detection_columns': [2, 3, 8],   # FIXED: was [3,4,7] row 8
                'detection_row': 6,
                'output_suffix': 'Manufacture'
            },
            # commissioning_rfsu.py  COL_LEVEL=2, COL_ACTIVITY_NAME=3, COL_STAGE_GATE=7, DATA_START_ROW=6
            'commissioning_rfsu': {
                'detection_columns': [2, 3, 7],   # FIXED: was [3,4,7] row 8
                'detection_row': 6,
                'output_suffix': 'Commissioning_RFSU'
            },
            # const_precomm.py  COL_LEVEL=2, COL_CODE_NAME=3, COL_STAGE_GATE=13, DATA_START_ROW=6
            'const_precomm': {
                'detection_columns': [2, 3, 13],  # FIXED: was [3,4,7] (col 13 is stage gate)
                'detection_row': 6,
                'output_suffix': 'Const_PreComm'
            },
            # eddr_cntr.py  COL_SN=1, COL_DISCIPLINE=2, COL_TOTAL_DOCS=3, DATA_START_ROW=7
            'eddr_cntr': {
                'detection_columns': [1, 2, 3],   # FIXED: was copy of EDDR [4,10,23] row 12
                'detection_row': 7,
                'output_suffix': 'EDDR_CNTR'
            },
            # overall_s_curve.py  COL_LEVEL=2, COL_WBS_CODE=3, COL_WBS_NAME=5, SUMMARY_DATA_START=3
            'overall_s_curve': {
                'detection_columns': [2, 3, 5],   # FIXED: added col 5 (WBS name)
                'detection_row': 3,               # FIXED: was 5; data starts at row 3
                'output_suffix': 'Overall_SCurve'
            },
            # pm_s_curve.py  monthly series start ROW_PERIOD_LABELS=41; col 2 has period labels
            'pm_s_curve': {
                'detection_columns': [2, 3],
                'detection_row': 41,              # FIXED: was 5; monthly labels are at row 41
                'output_suffix': 'PM_SCurve'
            },
            # bl_overall_progress_lv2.py  COL_SN=3, COL_DISCIPLINE=4, COL_PLANNED=19, DATA_START_ROW=9
            'bl_overall_progress_lv2': {
                'detection_columns': [3, 4, 19],  # FIXED: added col 19 (Planned %)
                'detection_row': 9,               # FIXED: was 8; data starts at 9
                'output_suffix': 'BL_Overall_Progress_Lv2'
            },
            # revised_bl_overall_progress.py  COL_SN=2, COL_DISCIPLINE=3, Cumulative col=9, DATA_START_ROW=9
            'revised_bl_overall_progress': {
                'detection_columns': [2, 3, 9],   # FIXED: was [3,4] row 8
                'detection_row': 9,
                'output_suffix': 'Revised_BL_Overall_Progress'
            },
            # newalgo.py — template-driven EP/LP/Actual tracker for any sheet name
            'newalgo': {
                'detection_columns': [1, 2],
                'detection_row': 1,
                'expected_headers': [
                    'Activity ID', 'Activity Name',
                    'Early Planned Start', 'Early Planed Finish', 'Planned End Date',
                    'Late Planned Start', 'Late Planned Finish', 'Stage Gate',
                    'Start [Actual]', 'Finish [Actual]', 'Actual Date'
                ],
                'output_suffix': 'Timeline_Deviation_Tracker'
            },
            # schedule_feb_update.py — Sheet2 EP/LP/Actual (Start/Finish) layout
            'schedule_feb_update': {
                'detection_columns': [1, 2, 4, 5, 6],
                'detection_row': 1,
                'expected_headers': ['Activity ID', 'Activity Name', 'Start [Actual]', 'Early Planned Start', 'Late Start'],
                'output_suffix': 'Schedule_Feb_Update_Tracker'
            },
        }
        # Normalize the lookup key the same way as the scanner
        lookup_key = file_stem.lower().replace(' ', '_')
        
        return {
            'name': name,
            'description': description,
            'detection_config': detection_configs.get(lookup_key, {})
        }
    
    def get_algorithm_list(self):
        """
        Get list of available algorithms for API response.
        
        Returns:
            list: List of algorithm info dicts
        """
        if not self.available_algorithms:
            self.scan_algorithms()
        
        return [
            {
                'id': algo_id,
                'name': info['name'],
                'description': info['description'],
                'class_name': info['class_name'],
                'file': info['file']
            }
            for algo_id, info in self.available_algorithms.items()
        ]
    
    def get_processor_class(self, algorithm_id):
        """
        Get the processor class for a specific algorithm.
        
        Args:
            algorithm_id: The algorithm identifier (e.g., 'app', 'app2')
            
        Returns:
            class: The processor class
        """
        if not self.available_algorithms:
            self.scan_algorithms()
        
        if algorithm_id not in self.available_algorithms:
            raise ValueError(f"Algorithm '{algorithm_id}' not found")
        
        return self.available_algorithms[algorithm_id]['processor_class']


# Global scanner instance
_scanner = None

def get_scanner():
    """Get the global algorithm scanner instance."""
    global _scanner
    if _scanner is None:
        _scanner = AlgorithmScanner()
        _scanner.scan_algorithms()
    return _scanner


def get_available_algorithms():
    """Get list of available algorithms."""
    scanner = get_scanner()
    return scanner.get_algorithm_list()


def get_processor_by_id(algorithm_id):
    """Get processor class by algorithm ID."""
    scanner = get_scanner()
    return scanner.get_processor_class(algorithm_id)


# For backwards compatibility
def refresh_algorithms():
    """Force refresh of algorithm list."""
    global _scanner
    _scanner = AlgorithmScanner()
    _scanner.scan_algorithms()
    return _scanner.get_algorithm_list()


if __name__ == "__main__":
    # Test the scanner
    print("="*60)
    print("ALGORITHM SCANNER TEST")
    print("="*60)
    
    algorithms = get_available_algorithms()
    
    print(f"\nAvailable Algorithms: {len(algorithms)}")
    print("-"*60)
    
    for algo in algorithms:
        print(f"\n[{algo['id']}] {algo['name']}")
        print(f"   ID: {algo['id']}")
        print(f"   Class: {algo['class_name']}")
        print(f"   File: {algo['file']}")
        print(f"   Description: {algo['description']}")
    
    print("\n" + "="*60)
