import pandas as pd
from pathlib import Path

class UnifiedAlgorithmProcessor:
   

    ALGO_TYPES = [
        'bl_overall_progress_lv2', 'commissioning_rfsu', 'const_precomm',
        'eddr_cntr', 'ho_procurements', 'ho_subcontract', 'manufacture',
        'overall_s_curve', 'pm_s_curve', 'project_management',
        'revised_bl_overall_progress', 'weekly_eddr_cont', 'etc'
    ]

    def __init__(self, algo_type, input_file, output_file=None):
        self.algo_type = algo_type
        self.input_file = Path(input_file)
        self.output_file = Path(output_file or f'output_{algo_type}.xlsx')
        self.data_store = []
        
        self.config = {
            'bl_overall_progress_lv2': {'sheet': 'BL Overall Project Progress Lv2', 'plan': 'Planned %', 'act': 'Actual %'},
            'overall_s_curve': {'sheet': 'Overall S-Curve', 'plan': 'BL Cumm. Early Plan', 'act': 'Cumm. Actual'},
            'eddr_cntr': {'sheet': 'EDDR', 'plan': 'Plan Date', 'act': 'Actual Date'},
        }

    def _calculate_variance(self, plan, actual):
        try:
            return float(actual) - float(plan)
        except (ValueError, TypeError):
            return 0.0

    def _determine_status(self, plan, actual):
        if pd.isna(plan) or str(plan).strip() == '':
            return 'Not Started'
        variance = self._calculate_variance(plan, actual)
        return 'On Time' if variance >= 0 else 'Delayed'

    def process(self):
        print(f'Starting execution for: {self.algo_type}')
        try:
            algo_cfg = self.config.get(self.algo_type, {'sheet': self.algo_type, 'plan': 'Planned', 'act': 'Actual'})
            try:
                df = pd.read_excel(self.input_file, sheet_name=algo_cfg['sheet'])
            except ValueError:
                df = pd.read_excel(self.input_file)
            
            p_col, a_col = algo_cfg['plan'], algo_cfg['act']
            if p_col not in df.columns or a_col not in df.columns:
                p_col = df.columns[1] if len(df.columns) > 1 else df.columns[0]
                a_col = df.columns[2] if len(df.columns) > 2 else df.columns[0]

            for index, row in df.iterrows():
                plan, actual = row.get(p_col, 0), row.get(a_col, 0)
                self.data_store.append({
                    'SN': index + 1,
                    'Planned': plan,
                    'Actual': actual,
                    'Variance': self._calculate_variance(plan, actual),
                    'Status': self._determine_status(plan, actual)
                })

            pd.DataFrame(self.data_store).to_excel(self.output_file, index=False)
            return True
        except Exception as e:
            print(f'Error: {str(e)}')
            return False
