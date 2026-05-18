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
        計算各標籤（主要產業 + 主題族群）的收斂密度
        """
        if not self.sector_engine:
            return []

        # 1. 統計各標籤的總股數 (樣本空間)
        label_total = {}
        for ticker in all_valid_tickers:
            labels = self.sector_engine.get_all_sectors(ticker)
            for label in labels:
                if label == "未知": continue
                label_total[label] = label_total.get(label, 0) + 1

        # 2. 統計各標籤的收斂與突破股數
        label_converging = {}
        label_breakout = {}
        
        for result in converging_results:
            labels = self.sector_engine.get_all_sectors(result.ticker)
            for label in labels:
                if label == "未知": continue
                
                if label not in label_converging:
                    label_converging[label] = []
                    label_breakout[label] = []
                
                label_converging[label].append(result.ticker)
                if result.is_breakout:
                    label_breakout[label].append(result.ticker)

        # 3. 建立 SectorDensity 結果
        densities = []
        for label, conv_tickers in label_converging.items():
            total = label_total.get(label, 1)
            density = len(conv_tickers) / total if total > 0 else 0
            
            densities.append(SectorDensity(
                sector=label,
                density=round(density, 4),
                converging_count=len(conv_tickers),
                total_count=total,
                converging_tickers=conv_tickers,
                breakout_tickers=label_breakout.get(label, []),
            ))

        # 4. 標準化分數 (0-100)
        if densities:
            max_density = max(d.density for d in densities) or 1
            for d in densities:
                d.density_score = round((d.density / max_density) * 100, 1)

        # 按密度排序
        densities.sort(key=lambda x: x.density, reverse=True)
        return densities

    def classify_opportunities(self, converging_results: list,
                               sector_densities: list[SectorDensity],
                               price_data: dict,
                               top_sectors_df=None) -> list[OpportunityResult]:
        """
        對每個收斂標的進行 A/B/C 機會分級 (支持多重標籤)
        """
        # 建立 label → SectorDensity 對照
        density_map = {sd.sector: sd for sd in sector_densities}
        hot_sector_set = set(top_sectors_df['sector'].tolist()) if top_sectors_df is not None and not top_sectors_df.empty else set()

        opportunities = []
        for result in converging_results:
            all_labels = self.sector_engine.get_all_sectors(result.ticker) if self.sector_engine else ["未知"]
            
            # 在所有標籤中找出「最優」的密度與領頭狀態
            best_density_score = 0.0
            is_any_high_density = False
            has_any_leader = False
            is_any_hot = False
            
            best_sd = None
            all_leaders = []
            all_peers = set()

            for label in all_labels:
                if label == "未知": continue
                sd = density_map.get(label)
                if not sd: continue
                
                # 更新最佳密度分
                if sd.density_score > best_density_score:
                    best_density_score = sd.density_score
                    best_sd = sd
                
                # 檢查是否達標
                if sd.density >= self.DENSITY_HIGH_THRESHOLD and sd.converging_count >= self.MIN_CONVERGING_COUNT:
                    is_any_high_density = True
                
                if sd.breakout_tickers:
                    has_any_leader = True
                    all_leaders.extend([{'ticker': t, 'source': f'{label}突破'} for t in sd.breakout_tickers if t != result.ticker])
                
                if label in hot_sector_set:
                    is_any_hot = True
                
                all_peers.update([t for t in sd.converging_tickers if t != result.ticker])

            # 供應鏈資訊 (額外加分)
            chain_position = None
            if self.supply_chain:
                chain_position = self.supply_chain.get_position(result.ticker)
                leader_info = self.supply_chain.detect_chain_leaders(result.ticker, price_data)
                if leader_info.get('has_leader'):
                    has_any_leader = True
                    all_leaders.extend([{'ticker': l['ticker'], 'source': f"供應鏈{l['level']}"} for l in leader_info.get('leaders', [])[:2]])

            # A/B/C 分級
            if is_any_high_density and has_any_leader and is_any_hot:
                grade = 'A'
                reason = f"族群/主題具高收斂密度 ({best_sd.density if best_sd else 0:.1%}) + 有領頭股突破 + 族群動能強勢。"
            elif is_any_high_density and has_any_leader:
                grade = 'B'
                reason = f"高密度且有領頭股，但族群動能尚不明顯。"
            elif is_any_high_density:
                grade = 'B'
                reason = f"族群/主題高密度收斂，但尚無明確突破確認。"
            else:
                grade = 'C'
                reason = f"單獨收斂或族群密度低 ({best_sd.density if best_sd else 0:.1%})，風險較高。"

            opportunities.append(OpportunityResult(
                ticker=result.ticker,
                grade=grade,
                sector="/".join(all_labels),
                density_score=best_density_score,
                has_leader=has_any_leader,
                leaders=all_leaders,
                chain_position=chain_position,
                reason=reason,
                converging_peers=list(all_peers),
            ))

        grade_order = {'A': 0, 'B': 1, 'C': 2}
        opportunities.sort(key=lambda x: (grade_order.get(x.grade, 9), -x.density_score))
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
