import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "vendor"))
import makefun_bars

# Named constants
REF_BARS = 480
DIP_PCT = 1.2
BOUNCE_BPS = 10
EQUITY_FRACTION = 0.9
MAX_SLIPPAGE_BPS = 100
RECOVER_PCT = 0.2
STOP_PCT = 3.0
FALLBACK_BAR = 960
FALLBACK_HISTORY = 240
MIN_REF_ENTRIES = 60
LOOKBACK_BOUNCE = 15
LOOKBACK_MOMENTUM = 5

# Module-level state
closes = []
dip_entry_made = False
fallback_entry_made = False
remembered_entry_close = None
remembered_ref_high = None
is_fallback_position = False

def on_batch(state, batch):
    global dip_entry_made, fallback_entry_made, remembered_entry_close, remembered_ref_high, is_fallback_position
    
    if state.warming_up:
        return []
    
    symbols = [s["symbol"] for s in state.hello["symbols"]]
    if not symbols:
        return []
    symbol = symbols[0]
    
    curr_close = state.close(symbol)
    if curr_close is None:
        return []
    
    closes.append(curr_close)
    
    held = state.positions.get(symbol)
    
    if held:
        should_sell = False
        if is_fallback_position:
            if curr_close <= remembered_entry_close * (1 - STOP_PCT / 100):
                should_sell = True
        else:
            if curr_close >= remembered_ref_high * (1 - RECOVER_PCT / 100):
                should_sell = True
            elif curr_close <= remembered_entry_close * (1 - STOP_PCT / 100):
                should_sell = True
        
        if should_sell:
            remembered_entry_close = None
            remembered_ref_high = None
            is_fallback_position = False
            return [{
                "side": "sell",
                "symbol": symbol,
                "qty": held.qty,
                "max_slippage_bps": MAX_SLIPPAGE_BPS
            }]
    else:
        # Check dip entry
        trailing_closes = closes[-REF_BARS-1:-1]
        if len(trailing_closes) >= MIN_REF_ENTRIES:
            ref_high = max(trailing_closes)
            if curr_close <= ref_high * (1 - DIP_PCT / 100):
                if len(closes) >= LOOKBACK_BOUNCE + 1:
                    lowest_15 = min(closes[-LOOKBACK_BOUNCE-1:-1])
                    if curr_close >= lowest_15 * (1 + BOUNCE_BPS / 10000):
                        if len(closes) >= LOOKBACK_MOMENTUM + 1:
                            mom_5 = closes[-LOOKBACK_MOMENTUM-1]
                            if curr_close > mom_5:
                                notional = int(state.equity * EQUITY_FRACTION)
                                if notional > state.cash:
                                    notional = state.cash
                                
                                min_notional = int(state.hello["fees"]["min_notional_quote_units"])
                                if notional >= min_notional:
                                    remembered_entry_close = curr_close
                                    remembered_ref_high = ref_high
                                    dip_entry_made = True
                                    is_fallback_position = False
                                    return [{
                                        "side": "buy",
                                        "symbol": symbol,
                                        "notional_quote_units": notional,
                                        "max_slippage_bps": MAX_SLIPPAGE_BPS
                                    }]
        
        # Check fallback entry
        if not dip_entry_made and not fallback_entry_made and state.seq >= FALLBACK_BAR and len(closes) >= FALLBACK_HISTORY:
            avg_240 = sum(closes[-FALLBACK_HISTORY:]) / FALLBACK_HISTORY
            if curr_close > avg_240:
                notional = int(state.equity * 0.5)
                if notional > state.cash:
                    notional = state.cash
                
                min_notional = int(state.hello["fees"]["min_notional_quote_units"])
                if notional >= min_notional:
                    remembered_entry_close = curr_close
                    remembered_ref_high = None
                    is_fallback_position = True
                    fallback_entry_made = True
                    return [{
                        "side": "buy",
                        "symbol": symbol,
                        "notional_quote_units": notional,
                        "max_slippage_bps": MAX_SLIPPAGE_BPS
                    }]
                    
    return []

makefun_bars.run(on_batch)
