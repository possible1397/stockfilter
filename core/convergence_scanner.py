"""
收斂密度聚合掃描器 (Convergence Density Scanner)
核心邏輯:
  - 全市場型態掃描 → 找出所有收斂標的 (不過濾族群)
  - 按族群/供應鏈聚合 → 計算「收斂密度」
  - 偵測 Leader-Laggard → 確認族群是否已有人先突破
  - 輸出 A/B/C 三級機會分類
"""
from dataclasses import dataclass, field
from typing import Optional

from utils.logger import get_logger

logger = get_logger(__name__)


@dataclass
class SectorDensity:
    """族群收斂密度結果"""
    sector: str
    density: float              # 收斂密度 = 收斂股數 / 族群總股數
    converging_count: int       # 收斂中的標的數
    total_count: int            # 族群總標的數
    converging_tickers: list = field(default_factory=list)
    breakout_tickers: list = field(default_factory=list)  # 已突破的標的
    density_score: float = 0.0  # 標準化分數 (0-100)


@dataclass
class OpportunityResult:
    """
    個股機會分級結果
    A 級: 收斂密度高 + 有領頭股突破 (最佳進場)
    B 級: 收斂密度高 + 無突破 (持續監控)
    C 級: 單獨收斂，族群不集中 (風險較高)
    """
    ticker: str
    grade: str                  # 'A', 'B', 'C'
    sector: str
    density_score: float
    has_leader: bool
    leaders: list = field(default_factory=list)
    chain_position: Optional[str] = None
    reason: str = ""
    converging_peers: list = field(default_factory=list)


class ConvergenceScanner:
    """
    收斂密度聚合掃描器
    實作「反轉邏輯」: 用型態收斂密度預測族群輪動，而非用輪動過濾型態
    """
    
    # 密度門檻: 族群內至少有此比例的股票正在收斂才算「高密度」
    DENSITY_HIGH_THRESHOLD = 0.15    # 15% 以上的股票在收斂 (因為收斂條件嚴格，15%已算很高)
    # 至少需要 N 檔收斂才算有統計意義
    MIN_CONVERGING_COUNT = 2

    def __init__(self, sector_engine=None, supply_chain_engine=None):
        """
        sector_engine: SectorRotationEngine 實例 (用於查詢 ticker → 族群)
        supply_chain_engine: SupplyChainEngine 實例 (用於查詢供應鏈)
        """
        self.sector_engine = sector_engine
        self.supply_chain = supply_chain_engine

    def calculate_sector_density(self, converging_results: list,
                                 all_valid_tickers: list) -> list[SectorDensity]:
        """
        計算各族群的三角收斂密度
        
        converging_results: PatternRecognizer.analyze() 回傳 is_converging=True 的 TriangleResult 列表
        all_valid_tickers: 通過 DataCleaner 的所有合格 ticker 列表
        
        回傳: 按密度排序的 SectorDensity 列表
        """
        if not self.sector_engine:
            return []

        # 統計各族群的總股數
        sector_total = {}
        for ticker in all_valid_tickers:
            sector = self.sector_engine.get_sector(ticker)
            if sector == "未知":
                continue
            sector_total[sector] = sector_total.get(sector, 0) + 1

        # 統計各族群的收斂/突破股數
        sector_converging = {}     # {sector: [ticker, ...]}
        sector_breakout = {}       # {sector: [ticker, ...]}
        
        for result in converging_results:
            sector = self.sector_engine.get_sector(result.ticker)
            if sector == "未知":
                continue
            
            if sector not in sector_converging:
                sector_converging[sector] = []
                sector_breakout[sector] = []
            
            sector_converging[sector].append(result.ticker)
            if result.is_breakout:
                sector_breakout[sector].append(result.ticker)

        # 建立 SectorDensity 結果
        densities = []
        for sector, conv_tickers in sector_converging.items():
            total = sector_total.get(sector, 1)
            density = len(conv_tickers) / total if total > 0 else 0
            
            densities.append(SectorDensity(
                sector=sector,
                density=round(density, 4),
                converging_count=len(conv_tickers),
                total_count=total,
                converging_tickers=conv_tickers,
                breakout_tickers=sector_breakout.get(sector, []),
            ))

        # 標準化分數 (0-100)
        if densities:
            max_density = max(d.density for d in densities) or 1
            for d in densities:
                d.density_score = round((d.density / max_density) * 100, 1)

        # 按密度排序
        densities.sort(key=lambda x: x.density, reverse=True)
        return densities

    def classify_opportunities(self, converging_results: list,
                               sector_densities: list[SectorDensity],
                               price_data: dict) -> list[OpportunityResult]:
        """
        對每個收斂標的進行 A/B/C 機會分級
        
        A 級: 族群密度 ≥ 門檻 + 族群內有領頭股已突破
        B 級: 族群密度 ≥ 門檻 + 無突破 (持續監控)
        C 級: 族群密度低 / 族群收斂標的少 (風險較高)
        """
        # 建立 sector → SectorDensity 對照
        density_map = {sd.sector: sd for sd in sector_densities}

        opportunities = []
        for result in converging_results:
            sector = (self.sector_engine.get_sector(result.ticker)
                      if self.sector_engine else "未知")
            sd = density_map.get(sector)

            # 供應鏈資訊
            chain_position = None
            chain_leaders = []
            has_chain_leader = False
            if self.supply_chain:
                chain_position = self.supply_chain.get_position(result.ticker)
                leader_info = self.supply_chain.detect_chain_leaders(
                    result.ticker, price_data)
                chain_leaders = leader_info.get('leaders', [])
                has_chain_leader = leader_info.get('has_leader', False)

            # 判斷密度是否足夠
            high_density = (sd and
                            sd.density >= self.DENSITY_HIGH_THRESHOLD and
                            sd.converging_count >= self.MIN_CONVERGING_COUNT)

            # 是否有領頭股 (族群內突破 或 供應鏈上游領漲)
            has_leader = bool(sd and sd.breakout_tickers) or has_chain_leader
            leader_list = []
            if sd and sd.breakout_tickers:
                leader_list.extend([{'ticker': t, 'source': '族群突破'}
                                     for t in sd.breakout_tickers
                                     if t != result.ticker])
            if chain_leaders:
                leader_list.extend([{'ticker': l['ticker'],
                                      'source': f"供應鏈{l['level']}",
                                      'return_5d': l['return_5d']}
                                     for l in chain_leaders[:3]])

            # A/B/C 分級
            if high_density and has_leader:
                grade = 'A'
                peer_list = [t for t in sd.converging_tickers if t != result.ticker] if sd else []
                peer_str = f" 同業收斂: {', '.join(peer_list[:3])}等" if peer_list else ""
                reason = (f"族群收斂密度 {sd.density:.1%} (高) + "
                          f"有 {len(leader_list)} 檔領頭股。{peer_str}")
            elif high_density:
                grade = 'B'
                peer_list = [t for t in sd.converging_tickers if t != result.ticker] if sd else []
                peer_str = f" 同業收斂: {', '.join(peer_list[:3])}等{len(peer_list)}家" if peer_list else ""
                reason = f"族群收斂密度 {sd.density:.1%} (高)，但尚無突破確認。{peer_str}"
            else:
                grade = 'C'
                density_str = f"{sd.density:.1%}" if sd else "無資料"
                peer_list = [t for t in sd.converging_tickers if t != result.ticker] if sd else []
                peer_str = f" 同業收斂: {', '.join(peer_list)}" if peer_list else ""
                reason = f"族群收斂密度 {density_str} (低)，單獨收斂風險較高。{peer_str}"

            opportunities.append(OpportunityResult(
                ticker=result.ticker,
                grade=grade,
                sector=sector,
                density_score=sd.density_score if sd else 0,
                has_leader=has_leader,
                leaders=leader_list,
                chain_position=chain_position,
                reason=reason,
                converging_peers=peer_list,
            ))

        # 按分級排序: A > B > C，同級別按 density_score 排序
        grade_order = {'A': 0, 'B': 1, 'C': 2}
        opportunities.sort(key=lambda x: (grade_order.get(x.grade, 9),
                                          -x.density_score))
        return opportunities

    def print_density_report(self, sector_densities: list):
        """印出族群收斂密度排行"""
        lines = [
            "\n  ╔══════════════════════════════════════════════════╗",
            "  ║         族群收斂密度排行 (輪動預測)              ║",
            "  ╠══════════════════════════════════════════════════╣",
        ]
        for sd in sector_densities:
            bar = "█" * int(sd.density_score / 5)
            hot = "[熱]" if sd.density >= self.DENSITY_HIGH_THRESHOLD else "    "
            bk = f"+{len(sd.breakout_tickers)}突破" if sd.breakout_tickers else ""
            lines.append(
                f"  ║ {hot} {sd.sector:12} │ 密度: {sd.density:5.1%} │ "
                f"{sd.converging_count:2}/{sd.total_count:3} 收斂 {bk:8} │ {bar}"
            )
        lines.append("  ╚══════════════════════════════════════════════════╝")
        logger.info("\n".join(lines))

    def print_opportunity_report(self, opportunities: list):
        """印出 A/B/C 機會分級報告"""
        for grade in ['A', 'B', 'C']:
            items = [o for o in opportunities if o.grade == grade]
            if not items:
                continue
            logger.info(f"\n  === [{grade}] {grade} 級機會 ({len(items)} 檔) ===")
            for o in items:
                chain_str = f" [{o.chain_position}]" if o.chain_position else ""
                leader_str = ""
                if o.leaders:
                    leader_str = " <- 領頭: " + ", ".join(l['ticker'] for l in o.leaders[:2])
                logger.info(
                    f"  {o.ticker:10} │ {o.sector:12}{chain_str} "
                    f"│ 密度分: {o.density_score:5.1f} {leader_str}"
                )
                logger.info(f"  {'':10} └ {o.reason}")
