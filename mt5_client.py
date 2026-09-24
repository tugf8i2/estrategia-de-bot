#!/usr/bin/env python3
"""
Cliente de MetaTrader 5 — ejecuta órdenes reales (o demo, según la cuenta que
tengas logueada en el terminal) directamente en tu cuenta de Vantage.

⚠️ Requiere Windows y el terminal de MetaTrader 5 de Vantage instalado y
logueado (o las credenciales para que este cliente inicie sesión solo).
El paquete `MetaTrader5` de Python SOLO funciona en Windows.

⚠️ ESTO EJECUTA ÓRDENES REALES EN TU CUENTA. Antes de usarlo en cuenta real:
  1. Probalo primero en una cuenta DEMO de Vantage.
  2. Confirmá manualmente en MT5 que las órdenes que manda el bot son
     exactamente las que esperás (símbolo, volumen, TP, SL).
"""

import logging


class MT5Error(Exception):
    pass


class MT5Client:
    def __init__(self, login: str = "", password: str = "", server: str = "",
                 path: str = "", log: logging.Logger = None):
        self.login = login
        self.password = password
        self.server = server
        self.path = path
        self.log = log or logging.getLogger("mt5_client")
        self._mt5 = None
        self._initialized = False
        self._resolved_symbols = {}

    def _lib(self):
        """Import perezoso: si estás en Linux/Mac (como este entorno de pruebas)
        esto va a fallar con un mensaje claro en vez de romper todo el bot al
        importar el módulo, ya que MetaTrader5 solo existe en Windows."""
        if self._mt5 is None:
            try:
                import MetaTrader5 as mt5
            except ImportError as e:
                raise MT5Error(
                    "No se pudo importar el paquete 'MetaTrader5'. Este cliente solo "
                    "funciona en Windows con MT5 instalado. Instalalo con: "
                    "pip install MetaTrader5 --break-system-packages"
                ) from e
            self._mt5 = mt5
        return self._mt5

    def connect(self):
        mt5 = self._lib()
        if self._initialized:
            return

        kwargs = {}
        if self.path:
            kwargs["path"] = self.path
        if self.login and self.password and self.server:
            kwargs.update({
                "login": int(self.login),
                "password": self.password,
                "server": self.server,
            })

        ok = mt5.initialize(**kwargs)
        if not ok:
            raise MT5Error(f"No se pudo conectar a MT5: {mt5.last_error()}")

        self._initialized = True
        info = mt5.account_info()
        if info is None:
            raise MT5Error(f"Conectado a MT5 pero no se pudo leer la cuenta: {mt5.last_error()}")
        self.log.info(
            "MT5: conectado. Cuenta=%s | Servidor=%s | Balance=%.2f %s",
            info.login, info.server, info.balance, info.currency,
        )
        return info

    def resolve_symbol(self, symbol_hint: str) -> str:
        """Confirma que el símbolo existe tal cual, o busca variantes con sufijo
        (algunos brokers agregan '.a', '_i', etc. al nombre base)."""
        if symbol_hint in self._resolved_symbols:
            return self._resolved_symbols[symbol_hint]

        mt5 = self._lib()
        self.connect()

        info = mt5.symbol_info(symbol_hint)
        if info is not None:
            mt5.symbol_select(symbol_hint, True)
            self._resolved_symbols[symbol_hint] = symbol_hint
            return symbol_hint

        candidates = mt5.symbols_get(f"*{symbol_hint}*") or []
        if not candidates:
            raise MT5Error(
                f"No se encontró el símbolo '{symbol_hint}' ni variantes en MT5. "
                "Revisá el nombre exacto en Market Watch de tu terminal de Vantage."
            )

        resolved = candidates[0].name
        mt5.symbol_select(resolved, True)
        self.log.info("Símbolo '%s' resuelto como '%s' en tu broker.", symbol_hint, resolved)
        self._resolved_symbols[symbol_hint] = resolved
        return resolved

    def place_market_order(self, symbol_hint: str, side: str, volume: float,
                            sl: float, tp: float, deviation: int = 20, comment: str = "signal-bot"):
        """Manda una orden de mercado con SL/TP puestos de una vez. side: 'LONG'/'SHORT'."""
        mt5 = self._lib()
        self.connect()
        symbol = self.resolve_symbol(symbol_hint)

        tick = mt5.symbol_info_tick(symbol)
        if tick is None:
            raise MT5Error(f"No se pudo leer el precio actual de '{symbol}': {mt5.last_error()}")

        order_type = mt5.ORDER_TYPE_BUY if side == "LONG" else mt5.ORDER_TYPE_SELL
        price = tick.ask if side == "LONG" else tick.bid

        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": symbol,
            "volume": float(volume),
            "type": order_type,
            "price": price,
            "sl": float(sl),
            "tp": float(tp),
            "deviation": deviation,
            "magic": 20260101,
            "comment": comment,
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_IOC,
        }

        self.log.info("MT5: enviando orden -> %s", request)
        result = mt5.order_send(request)

        if result is None:
            raise MT5Error(f"order_send devolvió None: {mt5.last_error()}")

        result_dict = result._asdict()
        self.log.info("MT5: respuesta de la orden -> %s", result_dict)

        if result.retcode != mt5.TRADE_RETCODE_DONE:
            raise MT5Error(f"MT5 rechazó la orden (retcode={result.retcode}): {result.comment}")

        return result_dict

    def get_open_positions(self, symbol_hint: str = None):
        mt5 = self._lib()
        self.connect()
        if symbol_hint:
            symbol = self.resolve_symbol(symbol_hint)
            positions = mt5.positions_get(symbol=symbol)
        else:
            positions = mt5.positions_get()
        return [p._asdict() for p in positions] if positions else []

    def close_all_positions(self, symbol_hint: str = None):
        """Botón de emergencia: cierra todas las posiciones abiertas (del símbolo
        indicado, o todas si no se especifica)."""
        mt5 = self._lib()
        positions = self.get_open_positions(symbol_hint)
        closed = []
        for pos in positions:
            symbol = pos["symbol"]
            volume = pos["volume"]
            is_buy = pos["type"] == mt5.POSITION_TYPE_BUY
            tick = mt5.symbol_info_tick(symbol)
            close_type = mt5.ORDER_TYPE_SELL if is_buy else mt5.ORDER_TYPE_BUY
            price = tick.bid if is_buy else tick.ask
            request = {
                "action": mt5.TRADE_ACTION_DEAL,
                "symbol": symbol,
                "volume": volume,
                "type": close_type,
                "position": pos["ticket"],
                "price": price,
                "deviation": 20,
                "magic": 20260101,
                "comment": "signal-bot-close-all",
                "type_time": mt5.ORDER_TIME_GTC,
                "type_filling": mt5.ORDER_FILLING_IOC,
            }
            result = mt5.order_send(request)
            closed.append((pos["ticket"], result._asdict() if result else None))
        return closed
