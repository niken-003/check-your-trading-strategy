"""Builds examples/ReportHistory-example.html: an MT5-style history report with made-up trades."""
import numpy as np, pandas as pd
rng=np.random.default_rng(11)
rows=[]; t=pd.Timestamp("2026-03-02 10:05"); pos=1000001
while len(rows)<180:
    if t.dayofweek>=5: t+=pd.Timedelta(days=1); continue
    for _ in range(rng.integers(0,5)):
        sym=rng.choice(["XAUUSD","EURUSD","US100.cash"],p=[.5,.35,.15]); side=rng.choice(["buy","sell"])
        vol=round(float(rng.choice([0.5,1.0,1.5])),2)
        price={"XAUUSD":4100,"EURUSD":1.135,"US100.cash":24500}[sym]*(1+rng.normal(0,.002))
        win=rng.random()<0.47; pnl=rng.normal(1500,300) if win else -rng.normal(1250,250)
        comm=-5*vol if sym=="EURUSD" else 0.0; swap=round(float(rng.choice([0,0,0,-3.2])),2)
        ot=t+pd.Timedelta(minutes=int(rng.integers(0,400))); ct=ot+pd.Timedelta(minutes=int(rng.integers(5,240)))
        sl="" if rng.random()<.2 else f"{price*(0.997 if side=='buy' else 1.003):.5f}"
        rows.append((ot,pos,sym,side,vol,price,sl,"",ct,price*(1+rng.normal(0,.001)),comm,swap,round(pnl,2))); pos+=1
    t+=pd.Timedelta(days=1)
def td(x,cls=""): return f'<td{cls}>{x}</td>'
h=['<html><head><meta charset="utf-16"><title>ReportHistory</title></head><body><table>',
   '<tr><td colspan="13"><div><b>Trade History Report</b></div></td></tr>',
   '<tr><td colspan="3">Name:</td><td colspan="10"><b>Example Trader</b></td></tr>',
   '<tr><td colspan="3">Account:</td><td colspan="10"><b>0000000 (USD, Example-Demo, demo, Hedge)</b></td></tr>',
   '<tr><td colspan="13" style="height:20px"></td></tr>',
   '<tr><th colspan="13"><b>Positions</b></th></tr>',
   '<tr><td>Time</td><td>Position</td><td>Symbol</td><td>Type</td><td>Volume</td><td>Price</td><td>S / L</td><td>T / P</td><td>Time</td><td>Price</td><td>Commission</td><td>Swap</td><td>Profit</td></tr>']
for r in rows:
    ot,p,sym,side,vol,pr,sl,tp,ct,cp,comm,swap,pnl=r
    h.append("<tr bgcolor=\"#FFFFFF\">"+td(ot.strftime("%Y.%m.%d %H:%M:%S"))+td(p)+td(sym)+td(side)+td(vol)+td(f"{pr:.5f}")+td(sl)+td(tp)
             +td(ct.strftime("%Y.%m.%d %H:%M:%S"))+td(f"{cp:.5f}")+td(f"{comm:.2f}")+td(f"{swap:.2f}")+td(f"{pnl:,.2f}".replace(","," "))+"</tr>")
h+=['<tr><td colspan="13" style="height:20px"></td></tr>','<tr><th colspan="13"><b>Orders</b></th></tr>',
    '<tr><td>Open Time</td><td>Order</td><td>Symbol</td><td>Type</td><td colspan="2">Volume</td><td>Price</td><td>S / L</td><td>T / P</td><td>Time</td><td>State</td><td colspan="2">Comment</td></tr>',
    '<tr><td>2026.03.02 10:05:00</td><td>1</td><td>XAUUSD</td><td>buy</td><td colspan="2">0.50 / 0.50</td><td>market</td><td></td><td></td><td>2026.03.02 10:05:00</td><td>filled</td><td colspan="2"></td></tr>',
    '<tr><th colspan="13"><b>Deals</b></th></tr>',
    '<tr><td>Time</td><td>Deal</td><td>Symbol</td><td>Type</td><td>Direction</td><td>Volume</td><td>Price</td><td>Order</td><td>Commission</td><td>Fee</td><td>Swap</td><td>Profit</td><td>Balance</td></tr>',
    '<tr><td>2026.02.27 09:00:00</td><td>1</td><td></td><td>balance</td><td></td><td></td><td></td><td></td><td>0.00</td><td>0.00</td><td>0.00</td><td>100 000.00</td><td>100 000.00</td></tr>',
    '</table></body></html>']
open("examples/ReportHistory-example.html","wb").write("\n".join(h).encode("utf-16"))
pd.DataFrame([{"close_time":r[8],"symbol":r[2],"type":r[3],"volume":r[4],"commission":r[10],"swap":r[11],"profit":r[12]} for r in rows]).to_csv("examples/trades-example.csv",index=False)
print(len(rows),"example trades")
