import ccxt
import time
import os
import pandas as pd
import telebot
from dotenv import load_dotenv
from typing import Optional, Dict, Any
from telebot.types import ReplyKeyboardMarkup, KeyboardButton

load_dotenv()

API_KEY = os.getenv("API_KEY")
API_SECRET = os.getenv("API_SECRET")
API_KEY_TESTNET = os.getenv("API_KEY_TESTNET")
API_SECRET_TESTNET = os.getenv("API_SECRET_TESTNET")
URL_API_TESTNET = os.getenv("URL_API_TESTNET")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

SIMULATION_MODE = False  # Ativa o modo simulado

class TradingBot:
    def __init__(self, symbol: str, initial_balance: float, risk_per_trade: float = 0.02, max_drawdown: float = 0.1, daily_profit_target: float = 0.3, simulation_mode: bool = True):
        self.symbol = symbol            # Símbolo do par de trading
        self.active_position: Optional[Dict[str, Any]] = None   # Posição ativa (None se não houver)

        self.bot_running = False    # Iniciar o bot desligado
        self.simulation_mode = simulation_mode      # Modo de simulação

        self.prev_rsi = None  # RSI anterior

        """
        Gerenciamento de Risco

        - initial_balance: Saldo total inicial (Ex.: Se o saldo inicial for $1.000, todas as operações e cálculos de risco serão baseados nisso)
        - risk_per_trade: Percentual de risco por operação (Ex.: Com 2% de risco e um saldo de $1.000, cada operação pode usar $20)
        - max_drawdown: Perda máxima diária permitida (Ex.: Se o max_drawdown for 10% e o saldo for $1.000, o bot nunca perderá mais do que $100 por dia)
        - daily_profit_target: Meta de lucro diária em USD (Ex.: Se a meta for 30% e o saldo for $1.000, ao atingir $300 de lucro, o bot para automaticamente)
        - max_daily_loss: Perda máxima diária em USD (Calculado automaticamente) (Ex.: Com um saldo de $1.000 e max_drawdown de 10%, a perda máxima diária será de $100)
        """
        self.daily_pnl = 0  # Acumula o PNL diário
        self.initial_balance = initial_balance  # Saldo total inicial
        self.current_balance = initial_balance  # Saldo atualizado
        self.risk_per_trade = risk_per_trade  # Percentual de risco por operação (ex: 2%)
        self.max_drawdown = max_drawdown  # Perda máxima diária permitida (ex: 10%)
        self.max_daily_loss = self.initial_balance * self.max_drawdown  # Perda máxima em USD
        self.daily_profit_target = self.initial_balance * daily_profit_target  # Meta de lucro diária em USD

        self.setup_exchange()
        self.setup_telegram()
    
    def setup_exchange(self):
        try:
            api_key = API_KEY_TESTNET if self.simulation_mode else API_KEY
            api_secret = API_SECRET_TESTNET if self.simulation_mode else API_SECRET
            
            exchange_config = {
                'apiKey': api_key,
                'secret': api_secret,
                'options': {
                    'defaultType': 'spot',
                    'adjustForTimeDifference': True,
                },
                'enableRateLimit': True,
            }

            if self.simulation_mode:
                exchange_config['urls'] = {'api': URL_API_TESTNET}
            
            self.exchange = ccxt.binance(exchange_config)

            if self.simulation_mode:
                self.exchange.set_sandbox_mode(True)

            self.exchange.fetch_balance()
            print(f"Conexão estabelecida com {'testnet' if self.simulation_mode else 'produção'}")

        except Exception as e:
            error_msg = f"Erro ao configurar exchange: {e}"
            # self.logger.error(error_msg)
            raise Exception(error_msg)
     
    ####### START - TELEGRAM BOT CONFIG ####################################################
    def setup_telegram(self):
        """Configura o bot do Telegram com botões"""
        self.telegram_bot = telebot.TeleBot(TELEGRAM_BOT_TOKEN)
        
        # Cria o teclado com botões
        self.keyboard = ReplyKeyboardMarkup(row_width=2, resize_keyboard=True)
        self.keyboard.add(
            KeyboardButton('/start_bot'),
            KeyboardButton('/stop_bot'),
            KeyboardButton('/simulation'),
            KeyboardButton('/status'),
            KeyboardButton('/posicao'),
            KeyboardButton('/resultados_do_dia'),
            KeyboardButton('/ajuda'),
            KeyboardButton('/cancelar_ordens'),
        )

        # Registra os handlers dos comandos
        @self.telegram_bot.message_handler(commands=['start'])
        def send_welcome(message):
            self.telegram_bot.reply_to(
                message, 
                "Bot de Trading iniciado! Use os botões abaixo para controlar:",
                reply_markup=self.keyboard
            )
        
        @self.telegram_bot.message_handler(commands=['start_bot'])
        def start_bot(message):
            self.bot_running = True
            self.send_telegram_message("🤖 Bot iniciado com sucesso! ✅")
        
        @self.telegram_bot.message_handler(commands=['stop_bot'])
        def stop_bot(message):
            self.bot_running = False
            self.send_telegram_message("🤖 Bot parado com sucesso! ❌")
        
        @self.telegram_bot.message_handler(commands=['simulation'])
        def toggle_simulation(message):
            self.simulation_mode = not self.simulation_mode
            self.send_telegram_message(f"Modo simulação: {'✅' if self.simulation_mode else '❌'}")
        
        @self.telegram_bot.message_handler(commands=['cancelar_ordens'])
        def cancel_orders(message):
            self.cancel_all_orders()
            
        @self.telegram_bot.message_handler(commands=['status'])
        def get_status(message):
            self.send_status()
            
        @self.telegram_bot.message_handler(commands=['posicao'])
        def get_position(message):
            self.send_position_info()

        @self.telegram_bot.message_handler(commands=['resultados_do_dia'])
        def get_pnl_day(message):
            self.send_daily_pnl()
            
        @self.telegram_bot.message_handler(commands=['ajuda'])
        def send_help(message):
            help_text = """
            Comandos disponíveis:
            /start_bot - Inicia o bot
            /stop_bot - Para o bot
            /simulation - Ativa/desativa modo simulação
            /cancelar_ordens - Cancela todas as ordens ativas
            /status - Mostra status atual do bot
            /posicao - Mostra detalhes da posição atual
            /resultados_do_dia - Mostra o PNL do dia
            /ajuda - Mostra esta mensagem
            """
            self.telegram_bot.reply_to(message, help_text)
        
        # Inicia o bot do Telegram em uma thread separada
        import threading
        threading.Thread(target=self.telegram_bot.polling, daemon=True).start()

    def send_status(self):
        """Envia status atual do bot"""
        try:
            price = self.exchange.fetch_ticker(self.symbol)['last']
            rsi = self.get_rsi()
            volume = self.get_volume()
            
            status = f"""
            📊 Status do Bot:
            Símbolo: {self.symbol}
            Preço atual: {price}
            RSI: {rsi:.2f}
            Volume: {volume:.2f}
            Modo simulação: {'✅' if self.simulation_mode else '❌'}
            Bot ativo: {'✅' if self.bot_running else '❌'}
            PNL Diário: ${self.daily_pnl:.2f}
            """
            
            self.send_telegram_message(status)
        except Exception as e:
            self.send_telegram_message(f"Erro ao buscar status: {e}")

    def send_position_info(self):
        """Envia informações detalhadas da posição atual"""
        if not self.active_position:
            self.send_telegram_message("Sem posição ativa no momento")
            return
            
        try:
            current_price = self.exchange.fetch_ticker(self.symbol)['last']
            entry_price = self.active_position['entry_price']
            trade_size = self.active_position['trade_size']  
            
            profit_perc = ((current_price - entry_price) / entry_price) * 100
            profit_absolute = (current_price - entry_price) * float(trade_size)
            
            if self.active_position['side'] == 'sell':
                profit_perc = -profit_perc
                profit_absolute = -profit_absolute
            
            position_info = f"""
            📍 Posição Atual:
            Side: {self.active_position['side'].upper()}
            Entrada: {entry_price}
            Preço atual: {current_price}
            Quantidade: {trade_size}
            P&L: {profit_perc:.2f}% (${profit_absolute:.2f})
            """
            
            self.send_telegram_message(position_info)
        except Exception as e:
            self.send_telegram_message(f"Erro ao buscar informações da posição: {e}")
    
    def send_daily_pnl(self):
        """Obtém o lucro/prejuízo total do dia."""
        try:
            # Calcula o timestamp correto para as últimas 24h em milissegundos
            since = int((time.time() - 86400) * 1000)

            # Pega todas as ordens fechadas no dia
            orders = self.exchange.fetch_closed_orders(self.symbol, since=since)

            if not orders:
                self.send_telegram_message("📊 Nenhuma ordem fechada nas últimas 24 horas.")
                return

            total_pnl = 0
            total_trades = 0

            for i in range(len(orders) - 1):  # Percorre as ordens em pares (VENDA -> COMPRA)
                sell_order = orders[i]
                buy_order = orders[i + 1]

                if sell_order['side'] == 'sell' and buy_order['side'] == 'buy':
                    # Pegamos os preços médios de execução (evita erro com `price == 0.0`)
                    sell_price = sell_order['average']
                    buy_price = buy_order['average']

                    if sell_price and buy_price:
                        pnl = (sell_price - buy_price) * sell_order['amount']
                        total_pnl += pnl
                        total_trades += 1

            message = f"📊 PNL Diário: ${total_pnl:.2f} em {total_trades} operações"
            self.send_telegram_message(message)
        
        except Exception as e:
            print(f"🚨 Erro ao obter PNL diário: {e}")
            self.send_telegram_message(f"Erro ao obter PNL diário: {e}")

    def send_telegram_message(self, message):
        """Envia mensagem para o Telegram"""
        try:
            # url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
            # payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message}
            # requests.post(url, data=payload)
            self.telegram_bot.send_message(TELEGRAM_CHAT_ID, message)  # Usando o bot do Telegram
        except Exception as e:
            print(f"Erro ao enviar mensagem Telegram: {e}")
    ####### END - TELEGRAM BOT CONFIG ####################################################

    ####### START - TENTATIVA GESTÃO DE RISCO CONFIG ######################################
    def calculate_trade_size(self):
        """Calcula o tamanho da ordem baseado no saldo e risco por operação"""
        try:
            # Obtém o saldo disponível na moeda base (exemplo: USDT)
            balance = self.exchange.fetch_balance()
            available_balance = float(balance.get('USDT', {}).get('free', 0))  # Fallback para evitar erro

            if available_balance <= 0:
                self.send_telegram_message("⚠️ Saldo insuficiente para operar.")
                return 0

            # Calcula o risco por trade (exemplo: 2% do saldo disponível)
            risk_amount = available_balance * self.risk_per_trade

            # Obtém informações do mercado
            market_info = self.exchange.load_markets().get(self.symbol, {})
            min_trade_size = market_info.get('limits', {}).get('amount', {}).get('min', 0.001)  # Fallback mínimo

            # Obtém último preço do ativo
            last_price = self.exchange.fetch_ticker(self.symbol).get('last', 0)

            if last_price <= 0:
                self.send_telegram_message("⚠️ Erro ao obter preço do ativo. Ignorando operação.")
                return 0

            # Calcula tamanho da ordem
            trade_size = risk_amount / last_price

            # Ajusta precisão e verifica se está acima do mínimo
            trade_size = max(trade_size, min_trade_size)  # Garante um tamanho mínimo
            trade_size = self.exchange.amount_to_precision(self.symbol, trade_size)

            return float(trade_size)

        except Exception as e:
            self.send_telegram_message(f"❌ Erro ao calcular tamanho da ordem: {e}")
            return 0

    def update_pnl(self, pnl):
        """Atualiza o saldo e verifica os limites de perda e lucro"""
        self.daily_pnl += pnl
        self.current_balance += pnl

        if self.daily_pnl <= -self.max_daily_loss:
            self.send_telegram_message(f"🛑 Limite de perda diária atingido: ${self.daily_pnl:.2f}. Parando o bot.")
            self.bot_running = False
        elif self.daily_pnl >= self.daily_profit_target:
            self.send_telegram_message(f"✅ Meta de lucro diário atingida: ${self.daily_pnl:.2f}. Parando o bot.")
            self.bot_running = False
    ####### END - TENTATIVA GESTÃO DE RISCO CONFIG ######################################

    def check_order_execution(self, order_id: str) -> Optional[Dict]:
        """
        Verifica se uma ordem específica foi executada
        
        Ex.: TP ou SL atingidos.
        """
        try:
            order = self.exchange.fetch_order(order_id, self.symbol)
            return order if order['status'] == 'closed' else None
        except Exception:
            return None
        
    def check_balance(self, trade_size: float) -> bool:
        """Verifica se há saldo suficiente para executar ordens"""
        try:
            balance = self.exchange.fetch_balance()
            
            # Para compra, verifica USDT
            if not self.active_position or self.active_position['side'] == 'buy':
                available_usdt = float(balance['USDT']['free'])
                required_usdt = trade_size * float(self.exchange.fetch_ticker(self.symbol)['last'])
                
                if available_usdt < required_usdt:
                    print(f"Saldo USDT insuficiente. Disponível: {available_usdt}, Necessário: {required_usdt}")
                    return False
            
            # Para venda, verifica a moeda base (ex: DOGE)
            else:
                base_currency = self.symbol.split('/')[0]  # Ex: DOGE/USDT -> DOGE
                available_base = float(balance[base_currency]['free'])
                
                if available_base < trade_size:
                    print(f"Saldo {base_currency} insuficiente. Disponível: {available_base}, Necessário: {trade_size}")
                    return False
            
            return True
                
        except Exception as e:
            print(f"Erro ao verificar saldo: {e}")
            return False
    
    def check_active_orders(self) -> bool:
        """
        Verifica se existem ordens ativas (TP ou SL) para o símbolo
        Retorna True se existirem ordens ativas, False caso contrário

        Ou seja, verifica se há ordens pendentes (TP ou SL abertos) para evita criar ordens duplicadas.
        """
        try:
            open_orders = self.exchange.fetch_open_orders(self.symbol)
            return len(open_orders) > 0
        except Exception as e:
            self.send_telegram_message(f"Erro ao verificar ordens ativas: {e}")
            return False
    
    def check_position(self) -> None:
        """
        Verifica o status da posição atual.

        Ou seja, verifica se a posição ativa foi fechada (TP ou SL atingidos), para confirmar se pode abrir uma nova operação.
        """
        # Se não tiver uma posição, sai da função e volta para o main course.
        if not self.active_position:
            return
        
        try:
            current_price = self.exchange.fetch_ticker(self.symbol)['last']
            entry_price = self.active_position['entry_price']
            trade_size = self.active_position['trade_size']  
            
            # Verifica TP e SL diretamente
            tp_executed = self.check_order_execution(self.active_position['tp_order_id'])
            sl_executed = self.check_order_execution(self.active_position['sl_order_id'])
            
            if tp_executed: # Se TP foi executado
                profit = ((tp_executed['price'] - entry_price) / entry_price) * 100
                profit_absolute = (tp_executed['price'] - entry_price) * float(trade_size)
                
                if self.active_position['side'] == 'sell':
                    profit = -profit
                    profit_absolute = -profit_absolute
                
                self.send_telegram_message(
                    f"✅ TAKE PROFIT Executado!\n"
                    f"Entrada: ${entry_price:.4f}\n"
                    f"Saída: ${tp_executed['price']:.4f}\n"
                    f"Lucro: {profit:.2f}% (${profit_absolute:.2f})\n"
                    f"Quantidade: {float(trade_size)}"
                )
                
                # Atualiza o PNL do dia
                self.update_pnl(profit_absolute)

                # Atualiza o Active Position para None e Encerra todas as ordens ativas (TP e SL restantes)
                self.active_position = None
                self.cancel_all_orders()
                
            elif sl_executed: # Se o SL foi atingido
                loss = ((sl_executed['price'] - entry_price) / entry_price) * 100
                loss_absolute = (sl_executed['price'] - entry_price) * float(trade_size)
                
                if self.active_position['side'] == 'sell':
                    loss = -loss
                    loss_absolute = -loss_absolute
                
                self.send_telegram_message(
                    f"🛑 STOP LOSS Executado!\n"
                    f"Entrada: ${entry_price:.4f}\n"
                    f"Saída: ${sl_executed['price']:.4f}\n"
                    f"Perda: {loss:.2f}% (${loss_absolute:.2f})\n"
                    f"Quantidade: {float(trade_size)}"
                )

                # Atualiza o PNL do dia
                self.update_pnl(loss_absolute)

                # Atualiza o Active Position para None e Encerra todas as ordens ativas (TP e SL restantes)
                self.active_position = None
                self.cancel_all_orders()
            
            # Break-even logic - só executa se ainda houver ordens ativas
            elif (current_price >= self.active_position['entry_price'] * BREAK_EVEN_TRIGGER and
                  self.active_position['side'] == 'buy' and
                  self.check_active_orders()):
                self.move_stop_loss_to_breakeven(trade_size=trade_size)
            
        except Exception as e:
            self.send_telegram_message(f"Erro ao verificar posição: {e}")

    def move_stop_loss_to_breakeven(self, trade_size: float) -> None:
        """Move o stop loss para o preço de entrada"""
        try:
            # Cancela apenas as ordens de SL existentes
            open_orders = self.exchange.fetch_open_orders(self.symbol)
            for order in open_orders:
                if order['type'] == 'STOP_LOSS_LIMIT':
                    self.exchange.cancel_order(order['id'], self.symbol)
            
            entry_price = self.active_position['entry_price']
            
            # Cria nova ordem SL no break-even
            self.exchange.create_order(
                self.symbol,
                'STOP_LOSS_LIMIT',
                'sell',
                float(trade_size),
                entry_price * 0.999,
                {'stopPrice': entry_price}
            )
            
            self.send_telegram_message("Stop Loss movido para break-even")
            
        except Exception as e:
            self.send_telegram_message(f"Erro ao mover SL para break-even: {e}")

    ####### INDICADORES DE MERCADO ########################################################
    def get_rsi(self, timeframe='5m', period=14):
        """Calcula o RSI para o símbolo atual"""
        try:
            candles = self.exchange.fetch_ohlcv(self.symbol, timeframe, limit=period+1)
            df = pd.DataFrame(candles, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
            
            delta = df['close'].diff()
            gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
            loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
            
            rs = gain / loss
            rsi = 100 - (100 / (1 + rs))
            
            return rsi.iloc[-1]
        except Exception as e:
            self.send_telegram_message(f"Erro ao calcular RSI: {e}")
            return None

    def get_volume(self, timeframe='5m', period=5):
        """Calcula o volume médio para o símbolo atual"""
        try:
            candles = self.exchange.fetch_ohlcv(self.symbol, timeframe, limit=period)
            df = pd.DataFrame(candles, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
            return df['volume'].mean()
        except Exception as e:
            self.send_telegram_message(f"Erro ao calcular volume: {e}")
            return None
    
    def check_trend(self, period='1h'):
        """Verifica a tendência usando médias móveis"""
        try:
            candles = self.exchange.fetch_ohlcv(self.symbol, period, limit=100)
            df = pd.DataFrame(candles, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
            
            # Médias móveis de 20 e 50 períodos
            df['MA20'] = df['close'].rolling(window=20).mean()
            df['MA50'] = df['close'].rolling(window=50).mean()
            
            # Tendência de alta: MA20 > MA50
            is_uptrend = df['MA20'].iloc[-1] > df['MA50'].iloc[-1]
            
            return is_uptrend
        except Exception as e:
            print(f"Erro ao verificar tendência: {e}")
            return None
    ####### FIM INDICADORES DE MERCADO ####################################################
    
    def place_trade(self, side: str, price: float, trade_size: float) -> None:
        """Executa uma nova operação com gestão de ordens"""
        try:
            if self.simulation_mode:
                self.send_telegram_message(f"[SIMULAÇÃO] {side.upper()} {trade_size} {self.symbol} a {price}")
                return
            
            print(f"\n🏁 Iniciando operação {side} de {float(trade_size)} {self.symbol} a {price}")

            if self.active_position or self.check_active_orders():
                self.send_telegram_message("❌ Já existe uma posição ativa ou ordens abertas. Ignorando novo sinal.")
                return

            if not self.check_balance(trade_size):
                self.send_telegram_message("❌ Saldo insuficiente para executar ordem")
                return
            
            # Cria ordem de mercado
            order = self.exchange.create_order(
                symbol=self.symbol,
                type='market',
                side=side,
                amount=float(trade_size),
                params={'newOrderRespType': 'FULL'}
            )

            if not order or 'status' not in order or order['status'] != 'closed':
                self.send_telegram_message(f"❌ Ordem principal não foi executada corretamente: {order}")
                return
            
            # Pega o preço real de execução
            executed_price = float(order.get('average', order.get('price', float(price))))
            print(f"Ordem executada a {executed_price}")

            if order and 'id' in order:
                self.active_position = {
                    'side': side,
                    'entry_price': executed_price,
                    'order_id': order['id'],
                    'trade_size': trade_size
                }
                
                # Calcula preços TP/SL
                if side == 'buy':
                    tp_price = executed_price * float(TAKE_PROFIT)
                    sl_price = executed_price * float(STOP_LOSS)
                    tp_side = 'sell'
                    sl_side = 'sell'
                else:
                    tp_price = executed_price * float(STOP_LOSS)  # Invertido para venda
                    sl_price = executed_price * float(TAKE_PROFIT)  # Invertido para venda
                    tp_side = 'buy'
                    sl_side = 'buy'
                
                # Ajusta preços para evitar erros de precisão
                tp_price = self.exchange.price_to_precision(self.symbol, tp_price)
                sl_price = self.exchange.price_to_precision(self.symbol, sl_price)
                trade_size_amount = self.exchange.amount_to_precision(self.symbol, trade_size)
                
                print(f"📈 Criando TP: {tp_side} {trade_size_amount} {self.symbol} @ {tp_price}")
                print(f"📉 Criando SL: {sl_side} {trade_size_amount} {self.symbol} @ {sl_price}")

                # Coloca as ordens de TP
                tp_order = self.exchange.create_order(
                    symbol=self.symbol,
                    type='TAKE_PROFIT_LIMIT',
                    side=tp_side,
                    amount=float(trade_size_amount),
                    price=float(tp_price),
                    params={'stopPrice': float(tp_price)}
                )
                
                # Coloca as ordens de SL
                sl_price_adjusted = float(sl_price) * (0.999 if side == 'buy' else 1.001)
                sl_order = self.exchange.create_order(
                    symbol=self.symbol,
                    type='STOP_LOSS_LIMIT',
                    side=sl_side,
                    amount=float(trade_size_amount),
                    price=float(sl_price_adjusted),
                    params={'stopPrice': float(sl_price)}
                )
                
                self.active_position.update({
                    'tp_order_id': tp_order['id'],
                    'sl_order_id': sl_order['id'],
                })

                # Calcula percentual de lucro e risco
                profit_target = (float(tp_price) - executed_price) / executed_price * 100
                loss_risk = (executed_price - float(sl_price)) / executed_price * 100
                
                if side == 'sell':
                    profit_target = -profit_target
                    loss_risk = -loss_risk
            
                # Checagem final para confirmar se TP e SL foram criados
                time.sleep(1)  # Pequeno delay para evitar erros de API
                open_orders = self.exchange.fetch_open_orders(self.symbol)

                # Confirma se TP e SL foram criados
                tp_created = any(o['id'] == tp_order['id'] for o in open_orders)
                sl_created = any(o['id'] == sl_order['id'] for o in open_orders)

                if not tp_created or not sl_created:
                    self.send_telegram_message("🚨 ERRO: TP ou SL não foram criados corretamente. Cancelando ordem principal.")
                    self.cancel_all_orders()
                    self.active_position = None
                    return

                self.send_telegram_message(
                    f"📌 Nova posição: {side.upper()} {self.symbol}\n"
                    f"💰 Preço: ${executed_price:.4f}\n"
                    f"📈 TP: ${tp_price:.4f} (+{profit_target:.2f}%)\n"
                    f"📉 SL: ${sl_price:.4f} (-{loss_risk:.2f}%)\n"
                    f"📦 Quantidade: {trade_size_amount}"
                )
                
        except Exception as e:
            print(f"❌ Erro detalhado ao executar ordem: {str(e)}")
            self.send_telegram_message(f"❌ Erro ao executar ordem: {str(e)}")
            self.active_position = None

    def trade(self):
        """Executa a lógica principal de trading"""
        try:
            if not self.bot_running:
                print("🤖🙉 Bot está parado. Ignorando novos sinais.")
                return
            
            # Verifica se atingiu a meta diária ou limite de perda
            if self.daily_pnl <= -self.max_daily_loss:
                self.send_telegram_message(f"🛑 Limite de perda diária atingido: ${self.daily_pnl:.2f}. Parando o bot e Fechando todas as posições.")
                self.cancel_all_orders()
                self.active_position = None
                self.bot_running = False
                return
            
            if self.daily_pnl >= self.daily_profit_target:
                self.send_telegram_message(f"✅ Meta de lucro diário atingida: ${self.daily_pnl:.2f}. Parando o bot.")
                self.bot_running = False
                return

            # Ajusta tamanho da ordem baseado no saldo
            trade_size = self.calculate_trade_size()
            if trade_size < 1:
                self.send_telegram_message("🚨 Valor muito baixo para operar. Aguardando saldo aumentar.")
                return

            # Verifica posição atual primeiro, para saber se podemos abrir uma nova posição.
            self.check_position()

            if self.active_position:
                # Caso não tenha entrado nas condições da função acima, a posição ainda está ativa.
                print("🛑 Posição já ativa. Aguardando fechamento antes de abrir nova operação.")
                return
            
            # Obtem indicadores do mercado: RSI, Volume e Tendência
            rsi = self.get_rsi()
            price = self.exchange.fetch_ticker(self.symbol)['last']
            volume = self.get_volume()
            
            # Se houver erro nos dados, ignora a iteração
            if rsi is None or volume is None:
                return
        
            is_uptrend = self.check_trend()  # Verifica tendência de alta (check_trend retorna True -> uptrend & False -> downtrend)

            print(f"🔎 RSI: {rsi:.2f}, Volume: {volume:.2f} e UpTrend: {is_uptrend} - {self.symbol} a {price}")

            if self.prev_rsi and self.prev_rsi >= 30 and rsi < 30 and volume > MIN_VOLUME_THRESHOLD and is_uptrend:
                self.place_trade('buy', price, trade_size)
            elif self.prev_rsi and self.prev_rsi <= 70 and rsi > 70 and volume > MIN_VOLUME_THRESHOLD and not is_uptrend:
                self.place_trade('sell', price, trade_size)
            
            # Atualiza o RSI anterior
            self.prev_rsi = rsi
        except Exception as e:
            self.send_telegram_message(f"Erro na execução principal: {e}")

    def cancel_all_orders(self) -> None:
        """
        Cancela todas as ordens ativas manualmente, independente do estado.

        Retorna True se conseguiu cancelar todas as ordens.
        """
        try:
            # Cancela todas as ordens abertas
            self.exchange.cancel_all_orders(self.symbol)
            
            # Reseta o estado do bot
            self.active_position = None
            
            self.send_telegram_message(f"🚫 Todas as ordens restantes foram canceladas para {self.symbol}")
            print(f"Todas as ordens canceladas para {self.symbol}")
            
        except Exception as e:
            error_msg = f"Erro ao cancelar ordens: {e}"
            self.send_telegram_message(error_msg)
            print(error_msg)

# Configurações
MIN_VOLUME_THRESHOLD = 50000 # Volume mínimo para operar -> 50k
TAKE_PROFIT = 1.021
STOP_LOSS = 0.99
BREAK_EVEN_TRIGGER = 1.007

bot = TradingBot(
    symbol="XRP/USDT",
    initial_balance=40,
    risk_per_trade=0.25,
    max_drawdown=0.15,
    daily_profit_target=0.30,
    simulation_mode=False
)

while True:
    bot.trade()
    time.sleep(20)