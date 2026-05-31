from pydantic import BaseModel
from typing import Optional, Dict, Any
from datetime import datetime


class TokenScores(BaseModel):
    rugcheck: float = 0.0          # 0-20 pts: seguridad rugcheck
    mint_authority: float = 0.0    # 0-10 pts: mint revocado
    freeze_authority: float = 0.0  # 0-10 pts: freeze desactivado
    liquidity: float = 0.0         # 0-15 pts: liquidez
    holder_distribution: float = 0.0  # 0-10 pts: concentracion wallets
    volume_mc_ratio: float = 0.0   # 0-10 pts: volumen/market cap
    price_trend: float = 0.0       # 0-8 pts: tendencia de precio
    volume_trend: float = 0.0      # 0-7 pts: volumen creciente
    news_sentiment: float = 0.0    # 0-5 pts: noticias
    social_score: float = 0.0      # 0-5 pts: actividad social
    total: float = 0.0             # suma total (0-100)

    def compute_total(self) -> float:
        self.total = (
            self.rugcheck + self.mint_authority + self.freeze_authority +
            self.liquidity + self.holder_distribution + self.volume_mc_ratio +
            self.price_trend + self.volume_trend + self.news_sentiment + self.social_score
        )
        return self.total


class TokenAnalysis(BaseModel):
    address: str
    name: str
    symbol: str
    chain: str = "solana"
    price_usd: float = 0.0
    price_sol: float = 0.0
    liquidity_usd: float = 0.0
    market_cap: float = 0.0
    volume_1h: float = 0.0
    volume_24h: float = 0.0
    price_change_1h: float = 0.0
    price_change_24h: float = 0.0
    holders: int = 0
    top10_pct: float = 0.0
    mint_authority: bool = True    # True = peligroso (aun activo)
    freeze_authority: bool = True  # True = peligroso
    lp_locked: bool = False
    age_minutes: float = 0.0
    rugcheck_risks: list = []
    image_url: str = ""
    category: str = "new"   # new | trending | top
    scores: Optional[TokenScores] = None
    verdict: str = "pending"       # pending | buy | skip | scam
    reason: str = ""


class Position(BaseModel):
    id: int
    token_address: str
    token_name: str
    token_symbol: str
    buy_time: str
    buy_price_usd: float
    buy_price_sol: float
    amount_sol: float
    amount_tokens: float
    target_price_usd: float
    stop_price_usd: float
    current_price_usd: float = 0.0
    pnl_pct: float = 0.0
    pnl_sol: float = 0.0
    scores: Optional[Dict] = None
    status: str = "open"


class Trade(BaseModel):
    id: int
    token_address: str
    token_name: str
    token_symbol: str
    buy_time: str
    sell_time: str
    buy_price_usd: float
    sell_price_usd: float
    amount_sol: float
    pnl_sol: float
    pnl_pct: float
    outcome: str  # profit | loss | stop_loss | take_profit


class BotStatus(BaseModel):
    running: bool = False
    mode: str = "live"  # live | paper
    sol_balance: float = 0.0
    eur_balance: float = 0.0
    sol_price_eur: float = 0.0
    total_pnl_sol: float = 0.0
    total_pnl_pct: float = 0.0
    open_positions: int = 0
    total_trades: int = 0
    win_rate: float = 0.0
    daily_pnl_sol: float = 0.0
    tokens_analyzed_today: int = 0


class WSMessage(BaseModel):
    type: str  # log | position_update | balance_update | new_token | trade_closed
    data: Dict[str, Any]
