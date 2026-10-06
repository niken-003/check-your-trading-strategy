# propcheck

**Is your trading edge real, and what are your odds of passing a prop-firm challenge?**

propcheck reads your MetaTrader 5 trade history and gives you an honest answer in one HTML report:

- **Results after costs.** Net profit, win rate, profit factor and drawdown, with commission and swap included.
- **Skill or luck.** Your trades are compared with thousands of random-direction versions of the same trades.
- **Challenge pass rate.** Your real trading days are replayed thousands of times against prop-firm rules (profit target, daily loss limit, max loss) to estimate how often you would pass.
- **Stability.** Do your early and recent trades agree?
-    Works with any MT5 account and any market: forex, gold, indices or crypto. Results come from each trade's profit, commission and swap in your account currency (USD, EUR, GBP and others are detected automatically).

![Example report](examples/report-screenshot.png)

The screenshot uses made-up example trades. Open `examples/ReportHistory-example_propcheck.html` in your browser to see the full report.

## Quick start

1. Install [Python 3.10+](https://www.python.org/downloads/), then in a terminal:
   ```
   pip install -r requirements.txt
   ```
2. In MT5, open the **History** tab, right-click, choose **Report**, and save it as **HTML** (or Excel).
3. Run:
   ```
   python propcheck.py ReportHistory.html
   ```
4. Open the `ReportHistory_propcheck.html` file it creates.

## Options

| Option | Default | Meaning |
|---|---|---|
| `--target` | 10 | Profit target, % of starting balance |
| `--daily-loss` | 5 | Daily loss limit, % of starting balance |
| `--max-loss` | 10 | Maximum loss, % of starting balance |
| `--min-days` | 4 | Minimum trading days before a pass counts |
| `--max-days` | 60 | Give up after this many trading days |
| `--balance` | auto | Starting balance, if it can't be detected from the report |
| `--sims` | 5000 | Number of simulations |
   | `--currency` | auto | Account currency, e.g. `EUR` (detected from MT5 reports) |

Example with a 2-step challenge's second phase (5% target):

```
python propcheck.py ReportHistory.html --target 5
```

You can also use a CSV with at least `close_time` and `profit` columns (optional: `commission`, `swap`, `symbol`), plus `--balance`.

## How it works

- **Net result** of each closed position = profit + commission + swap.
- **Luck test:** each trade's direction is flipped at random (keeping its size) thousands of times. If those random versions often do as well as you, your results could be luck.
- **Challenge simulation:** whole trading days are drawn at random from your history, with each day's trades kept in order, and the rules are checked after every closed trade.

## Limitations

- A history report only shows **closed** trades. Floating losses while trades were open are invisible, so real daily drawdowns can be deeper than shown.
- The pass rate assumes your past trading continues unchanged. Markets change.
- Fewer than ~50 trades is not enough to judge an edge.

This is a statistical look at past trades. **It is not financial advice** and cannot guarantee future results.

## Privacy

Everything runs on your own computer. Your report is never uploaded anywhere. MT5 reports contain your account number and name, so don't commit your own reports to a public repository (the `.gitignore` here helps prevent that).
## License

MIT. Free to use, modify and share.

   ## Contact
 Questions, custom strategy testing, or collaboration: **niken003gurung@gmail.com**
