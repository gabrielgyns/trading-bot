import asyncio
import json
import threading
import websockets
import ccxt
import time
import os
import pandas as pd
import telebot
from dotenv import load_dotenv
from typing import Optional, Dict, Any
from telebot.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton

load_dotenv()

API_KEY = os.getenv("API_KEY")
API_SECRET = os.getenv("API_SECRET")
API_KEY_TESTNET = os.getenv("API_KEY_TESTNET")
API_SECRET_TESTNET = os.getenv("API_SECRET_TESTNET")
URL_API_TESTNET = os.getenv("URL_API_TESTNET")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
BINANCE_WS_URL = os.getenv("BINANCE_WS_URL")

# Configurações
SIMULATION_MODE = False  # Ativa o modo simulado
MIN_VOLUME_THRESHOLD = 50000 # Volume mínimo para operar -> 50k
TAKE_PROFIT = 1.021
STOP_LOSS = 0.99
BREAK_EVEN_TRIGGER = 1.007
TAKE_PROFIT_RATIO = 2.0  # Risco:Recompensa de 2:1
RISK_PER_TRADE = 0.05  # Risco de 5% da banca por operação
PAR_SYMBOL, QUANTIDADE_OPERACAO = "XRP/USDT", 5 # 5 itens
# PAR_SYMBOL, QUANTIDADE_OPERACAO = "ADA/USDT", 10 # 10


class TradingBot:
    def __init__(self, symbol: str, initial_balance: float, websocket_client, risk_per_trade: float = 0.02, max_drawdown: float = 0.1, daily_profit_target: float = 0.3, simulation_mode: bool = True):
        self.symbol = symbol            # Símbolo do par de trading
        self.active_position: Optional[Dict[str, Any]] = None   # Posição ativa (None se não houver)
        
        self.ws = websocket_client  # Instância do WebSocket
        self.bot_running = False    # Iniciar o bot desligado
        self.simulation_mode = simulation_mode      # Modo de simulação

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
            print(f"🚀 Conexão estabelecida com {'testnet' if self.simulation_mode else 'produção'}")

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
            # KeyboardButton('/trocar_par'),
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
        def cancel_orders_confirmation(message):
            # Cria botões inline de confirmação
            markup = InlineKeyboardMarkup()
            markup.row(
                InlineKeyboardButton("✅ Sim", callback_data="cancel_confirm"),
                InlineKeyboardButton("❌ Não", callback_data="cancel_deny")
            )
            
            self.telegram_bot.reply_to(
                message,
                "⚠️ Tem certeza que deseja cancelar todas as ordens?",
                reply_markup=markup
            )
        
        # Handler para os botões de confirmação
        @self.telegram_bot.callback_query_handler(func=lambda call: True)
        def callback_handler(call):
            if call.data == "cancel_confirm":
                # Executa o cancelamento
                self.cancel_all_orders()
                self.telegram_bot.edit_message_text(
                    "✅ Ordens canceladas com sucesso!",
                    chat_id=call.message.chat.id,
                    message_id=call.message.message_id
                )
            elif call.data == "cancel_deny":
                # Cancela a operação
                self.telegram_bot.edit_message_text(
                    "❌ Operação cancelada!",
                    chat_id=call.message.chat.id,
                    message_id=call.message.message_id
                )
            
        @self.telegram_bot.message_handler(commands=['status'])
        def get_status(message):
            self.send_status()
            
        @self.telegram_bot.message_handler(commands=['posicao'])
        def get_position(message):
            self.send_position_info()

        @self.telegram_bot.message_handler(commands=['resultados_do_dia'])
        def get_pnl_day(message):
            self.send_daily_pnl()
        
        @self.telegram_bot.message_handler(commands=['trocar_par'])
        def change_pair(message):
            try:
                # O formato esperado é: /trocar_par BTC/USDT
                parts = message.text.split()
                if len(parts) != 2:
                    self.send_telegram_message("❌ Formato incorreto. Use: /trocar_par SYMBOL/USDT")
                    return
                    
                new_symbol = parts[1].upper()
                # Valida se o par existe
                try:
                    self.exchange.fetch_ticker(new_symbol)
                    self.change_symbol(new_symbol)
                except Exception as e:
                    self.send_telegram_message(f"❌ Par inválido ou não suportado: {new_symbol}")
                    
            except Exception as e:
                self.send_telegram_message(f"❌ Erro ao processar comando: {e}")
                
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
            /trocar_par SYMBOL/USDT - Troca o par de trading (ex: /trocar_par BTC/USDT)
            /ajuda - Mostra esta mensagem
            """
            self.telegram_bot.reply_to(message, help_text)

        def polling():
            try:
                self.telegram_bot.polling(none_stop=True)
            except Exception as e:
                print(f"Erro no polling do Telegram: {e}")
                
        threading.Thread(target=polling, daemon=True).start()

    def send_status(self):
        """Envia status atual do bot"""
        try:
            price = self.ws.get_price()
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
            current_price = self.ws.get_price()
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
    
    def change_symbol(self, new_symbol: str):
        """Troca o par de trading"""
        try:
            # Primeiro cancela todas as ordens existentes
            self.cancel_all_orders()
            
            # Salva o estado anterior do bot
            was_running = self.bot_running
            
            # Para o bot temporariamente
            self.bot_running = False
            
            # Atualiza o símbolo
            self.symbol = new_symbol.upper()
            
            # Atualiza o WebSocket
            self.ws.change_symbol(new_symbol.lower().replace("/", ""))
            
            # Restaura o estado do bot
            self.bot_running = was_running
            
            self.send_telegram_message(f"✅ Par alterado para {new_symbol}")
            print(f"Par alterado para {new_symbol}")
            
        except Exception as e:
            self.send_telegram_message(f"❌ Erro ao trocar par: {e}")
            print(f"Erro ao trocar par: {e}")

    ####### END - TELEGRAM BOT CONFIG ####################################################

    ####### START - TENTATIVA GESTÃO DE RISCO CONFIG ######################################
    # def calculate_trade_size(self):
    #     balance = self.exchange.fetch_balance().get('USDT', {}).get('free', 0)
    #     risk_amount = balance * RISK_PER_TRADE
    #     last_price = self.ws.get_price()
    #     trade_size = risk_amount / last_price if last_price else 0
    #     return max(trade_size, 0.1)

    def calculate_trade_size(self):
        """Calcula o tamanho da ordem com base no risco e no saldo disponível"""
        # Obtém o saldo de USDT e outras moedas
        balance = self.exchange.fetch_balance()

        usdt_balance = balance.get('USDT', {}).get('free', 0)
        xrp_balance = balance.get('XRP', {}).get('free', 0)
        
        # Imprime os saldos para debug
        print(f"Saldo USDT disponível: {usdt_balance}")
        print(f"Saldo XRP disponível: {xrp_balance}")
        
        # Caso o saldo de USDT seja suficiente, use-o
        if usdt_balance > 0:
            available_balance = usdt_balance
        else:
            # Se não houver USDT suficiente, use o saldo de XRP
            price_xrp = self.ws.get_price()  # Preço atual do XRP
            available_balance = xrp_balance * price_xrp  # Converte o XRP para USDT

        # Calcula o risco baseado no saldo disponível
        risk_amount = available_balance * RISK_PER_TRADE
        last_price = self.ws.get_price()

        if last_price is None:
            return 0  # Retorna 0 se o preço não for obtido

        # Calcula o tamanho da ordem baseado no risco e preço atual
        trade_size = risk_amount / last_price

        # Verifica o valor mínimo de transação permitido pela Binance
        market = self.exchange.market(self.symbol)
        min_notional = market['limits']['cost']['min']

        # Calcula o valor total da transação
        total_value = trade_size * last_price

        # Ajusta o tamanho da ordem para garantir que atenda ao valor mínimo
        if total_value < min_notional:
            # Se o valor da transação for abaixo do mínimo, ajusta o trade_size
            trade_size = min_notional / last_price
            print(f"⚠️ Ajustando trade_size para o valor mínimo permitido: {trade_size}")
        
        # Garantir que o trade_size não seja muito pequeno
        return max(trade_size, 0.1)  # Garante que a ordem tenha pelo menos 0.1 moeda

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
                required_usdt = trade_size * float(self.ws.get_price())
                
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
            current_price = self.ws.get_price()
            if current_price is None:
                print("⏳ Aguardando preço do WebSocket para verificar posição...")
                return
            
            entry_price = self.active_position['entry_price']
            trade_size = self.active_position['trade_size']  
            
            # Verifica TP e SL diretamente
            tp_executed = self.check_order_execution(self.active_position['tp_order_id'])
            sl_executed = self.check_order_execution(self.active_position['sl_order_id'])
            
            if tp_executed: # Se TP foi executado
                profit = ((tp_executed['price'] - entry_price) / entry_price) * 100
                profit_absolute = (tp_executed['price'] - entry_price) * trade_size
                
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
    
    def get_indicators(self, timeframe='5m', period=14):
        # Obtém as velas (OHLCV)
        candles = self.exchange.fetch_ohlcv(self.symbol, timeframe, limit=period+1)
        df = pd.DataFrame(candles, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        
        df['EMA9'] = df['close'].ewm(span=9, adjust=False).mean()

        # Cálculo do RSI
        delta = df['close'].diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
        df['RSI'] = 100 - (100 / (1 + gain / loss))

        # Cálculo do ATR (Average True Range)
        df['ATR'] = df['high'] - df['low']  # ATR simples, você pode usar uma média ou fórmula ajustada aqui
        df['ATR'] = df['ATR'].rolling(window=period).mean()  # Média para suavizar
        
        # Cálculo do MACD
        df['EMA_fast'] = df['close'].ewm(span=12, adjust=False).mean()  # EMA de curto prazo
        df['EMA_slow'] = df['close'].ewm(span=26, adjust=False).mean()  # EMA de longo prazo
        df['MACD'] = df['EMA_fast'] - df['EMA_slow']  # Diferença entre as EMAs
        df['Signal_Line'] = df['MACD'].ewm(span=9, adjust=False).mean()  # Linha de sinal do MACD

        # Retorna os últimos valores
        return {
            'RSI': df['RSI'].iloc[-1],
            'ATR': df['ATR'].iloc[-1],
            'MACD': df['MACD'].iloc[-1],
            'Signal_Line': df['Signal_Line'].iloc[-1],
            'volume': df['volume'].iloc[-1]  # Garantindo que o volume seja retornado
        }
    ####### FIM INDICADORES DE MERCADO ####################################################
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

    def place_trade(self, side: str, price: float, trade_size: float, atr: float) -> None:
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
                    tp_price = executed_price + (atr * TAKE_PROFIT_RATIO)
                    sl_price = executed_price - atr
                    tp_side = 'sell'
                    sl_side = 'sell'
                else:
                    tp_price = executed_price - (atr * TAKE_PROFIT_RATIO)  # Invertido para venda
                    sl_price = executed_price + atr  # Invertido para venda
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

    def trade(self, price: float) -> None:
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
            trade_size = QUANTIDADE_OPERACAO # self.calculate_trade_size() # TODO: Corrigir depois esse trade size.
            if trade_size < 0.1:
                self.send_telegram_message("🚨 Valor de ordem abaixo do mínimo permitido. Aguardando saldo aumentar.")
                return

            # Verifica posição atual primeiro, para saber se podemos abrir uma nova posição.
            self.check_position()

            if self.active_position:
                # Caso não tenha entrado nas condições da função acima, a posição ainda está ativa.
                print("🛑 Posição já ativa. Aguardando fechamento antes de abrir nova operação.")
                return
            
            # Obtem indicadores do mercado: RSI, Volume e Tendência
            indicators = self.get_indicators()

            # Se houver erro nos dados, ignora a iteração
            if indicators['RSI'] is None or indicators['volume'] is None:
                print("⚠️ Dados de mercado inválidos. Ignorando esta iteração.")
                return

            print(f"🔎 RSI: {indicators['RSI']:.2f}, Volume: {indicators['volume']:.2f}, MACD: {indicators['MACD']:.4f}, Signal Line: {indicators['Signal_Line']:.4f}, ATR: {indicators['ATR']} - {self.symbol} a {price}")

            if indicators['RSI'] < 30 and indicators['volume'] > MIN_VOLUME_THRESHOLD: # Usar ATR > 0 ?
            # if indicators['RSI'] < 30 and indicators['MACD'] > indicators['Signal_Line']: # Usar ATR > 0 ?
                self.place_trade('buy', price, trade_size, indicators['ATR'])
            elif indicators['RSI'] > 70 and indicators['volume'] > MIN_VOLUME_THRESHOLD: # Usar ATR > 0 ?
            # elif indicators['RSI'] > 70 and indicators['MACD'] < indicators['Signal_Line']: # Usar ATR > 0 ?
                self.place_trade('sell', price, trade_size, indicators['ATR'])

        except Exception as e:
            self.send_telegram_message(f"Erro na execução principal: {e}")
    
    async def run(self):
        """Executa o loop principal do bot"""
        print("🔄 Iniciando loop principal...")
        while self.bot_running:
            try:
                price = self.ws.get_price()

                if price:
                    self.trade(price)

                await asyncio.sleep(1)  # Delay entre iterações
            except Exception as e:
                print(f"❌ Erro no loop principal: {e}")
                await asyncio.sleep(5)  # Delay maior em caso de erro

class BinanceWebSocket:
    def __init__(self, symbol: str):
        self.symbol = symbol.lower().replace("/", "")
        self.price = None
        self.ws_url = f"wss://stream.binance.com:9443/ws/{self.symbol}@ticker"

    async def connect(self):
        """Conecta ao WebSocket da Binance"""
        while True:
            try:
                async with websockets.connect(self.ws_url) as websocket:
                    async for message in websocket:
                        data = json.loads(message)
                        self.price = float(data["c"])

            except Exception as e:
                print(f"⚠️ Erro WebSocket: {e}")
                await asyncio.sleep(5)
                
    def get_price(self):
        """Retorna o preço atualizado pela WebSocket"""
        return self.price

    def change_symbol(self, new_symbol: str):
        """Troca o símbolo do WebSocket"""
        self.symbol = new_symbol
        self.ws_url = f"wss://stream.binance.com:9443/ws/{self.symbol}@ticker"
        self.price = None  # Reseta o preço


async def main():
    print("🚀 Iniciando sistema...")

    # Inicializa WebSocket
    ws = BinanceWebSocket(PAR_SYMBOL)
    print("📡 WebSocket inicializado")
    # threading.Thread(target=lambda: asyncio.run(ws.connect()), daemon=True).start()

    # Inicializa Bot
    bot = TradingBot(
        symbol=PAR_SYMBOL,
        websocket_client=ws,
        initial_balance=40,
        risk_per_trade=0.25,
        max_drawdown=0.15,
        daily_profit_target=0.30,
        simulation_mode=False
    )
    print("🤖 Bot inicializado")

    # Ativa o bot
    bot.bot_running = True
    print("✅ Bot ativado")

    try:
        await asyncio.gather(
            ws.connect(),
            bot.run()
        )
    except KeyboardInterrupt:
        print("\n🛑 Encerrando o bot...")
    except Exception as e:
        print(f"❌ Erro: {e}")
    finally:
        bot.bot_running = False

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n👋 Bot encerrado pelo usuário")
