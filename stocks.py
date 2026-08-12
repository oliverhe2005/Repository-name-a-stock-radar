from dataclasses import dataclass


@dataclass(frozen=True)
class Stock:
    code: str
    name: str
    market: str  # sh / sz / bj

    @property
    def ticker(self) -> str:
        return f"{self.market}{self.code}"


WATCHLIST = [
    Stock("002371", "北方华创", "sz"),
    Stock("603268", "松发股份", "sh"),
    Stock("688188", "柏楚电子", "sh"),
    Stock("002156", "通富微电", "sz"),
    Stock("688017", "绿的谐波", "sh"),
    Stock("300620", "光库科技", "sz"),
    Stock("688825", "长鑫科技", "sh"),
    Stock("300373", "扬杰科技", "sz"),
    Stock("688020", "方邦股份", "sh"),
    Stock("002463", "沪电股份", "sz"),
    Stock("002851", "麦格米特", "sz"),
    Stock("920045", "蘅东光", "bj"),
    Stock("300394", "天孚通信", "sz"),
    Stock("002384", "东山精密", "sz"),
    Stock("300502", "新易盛", "sz"),
    Stock("300750", "宁德时代", "sz"),
    Stock("301488", "豪恩汽电", "sz"),
    Stock("603986", "兆易创新", "sh"),
]

WATCHLIST_BY_CODE = {s.code: s for s in WATCHLIST}
