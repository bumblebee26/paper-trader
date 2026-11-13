"""Charges on one intraday (MIS) equity order on NSE, as a broker's contract note would show them.

  brokerage       Rs 20 a order (flat; many brokers charge the lower of Rs 20 and 0.03%, so this is conservative)
  STT             0.025% of the value, sell orders only (intraday rate)
  exchange fee    0.00297% (NSE transaction charge)
  SEBI fee        Rs 10 a crore = 0.0001%
  GST             18% of brokerage + exchange fee + SEBI fee
  stamp duty      0.003% of the value, buy orders only
Slippage is separate: it is applied to the fill price itself (engine.slip, exits._exit_fill).
Rates are approximations of the published ones; config.py can override each.
"""
import config


def order(value: float, side: str) -> float:
    """Charges on one order worth `value` rupees. side: the ORDER's side, BUY or SELL (a short's entry is a SELL)."""
    value = abs(float(value))
    brokerage = config.BROKERAGE_PER_ORDER
    exchange = value * config.EXCHANGE_FEE_PCT / 100
    sebi = value * config.SEBI_FEE_PCT / 100
    gst = (brokerage + exchange + sebi) * config.GST_PCT / 100
    stt = value * config.STT_SELL_PCT / 100 if side == "SELL" else 0.0
    stamp = value * config.STAMP_BUY_PCT / 100 if side == "BUY" else 0.0
    return round(brokerage + exchange + sebi + gst + stt + stamp, 2)


def round_trip(entry_value: float, exit_value: float, side: str) -> float:
    """Both orders of a trade. side: the TRADE's side (BUY = buy then sell; SELL = sell then buy back)."""
    exit_side = "SELL" if side == "BUY" else "BUY"
    return round(order(entry_value, side) + order(exit_value, exit_side), 2)
