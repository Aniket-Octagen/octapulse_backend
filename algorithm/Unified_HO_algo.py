import pandas as pd
import os

class UnifiedHOProcessor:
    """Handles Head Office (HO) tracking (Procurements, Subcontract)"""
    def __init__(self, sheet_type, input_file, output_dir='outputs'):
        self.sheet_type = sheet_type
        self.input_file = input_file
        self.output_file = os.path.join(output_dir, f'{sheet_type}_tracker.xlsx')
        os.makedirs(output_dir, exist_ok=True)
        
    def _calculate_cost_variance(self, budget, actual_cost):
        try:
            return float(budget) - float(actual_cost)
        except (ValueError, TypeError):
            return 0.0

    def _determine_financial_health(self, variance):
        if variance > 0: return 'Under Budget'
        if variance == 0: return 'On Budget'
        return 'Over Budget'

    def process(self):
        try:
            df = pd.read_excel(self.input_file, sheet_name=self.sheet_type)
            processed = []
            for index, row in df.iterrows():
                budget = row.iloc[1] if len(row) > 1 else 0
                actual = row.iloc[2] if len(row) > 2 else 0
                var = self._calculate_cost_variance(budget, actual)
                processed.append({
                    'Index': index, 'Budget': budget, 'Actual Cost': actual,
                    'Cost Variance': var,
                    'Health Status': self._determine_financial_health(var)
                })
            pd.DataFrame(processed).to_excel(self.output_file, index=False)
            return True
        except Exception as e:
            print(f'Error: {e}')
            return False
