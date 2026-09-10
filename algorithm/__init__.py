

import importlib.util
import sys
from pathlib import Path

_algo_dir = Path(__file__).parent


def _load_class(filename, class_name):
    """Load a class from an algorithm file, supporting filenames with spaces."""
    file_path = _algo_dir / filename
    module_name = f"algorithm.{Path(filename).stem.replace(' ', '_')}"
    if module_name not in sys.modules:
        spec = importlib.util.spec_from_file_location(module_name, file_path)
        if spec is None:
            raise ImportError(f"Cannot load module from {file_path}")
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
    return getattr(sys.modules[module_name], class_name)


# Load all processor classes
EDDRProcessor               = _load_class('main1.py',                         'EDDRProcessor')
ProjectManagementProcessor  = _load_class('project_management.py',            'ProjectManagementProcessor')
HOSubcontractProcessor      = _load_class('ho_subcontract.py',                'HOSubcontractProcessor')
HOAsBuiltsProcessor         = _load_class('ho_as_builts.py',                  'HOAsBuiltsProcessor')
CommissioningRFSUProcessor  = _load_class('commissioning_rfsu.py',            'CommissioningRFSUProcessor')
ConstPreCommProcessor       = _load_class('const_precomm.py',                 'ConstPreCommProcessor')
HOProcurementsProcessor     = _load_class('ho_procurements.py',               'HOProcurementsProcessor')
ManufactureProcessor        = _load_class('manufacture.py',                   'ManufactureProcessor')
OverallSCurveProcessor      = _load_class('overall_s_curve.py',               'OverallSCurveProcessor')
PMSCurveProcessor           = _load_class('pm_s_curve.py',                    'PMSCurveProcessor')
TimelineDeviationProcessor  = _load_class('newalgo.py',                       'TimelineDeviationProcessor')
GenericSheetProcessor       = _load_class('generic_sheet.py',                 'GenericSheetProcessor')

__all__ = [
    'EDDRProcessor',
    'ProjectManagementProcessor',
    'HOSubcontractProcessor',
    'HOAsBuiltsProcessor',
    'CommissioningRFSUProcessor',
    'ConstPreCommProcessor',
    'HOProcurementsProcessor',
    'ManufactureProcessor',
    'OverallSCurveProcessor',
    'PMSCurveProcessor',
    'TimelineDeviationProcessor',
    'GenericSheetProcessor',
]
