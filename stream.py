from abc import abstractmethod
import websocket
import json
import threading
import traceback
import queue
from time import sleep, time
from collections import defaultdict

import config
from db import DB
from event import MarketEvent

class OutofDataError(Exception):
  pass

class TickerObject(object):
  def __init__(self):
    self.hod = None
    self.lod = None
    self.price = None
    self.bars = []

  def update_price(self, price, timestamp):
    self.price = price
    if not self.hod or price > self.hod[0]:
      self.hod = (price, timestamp)
    if not self.lod or price < self.lod[0]:
      self.lod = (price, timestamp)

  def add_bar(self, bar):
    if not self.hod or bar['h'] > self.hod[0]:
      self.hod = (bar['h'], bar['t'])
    if not self.lod or bar['l'] < self.lod[0]:
      self.lod = (bar['l'], bar['t'])

    self.bars.append(bar)


class DataHandler(object):
  def __init__(self, events):
    self.db = DB(config.db_file)
    self.events = events
    self.tickers = defaultdict(TickerObject)

  def get_latest_bars(self, ticker, N=1):
    # print(self.tickers[ticker].bars)
    return self.tickers[ticker].bars[-N:]
  
  @abstractmethod
  def update_price(self):
    raise NotImplementedError('update_price() should be implemented')

class HistoricalDataStreamer(DataHandler):
  def __init__(self, events, type, tickers, start, end):
      super().__init__(events)
      self.type = type
      self.data = queue.Queue()
      self.index = -1
      self.load_data(type, tickers, start, end)

  def get_latest_bars(self, ticker, N=1):
    return self.tickers[ticker].bars[self.index-N+1:self.index+1]

  def update_price(self):
    if self.type == 'bars':
      self.index += 1
      if self.index < self.len:
        self.events.put(MarketEvent('bars'))
      else:
        raise OutofDataError

  def load_data(self, type, tickers, start, end):
    if type == 'bars':
      self.len = 0
      for ticker in tickers:
        bars = self.fix_bars(self.db.get_bars([ticker], start, end))
        self.tickers[ticker].bars = bars
        if len(bars) > self.len:
          self.len = len(bars)
      self.data.queue = queue.deque(bars)
  
  def fix_bars(self, bars):
    ret = []
    for bar in bars:
      a = {
        'o': bar[2],
        'h': bar[3],
        'l': bar[4],
        'c': bar[5],
        'v': bar[6],
        't': bar[7],
      }
      ret.append(a)
    return ret


class LiveDataStreamer(DataHandler):
  def __init__(self, events):
    super().__init__(events)
    self.ws = websocket.WebSocketApp(config.ws_url, on_open=self.on_open, on_message=self.on_message, on_close=self.on_close)
    self.wst = threading.Thread(target=self.ws.run_forever)
    self.wst.daemon = True
    self.new_bar = False
    self.new_trade = False

  def update_price(self):
    if self.new_bar and (time() - self.new_bar) > 2:
      print(time())
      print(self.new_bar)
      print(time() - self.new_bar)
      self.new_bar = False
      self.events.put(MarketEvent('bars'))
    if self.new_trade:
      self.new_trade = False
      self.events.put(MarketEvent('trade'))

  def on_open(self, ws):
    print("Live WS opened")
    auth_data = {"action": "auth", "key": config.api_key, "secret": config.api_secret}

    ws.send(json.dumps(auth_data))

    subscribe_message = {"action": "subscribe", "trades": ["AAPL","TSLA","BIDU","ROKU"], "bars": config.tickers}

    ws.send(json.dumps(subscribe_message))

  def on_message(self, ws, message):
    message = json.loads(message)
    self.handle_message(message)

  def on_close(self, ws):
    print("closed connection")

  def subscribe_to_ticker(self, tickers):
    subscribe_message = {"action": "subscribe", "trades": tickers}
    for ticker in tickers:
      self.prices[ticker]
    self.ws.send(json.dumps(subscribe_message))

  def unsubscribe_to_ticker(self, tickers):
    unsubscribe_message = {"action": "unsubscribe", "trades": tickers}
    self.ws.send(json.dumps(unsubscribe_message))

  def handle_message(self, message):
    for event in message:
      type = event['T']
      if type == 't':
        self.handle_trade(event)
      elif type == 'b':
        self.handle_bar(event)

  def handle_trade(self, trade):
    try:
      # print('Handling trade for ' + trade['S'])
      trade_obj = (trade['S'], trade['p'], trade['s'], trade['t'])
      self.db.add_trade(trade_obj)
      self.tickers[trade['S']].update_price(trade['p'], trade['t'])
      self.new_trade = True
    except Exception as e:
      print('error')
      print(traceback.print_exc())


  def handle_bar(self, bar):
    try:
      # print('Handling bar for ' + bar['S'])
      s = bar['S']
      bar_obj = (bar['o'], bar['h'], bar['l'], bar['c'], bar['v'], bar['t'])
      self.db.add_bar((s, *bar_obj))
      b = {
        'o': bar['o'],
        'h': bar['h'],
        'l': bar['l'],
        'c': bar['c'],
        't': bar['t']
      }
      self.tickers[s].add_bar(b)
      if not self.new_bar:
        print('new bar is here')
      self.new_bar = time()
    except Exception as e:
      print('error')
      print(traceback.print_exc())

  def run(self):
    self.wst.start()

if __name__ == '__main__':
  events = None
  stream = LiveDataStreamer(events)
  stream.run()

  conn_timeout = 5
  while not stream.ws.sock.connected and conn_timeout:
    sleep(1)
    conn_timeout -= 1

  msg_counter = 0
  while stream.ws.sock.connected:
    sleep(1)
