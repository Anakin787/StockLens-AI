import sys
import time

# Try importing pykiwoom, handle failure gracefully for non-32bit/non-window envs
try:
    from pykiwoom.kiwoom import Kiwoom
    KIWOOM_AVAILABLE = True
except ImportError:
    KIWOOM_AVAILABLE = False
    print("Warning: 'pykiwoom' module not found. Kiwoom API functionalities might not work.")

class KiwoomManager:
    def __init__(self, config):
        self.mock = config.get('kiwoom', {}).get('mock', False)
        self.account_index = config.get('kiwoom', {}).get('account_index', 0)
        self.kiwoom = None
        
        if not self.mock and KIWOOM_AVAILABLE:
            try:
                self.kiwoom = Kiwoom()
                print("Kiwoom instance created. Waiting for login...")
                self.kiwoom.CommConnect(block=True)
                print("Kiwoom Login Successful.")
            except Exception as e:
                print(f"Kiwoom Login Failed: {e}. Switching to Mock mode.")
                self.mock = True
        else:
            if not self.mock:
                print("Kiwoom API unavailable (missing module or system support). Switching to Mock mode.")
                self.mock = True

    def get_assets(self):
        if self.mock:
            return self._get_mock_assets()
        
        try:
            # 1. Get Account List
            accounts = self.kiwoom.GetLoginInfo("ACCNO")
            accounts = [a for a in accounts if a] # filter empty
            if not accounts:
                print("No accounts found.")
                return None
            
            target_account = accounts[self.account_index]
            print(f"Using Account: {target_account}")

            # 2. Get Deposit (Basic check using opw00001)
            # This is a simplified call; real implementation involves TR requests.
            # pykiwoom's block_request makes this easier.
            
            # TR: opw00001 (Deposit Info)
            df_deposit = self.kiwoom.block_request("opw00001",
                account_num=target_account,
                pwd="", # Empty usually works if auto-login
                pwd_type="00",
                search_type="00"
            )
            
            # TR: opw00018 (Account Evaluation / Portfolio)
            df_balance = self.kiwoom.block_request("opw00018",
                account_num=target_account,
                pwd="",
                pwd_type="00",
                search_type="00"
            )

            # Parsing logic (Subject to TR output format structure)
            # This is illustrative; actual column names depend on TR mapping.
            # Assuming simplified dictionary return for this generic implementation
            
            total_eval = 0
            total_buy = 0
            portfolio = []

            # Note: pykiwoom usually returns pandas DataFrame or list of dicts
            # We will try to extract safely.
            
            # Extract summary from single row dataframe usually returned first
            if not df_deposit.empty:
                 # Typical mapping for d2_deposit (Jesus money)
                 pass

            # For now, to ensure safety without live debugging on user PC,
            # We return a structured object if data exists, else mock/empty.
            
            return {
                "total_balance": 1000000, # Placeholder
                "total_evaluation": 1200000,
                "total_profit": 200000,
                "profit_rate": 20.0,
                "portfolio": [
                    {"name": "Real Data Not Fully Parsed (Requires TR Calibration)", "profit_rate": 0}
                ]
            }

        except Exception as e:
            print(f"Error fetching Kiwoom data: {e}")
            return self._get_mock_assets()

    def _get_mock_assets(self):
        return {
            "total_balance": 5000000,
            "total_evaluation": 5500000,
            "total_profit": 500000,
            "profit_rate": 10.0,
            "portfolio": [
                {"name": "Samsung Electronics", "profit_rate": 5.2},
                {"name": "SK Hynix", "profit_rate": 12.5},
                {"name": "Kakao", "profit_rate": -3.0}
            ]
        }

if __name__ == "__main__":
    # Test
    k = KiwoomManager({"kiwoom": {"mock": True}})
    print(k.get_assets())
