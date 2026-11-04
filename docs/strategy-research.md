# Strategy, news & social-media research

Researched November 2026. Each claim is labelled by how strong the evidence is:
**Peer-reviewed** (published in an academic journal), **Working paper** (academic-style but not peer-reviewed),
**Backtest** (someone's own simulation), or **Marketing** (trading-education or vendor content).
A big backtest number is not proof. Most of the eye-catching results below have never been independently reproduced.

---

## The most important finding for *our* bot

The bot currently runs an opening-range breakout on a **fixed list of big stocks** (SPY, AAPL, NVDA…).
The main study of opening-range breakouts on US stocks (Zarattini, Barbon & Aziz, 2024, *Working paper*) found:

| Version (2016–2023, $0.0035/share commission) | Total return | Per year | Sharpe | Max drawdown |
|---|---|---|---|---|
| ORB on **all** liquid stocks (close to what we run) | 29% | 3.2% | 0.48 | 13% |
| ORB on the day's **20 most unusually active** stocks ("stocks in play") | 1,637% | 41.6% | 2.81 | 12% |
| S&P 500 buy-and-hold | 198% | 14.2% | 0.78 | 34% |

- **The breakout rule itself barely worked. Picking the right stocks did almost all the work.** "In play" means the
  stock's volume in the first 5 minutes is at least 100% of its normal first-5-minute volume (its 14-day average). The
  higher that ratio, the better the trades: about −0.02R per trade below 100%, rising to about 0.38R above 3,000%.
- Their rules: 5-minute opening range. Go long if the first candle closes up, short if down. Stop at 10% of the
  stock's 14-day ATR (average daily price range). No profit target: exit at the close. Risk 1% per trade, max 4x leverage.
  Filters: price > $5, average volume > 1M shares/day, ATR > $0.50.
- **Big caveats:**
  - **No slippage is modelled.** Entries are stop orders in the first minutes of the day on the most frantic stocks,
    which is exactly where real fills are worst, and a stop of 10% of ATR is very tight, so slippage eats a large share of each trade's risk.
  - Not peer-reviewed. The authors sell trading education (Bear Bull Traders).
  - One independent test on Nasdaq futures with realistic costs found **no** ORB variant that held up.

**What this means for us:** our fixed-list version is the weak variant. The evidence points to adding a
**stocks-in-play scanner** and testing it *with realistic slippage* before trusting it. That's the recommended next build.

---

## Other strategies with real evidence

| Strategy | Evidence | What it found | Usable by us? |
|---|---|---|---|
| **Intraday momentum** (Gao, Han, Li & Zhou, *J. Financial Economics* 2018) | **Peer-reviewed** | The S&P 500's first-half-hour return predicts its last-half-hour return, more so on volatile, high-volume or news days. Replications say the effect is real but small (explains ~1–2% of the move; ~6%/yr). | Yes, as a SPY add-on. Small edge. |
| **SPY "noise area" momentum** (Zarattini, Aziz & Barbon, 2024) | Working paper | Trade SPY only when it breaks out of its usual intraday range. Reported 19.6%/yr, Sharpe 1.33, 2007–2024, **including** commissions and slippage. | Yes. One symbol, easy to test, and it modelled costs. Good candidate. |
| **End-of-day reversal** (Baltussen, Da & Soebhag) | Working paper (academic) | Individual stocks tend to reverse in the last half hour, linked to retail buying and selling pressure. | Maybe, as a filter on late trades. |
| **Short-term contrarian** (reverse big 60-minute moves) | Academic | Profitable after the bid-ask spread in liquid stocks, but reviews say the gains mostly go to high-frequency traders. | Unlikely for a retail bot. |
| **Post-earnings drift** | Peer-reviewed, contested | Prices keep drifting in the direction of an earnings surprise for days or weeks. Recent papers disagree on whether it still exists. | Multi-day, not day trading. |
| "90% win-rate" scalping, indicator combos, chart patterns | Marketing | No rigorous testing found. | No. |

**How retail day traders actually do:** in the most-cited study (Brazil, Chague et al. 2019, cited second-hand), about 97% of people who day-traded for 300+ days lost money. Another study found fewer than 20% earned a net profit. The edge, if any, comes from discipline, costs and stock selection, not from finding a secret indicator.

## Tips that the evidence supports
1. **Trade where something is happening.** Unusual volume or news is where intraday trends continue. Quiet stocks just chop around.
2. **Accept a low win rate with big winners.** The working ORB variants won only ~40–48% of trades. Profit came from small, fixed losses and letting winners run.
3. **Size every trade by risk**, a fixed small % of the account. The bot already does this.
4. **Costs and slippage decide whether a strategy works.** Always backtest with them. Our backtester already assumes worse-than-ideal fills.
5. **Distrust headline backtests**, especially ones tuned over many parameter combinations. Keep some data you never tune on.
6. **Trade less, not more.** Every extra trade pays the spread and commission.

---

## News, X (Twitter) and social posts

| Source | Status (tested today) | Cost | Verdict |
|---|---|---|---|
| **Alpaca news (Benzinga)** | Reachable; uses your Alpaca keys | Included with Alpaca market data (free tier has rate limits) | **Use it.** Real-time stream plus history back to 2015, so it *can* be backtested. Best free news source for the bot. |
| Alpha Vantage news sentiment | Tested: **premium-only** | Paid plan | Skip for now. |
| **StockTwits** public stream | Tested: works, returns the latest 30 posts per symbol | Free | Very noisy. In today's NVDA sample about half the posts had no sentiment tag, and many were off-topic or about politics. No free history, so it can't be backtested. Use for context only, not trades. |
| **X (Twitter)** | Website loads; the API needs a paid developer account | Pay per use: about $0.005 per post read, recent-search window about 7 days | Not worth it yet. Example: 8 symbols × 100 posts × 21 trading days ≈ 16,800 reads ≈ $84/month, and there's no cheap way to backtest it. Scraping X instead breaks its terms. |
| Reddit | Blocked without API sign-up (403) | Free tier with limits | Same problem: noisy and hard to backtest. |

**What the research says about social sentiment:** mixed and short-lived. One large StockTwits study (~60M posts, 2012–2016) found that sentiment in a given half-hour helps predict the next half-hour of index returns, and a 2024 study found StockTwits sentiment predicts returns around events. But a Twitter study found no causal link, and the effects that do exist fade within hours and haven't been shown to survive costs. **News** has somewhat better support. For example, a 2025 study found AI-scored news predicted 30-minute moves within the first two hours after dividend announcements.

**The core problem:** a signal you can't backtest is a guess. News from Alpaca has history and can be tested. X and StockTwits mostly don't (cheaply), so they shouldn't place trades.

**How news fits into the bot:**
1. **Catalyst tag:** when picking stocks in play, flag those with fresh news. That's the reason the volume is unusual.
2. **Alerts:** include the latest headline in each trade alert so you can see *why* a stock is moving.
3. **Later, after backtesting:** score headlines (positive/negative) and test whether trading only in the news's direction helps.

---

## TradingView's role
With network access now open, TradingView is reachable. Its role is the one in
[tradingview-evaluation.md](tradingview-evaluation.md): visually checking trades and running a second backtest with
`tradingview/orb_strategy.pine`. It's not the data source. Testing the Pine script needs your TradingView login in your
own browser; it takes about 2 minutes and you don't need to share any password.

## Recommended plan
1. **Now:** finish setup (`python -m bot status`), then run the current bot's backtest on a year of real data as a baseline.
2. **Next build:** a stocks-in-play scanner (relative volume, ATR stop, slippage included) plus Alpaca news tagging. Backtest it against the baseline.
3. **Paper trade** whichever version wins for 4–6 weeks.
4. Revisit X/StockTwits only if the news signal proves useful and there's a budget for X's API.

## Sources
- Zarattini, Barbon & Aziz (2024), *A Profitable Day Trading Strategy for the U.S. Equity Market*, SSRN 4729284 / SFI RP 24-98: https://ideas.repec.org/p/chf/rpseri/rp2498.html (full text read from https://www.wealth-lab.com/api/discussion/download/pdf/8007-ssrn-4729284-1-pdf)
- Critiques and replications: https://blog.tradoki.com/posts/opening-range-breakout-stocks-in-play-research · https://danfin.net/opening-range-breakout-research · https://www.quantconnect.com/research/18444/opening-range-breakout-for-stocks-in-play/
- Gao, Han, Li & Zhou (2018), *Market Intraday Momentum*, JFE 129(2): https://econpapers.repec.org/RePEc:eee:jfinec:v:129:y:2018:i:2:p:394-414 · replication notes: https://www.cxoadvisory.com/?p=25481
- Zarattini, Aziz & Barbon, *Beat the Market: An Effective Intraday Momentum Strategy for SPY*: https://ideas.repec.org/p/chf/rpseri/rp2497.html · https://concretumgroup.com/beat-the-market-an-effective-intraday-momentum-strategy-for-sp500-etf-spy/
- Baltussen, Da & Soebhag, *End-of-Day Reversal*: https://academicweb.nd.edu/~zda/EOD.pdf
- Lou, Polk & Skouras, *A Tug of War: Overnight Versus Intraday Expected Returns*: http://www.econ.yale.edu/~shiller/behfin/2015-04-11/lou_polk_skouras.pdf
- Intraday contrarian evidence: https://arxiv.org/pdf/cond-mat/0406696
- Intraday online investor sentiment (StockTwits): https://www.sciencedirect.com/science/article/abs/pii/S0378426617301589
- News vs social media sentiment: https://www.mdpi.com/1911-8074/18/12/660
- Intraday sentiment, dividend announcements (2025): https://www.tandfonline.com/doi/full/10.1080/15427560.2025.2538879
- Post-earnings drift debate: https://anderson-review.ucla.edu/is-post-earnings-announcement-drift-a-thing-again/
- X API pricing: https://docs.x.com/x-api/getting-started/pricing · https://www.socialcrawl.dev/blog/x-twitter-api-2026
- Alpaca news API: https://alpaca.markets/docs/news-api · https://docs.alpaca.markets/us/docs/streaming-real-time-news.md
- StockTwits API overview: https://jentic.com/apis/stocktwits
