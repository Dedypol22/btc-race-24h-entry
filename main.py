import json
import re
import sys

# agent-fun/bars-replay/v1 protocol constants
PROTOCOL = "agent-fun/bars-replay/v1"
MAX_HARNESS_LINE_BYTES = 1_048_576
MAX_BOT_LINE_BYTES = 65_536
BPS = 10_000
U64_MAX = 2**64 - 1
USDC_RAW = 1_000_000

# Strategy constants
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

_U64_RE = re.compile(r"0|[1-9][0-9]{0,19}")
_SYMBOL_RE = re.compile(r"[A-Z0-9]{1,20}")
_CLIENT_ID_RE = re.compile(r"[A-Za-z0-9-][A-Za-z0-9_-]{0,63}")
_KEYS = {
    "buy": ("side", "client_id", "symbol", "notional_quote_units", "max_slippage_bps"),
    "sell": ("side", "client_id", "symbol", "qty", "max_slippage_bps"),
}

class ProtocolError(Exception): pass
class OrderError(Exception): pass

class Holding:
    __slots__ = ("qty", "cost")
    def __init__(self, qty, cost):
        self.qty = qty
        self.cost = cost

class BarsState:
    def __init__(self, hello):
        self.hello = hello
        self.seq = -1
        self.t = -1
        units = int(hello["capital"]["quote_units"])
        self.cash = units
        self.equity = units
        self.positions = {}
        self.latest = {}
        self.symbols = {s["symbol"]: s for s in hello["symbols"]}
        self.warmup_bars = hello.get("warmup_bars", 0)
        self._used = set()
        self._next = 1

    @property
    def warming_up(self):
        return self.warmup_bars > 0 and self.seq < self.warmup_bars

    def close(self, symbol):
        bar = self.latest.get(symbol)
        return None if bar is None else int(bar["c"])

    def client_id(self):
        while True:
            cid = f"o{self._next}"
            self._next += 1
            if cid not in self._used:
                return cid

    def apply_batch(self, batch):
        self.seq = batch["seq"]
        self.t = batch["t"]
        for bar in batch["bars"]:
            self.latest[bar["symbol"]] = bar
        self._set_account(batch["account"])

    def apply_done(self, done):
        self.seq = done["seq"]
        self._set_account(done["account"])

    def _set_account(self, account):
        self.cash = int(account["cash_quote_units"])
        self.equity = int(account["equity_quote_units"])
        self.positions = {p["symbol"]: Holding(int(p["qty"]), int(p["cost_quote_units"])) for p in account["positions"]}

    def to_orders(self, result):
        if result is None: return []
        if not isinstance(result, (list, tuple)):
            raise OrderError(f"batch {self.seq}: expected list of orders")
        cap = self.hello["caps"]["max_orders_per_bar"]
        if len(result) > cap:
            raise OrderError(f"batch {self.seq}: too many orders")
        
        orders = []
        taken = {o.get("client_id") for o in result if isinstance(o, dict) and o.get("client_id")}
        for o in result:
            checked = self._check_order(o)
            cid = checked["client_id"]
            if cid is None:
                while True:
                    cid = f"o{self._next}"
                    self._next += 1
                    if cid not in self._used and cid not in taken:
                        break
            taken.add(cid)
            side = checked["side"]
            keys = _KEYS[side]
            order = {
                "side": side,
                "client_id": cid,
                "symbol": checked["symbol"],
                keys[3]: checked["amount"],
                "max_slippage_bps": checked["bps"]
            }
            orders.append(order)
        return orders

    def _check_order(self, o):
        if not isinstance(o, dict): raise OrderError("order must be dict")
        side = o.get("side")
        if side not in ("buy", "sell"): raise OrderError("invalid side")
        keys = _KEYS[side]
        for k in o:
            if k not in keys: raise OrderError(f"unknown key {k}")
        symbol = o.get("symbol")
        if not isinstance(symbol, str) or not _SYMBOL_RE.fullmatch(symbol):
            raise OrderError("invalid symbol")
        amount = self._to_u64(o.get(keys[3]))
        bps = o.get("max_slippage_bps")
        if not isinstance(bps, int) or not 0 <= bps <= 10000:
            raise OrderError("invalid max_slippage_bps")
        cid = o.get("client_id")
        if cid is not None and (not isinstance(cid, str) or not _CLIENT_ID_RE.fullmatch(cid)):
            raise OrderError("invalid client_id")
        return {"side": side, "client_id": cid, "symbol": symbol, "amount": amount, "bps": bps}

    def _to_u64(self, val):
        if isinstance(val, int): n = val
        elif isinstance(val, str) and _U64_RE.fullmatch(val): n = int(val)
        else: raise OrderError("invalid u64")
        if not 0 <= n <= U64_MAX: raise OrderError("u64 out of range")
        return str(n)

def read_lines(src):
    while True:
        raw = src.readline(MAX_HARNESS_LINE_BYTES + 1)
        if not raw: return
        if raw.endswith(b"\n"): raw = raw[:-1]
        elif len(raw) > MAX_HARNESS_LINE_BYTES: raise ProtocolError("line too long")
        if raw.endswith(b"\r"): raw = raw[:-1]
        yield raw.decode("utf-8")

# Strategy state
closes = []
dip_entry_made = False
fallback_entry_made = False
remembered_entry_close = None
remembered_ref_high = None
is_fallback_position = False

def on_batch(state):
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

def run():
    src = sys.stdin.buffer
    out = sys.stdout
    state = None
    for line in read_lines(src):
        if not line.strip(): continue
        try: msg = json.loads(line)
        except ValueError: raise ProtocolError("invalid JSON")
        kind = msg.get("type")
        if kind == "hello":
            if msg.get("protocol") != PROTOCOL: raise ProtocolError("wrong protocol")
            state = BarsState(msg)
        elif kind == "batch":
            if state is None: raise ProtocolError("batch before hello")
            state.apply_batch(msg)
            res = on_batch(state)
            orders = state.to_orders(res)
            for o in orders: state._used.add(o["client_id"])
            reply = json.dumps({"type": "reply", "seq": msg["seq"], "orders": orders}, separators=(",", ":"), ensure_ascii=False)
            out.write(reply + "\n")
            out.flush()
        elif kind == "done":
            if state is None: raise ProtocolError("done before hello")
            state.apply_done(msg)
            return
        else: raise ProtocolError(f"unknown message type: {kind}")

if __name__ == "__main__":
    try:
        run()
    except (ProtocolError, OrderError) as e:
        sys.exit(1)
    except Exception:
        sys.exit(1)
