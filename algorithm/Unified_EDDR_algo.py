import pandas as pd
import os

class UnifiedEDDRProcessor:
    """Handles EDDR sheets applying standard formulas"""
    def __init__(self, sheet_type, input_file, output_dir='outputs'):
        self.sheet_type = sheet_type
        self.input_file = input_file
        self.output_file = os.path.join(output_dir, f'{sheet_type}_tracker.xlsx')
        os.makedirs(output_dir, exist_ok=True)
        
    def _calculate_delay_days(self, target_date, actual_date):
        try:
            return (pd.to_datetime(actual_date) - pd.to_datetime(target_date)).days
        except Exception:
            return 0

    def _determine_progress(self, items_done, items_total):
        try:
            return (float(items_done) / float(items_total)) * 100
        except (ValueError, TypeError, ZeroDivisionError):
            return 0.0

    def process(self):
        try:
            df = pd.read_excel(self.input_file, sheet_name=self.sheet_type)
            processed = []
            for index, row in df.iterrows():
                plan_d = row.iloc[1] if len(row) > 1 else None
                act_d = row.iloc[2] if len(row) > 2 else None
                processed.append({
                    'Index': index, 'Plan Date': plan_d, 'Actual Date': act_d,
                    'Delay Days': self._calculate_delay_days(plan_d, act_d),
                    'Progress %': self._determine_progress(act_d, plan_d) # Arbitrary progress formula for skeleton
                })
            pd.DataFrame(processed).to_excel(self.output_file, index=False)
            return True
        except Exception as e:
            print(f'Error: {e}')
            return False
