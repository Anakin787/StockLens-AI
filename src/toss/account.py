"""Account and asset endpoints.

Every call here needs the ``X-Tossinvest-Account`` header in addition to the
bearer token, so the account sequence is resolved once and reused.
"""

from src.toss.errors import TossConfigError


class AccountApi:
    def __init__(self, client, account_no=None):
        self.client = client
        self.account_no = account_no
        self._account_seq = None
        self._accounts = None

    def list_accounts(self):
        """GET /api/v1/accounts - rate limit group ACCOUNT (1 TPS)."""
        if self._accounts is None:
            self._accounts = self.client.get("/api/v1/accounts", group="ACCOUNT") or []
        return self._accounts

    def resolve_account_seq(self):
        """Pick the account to operate on and cache its ``accountSeq``.

        Uses ``account_no`` from the config when given, otherwise the first
        account returned.
        """
        if self._account_seq is not None:
            return self._account_seq

        accounts = self.list_accounts()
        if not accounts:
            raise TossConfigError(
                "토스증권 계좌를 찾을 수 없습니다. Open API 사용 신청 상태를 확인하세요."
            )

        chosen = None
        if self.account_no:
            wanted = str(self.account_no)
            for account in accounts:
                if str(account.get("accountNo")) == wanted:
                    chosen = account
                    break
            if chosen is None:
                available = ", ".join(str(a.get("accountNo")) for a in accounts)
                raise TossConfigError(
                    f"config의 account_no({self.account_no})에 해당하는 계좌가 없습니다. "
                    f"사용 가능한 계좌: {available}"
                )
        else:
            chosen = accounts[0]

        self._account_seq = chosen.get("accountSeq")
        return self._account_seq

    def holdings(self, symbol=None):
        """GET /api/v1/holdings - rate limit group ASSET (5 TPS).

        Returns the raw result. Toss reports totals split by currency
        (``krw``/``usd``); combining them into one KRW figure is the
        aggregator's job, not this wrapper's.
        """
        params = {"symbol": symbol} if symbol else None
        return self.client.get(
            "/api/v1/holdings",
            group="ASSET",
            params=params,
            account_seq=self.resolve_account_seq(),
        )

    def buying_power(self, currency):
        """GET /api/v1/buying-power - rate limit group ORDER_INFO (6 TPS).

        Read-only, so it works on a read-only client. Feeds the dashboard's
        "Cash Buying Power" card.
        """
        return self.client.get(
            "/api/v1/buying-power",
            group="ORDER_INFO",
            params={"currency": currency},
            account_seq=self.resolve_account_seq(),
        )

    def sellable_quantity(self, symbol):
        """GET /api/v1/sellable-quantity - ORDER_INFO (6 TPS).

        How many shares can actually be sold right now, which is not the same
        as how many are held: shares bought today, lent out or pledged are
        excluded. Checking this before a sell is what stops the risk gate
        learning about it from a 422.

        Note the peak restriction - ORDER_INFO drops to 3 TPS between 09:00
        and 09:10 - so callers should read this once per symbol per run and
        cache it rather than polling.
        """
        return self.client.get(
            "/api/v1/sellable-quantity",
            group="ORDER_INFO",
            params={"symbol": symbol},
            account_seq=self.resolve_account_seq(),
        )
