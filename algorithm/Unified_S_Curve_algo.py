import pandas as pd
import os

class UnifiedSCurveProcessor:
    def __init__(self, sheet_type, input_file, output_dir='outputs'):
        self.sheet_type = sheet_type
        self.input_file = input_file
        self.output_file = os.path.join(output_dir, f'{sheet_type}_tracker.xlsx')
        os.makedirs(output_dir, exist_ok=True)
        
    def _calculate_variance(self, plan, actual):
        try:
            return float(actual) - float(plan)
        except (ValueError, TypeError):
            return 0.0

    def _determine_status(self, plan, actual):
        if pd.isna(plan) and pd.isna(actual): return 'Not Started'
        if plan == 0 and actual == 0: return 'Not Started'
        return 'On Time' if self._calculate_variance(plan, actual) >= 0 else 'Delayed'

    def process(self):
        try:
            df = pd.read_excel(self.input_file, sheet_name=self.sheet_type)
            processed = []
            for index, row in df.iterrows():
                plan = row.iloc[1] if len(row) > 1 else 0
                actual = row.iloc[2] if len(row) > 2 else 0
                processed.append({
                    'Index': index, 'Planned %': plan, 'Actual %': actual,
                    'Variance %': self._calculate_variance(plan, actual),
                    'Status': self._determine_status(plan, actual)
                })
            pd.DataFrame(processed).to_excel(self.output_file, index=False)
            return True
        except Exception as e:
            print(f'Error: {e}')
            return False
