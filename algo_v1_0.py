import logging
import math

from typing import Tuple, Optional
from ib_insync import IB, Contract, ContFuture, MarketOrder, FuturesOption, Trade, PriceCondition
from tenacity import retry, wait_fixed
from datetime import datetime, timedelta, date
from argparse import ArgumentParser
from tabulate import tabulate


OPEN_ORDERS_CHECK_PERIOD = timedelta(minutes=1)  # how often the script checks for open orders(if there is any,
                                                # and the script can't continue when there is someting)
ORDER_PLACE_MAX_DATE = timedelta(days=30)  # how far the script can place orders from the first date started this day
TRADING_DAYS_OF_WEEK = [0, 2, 4]  # days of week good to trade our options
STRIKE_PRICE_WAIT_PERIOD = timedelta(seconds=10)  # waiting period for one step for getting suitable strike price
STRIKE_PRICE_MAX_WAIT_PERIOD = timedelta(minutes=2)  # total waiting period for getting suitable strike price
GENERIC_WAIT_TIME = timedelta(seconds=2) # some actions need a small break after the previous action,
                                        # just to load the info. To avoid setting strict numbers in the script
                                        # and to make it a bit easier to adjust, it's set in a variable here
CONTRACTS_NUMBER = 1 # number of contracts to place
TRIAL_ACCOUNT = True # set delayed data
TRIAL_ROUNDING = True # set False here so it doesn't round + 10
BANK_HOLIDAYS_DATES = [date(2021, 9, 6), date(2021, 10, 10), date(2021, 11, 11),
                       date(2021, 11, 24), date(2021, 12, 26)]
PERIOD_TO_WAIT_FOR_THE_NEXT_DAY = timedelta(hours=1)  # if ORDER_PLACE_MAX_DATE goes too far,
                                                    # this parameter is used to wait for tomorrow to move on
DATE_OF_TRADE = datetime.today().date()


class ConnectionError(Exception):
    pass


class NoSuitablePrice(Exception):
    pass


class NoSuitableDate(Exception):
    pass


def connect():
    """
    Connect to API, switch to delayed info (for trial account)
    :return:
    """

    ib = IB()
    ib.connect()
    if not ib.isConnected():
        raise ConnectionError('Cannot establish connection')

    if TRIAL_ACCOUNT:
        ib.reqMarketDataType(3)

    return ib


ib = connect()


@retry(wait=wait_fixed(int(PERIOD_TO_WAIT_FOR_THE_NEXT_DAY.total_seconds())))
def get_next_available_date(start_date: date) -> date:

    """
    Find the next available date for trade
    :return: date in datetime format
    """

    log.info(f'Searching for suitable date, starting from {start_date}')

    for i in range(7):

        candidate_date = start_date + timedelta(days=i)

        if candidate_date > datetime.today().date() + ORDER_PLACE_MAX_DATE:
            raise NoSuitableDate('No suitable days left. Waiting till the next day.')

        if candidate_date in BANK_HOLIDAYS_DATES:
            log.info(f'{candidate_date} is a bank holiday, moving to the next day. ')
            continue

        if candidate_date.weekday() in TRADING_DAYS_OF_WEEK:
            log.info(f'The closest possible date is {candidate_date}')
            return candidate_date

    assert False, 'Could not find available date'


def create_reference() -> Tuple[Contract, int]:
    """
    Creates futures contract needed for reference
    :return: Tuple consisting of a Contract object and integer of this contract rounded price
    """
    reference_futures_contract = ContFuture('ES', 'GLOBEX')
    ib.qualifyContracts(reference_futures_contract)
    reference_futures_ticker = ib.reqMktData(reference_futures_contract)
    reference_price = reference_futures_ticker.marketPrice()
    while math.isnan(reference_price):
        ib.sleep(GENERIC_WAIT_TIME.total_seconds())
        reference_price = reference_futures_ticker.marketPrice()

    for i in range(int(STRIKE_PRICE_MAX_WAIT_PERIOD / STRIKE_PRICE_WAIT_PERIOD)):
        reference_price = reference_futures_ticker.marketPrice()
        reference_price_rounded = 5 * round(reference_price / 5)
        if TRIAL_ROUNDING:
            reference_price_rounded += 10
        if reference_price_rounded > reference_price:
            log.info(f'Good to go, reference futures price is {reference_price}, rounded is {reference_price_rounded}')
            return reference_futures_contract, reference_price_rounded
        log.info(f'The current price {reference_price} does not allow to trade now, waiting.')
        ib.sleep(STRIKE_PRICE_WAIT_PERIOD.total_seconds())
    raise NoSuitablePrice('Can not find suitable price')


def create_and_trade_contract(date: date, strike_price: int) -> Trade:
    """

    :param date: Date, available for trading, from get_available_date()
    :param strike_price: Price of futures contract, used as a reference price and strike price for Future Option,
    from create_reference() function
    :return: a Trade object
    """
    date_formatted = date.strftime('%Y%m%d')
    option_contract = FuturesOption('ES', date_formatted, strike_price, 'C', 'GLOBEX')
    ib.qualifyContracts(option_contract)

    option_contract_order = MarketOrder('SELL', CONTRACTS_NUMBER, outsideRth=True)
    option_trade = ib.placeOrder(option_contract, option_contract_order)

    return option_trade


def set_option_trade(date_of_trade: date):
    """
    Sets a trade via create_and_trade_contract function, waits for it to get filled and once it does, sets a stop-loss
    :return:
    """

    reference_contract, strike_price = create_reference()

    option_trade = create_and_trade_contract(date_of_trade, strike_price)
    log.info(f'Order placed: {option_trade.order}')

    option_status = option_trade.orderStatus.status
    while option_status != 'Filled':
        ib.sleep(GENERIC_WAIT_TIME.total_seconds())
        option_status = option_trade.orderStatus.status
    log.info(f'Short order filled, setting stop-loss')
    set_stop_loss(date_of_trade, reference_contract, strike_price)


def calculate_stop_loss_price(price: int) -> float:
    """
    Calculates stop-loss price by given formula
    :param price: rounded price from get_reference function
    :return: float of stop-loss price
    """
    stop_loss_price = price + 4.75
    return stop_loss_price


def set_stop_loss(date_of_trade: date, reference_contract, strike_price):
    """
    Sets an order on the opposite side to close the trade, once the reference futures price gets to a specific value
    :param date_of_trade: date of the short contract from get_available_date()
    :param reference_contract: data of the reference futures contract from get_reference function
    :param strike_price: strike price of the actual Futures Option contract(same as reference futures contract price)
    :return:
    """

    date_formatted = date_of_trade.strftime('%Y%m%d')
    stop_loss_price = calculate_stop_loss_price(strike_price)

    price_condition = PriceCondition(
        price=stop_loss_price,
        conId=reference_contract.conId,
        exch=reference_contract.exchange
    )

    long_contract = FuturesOption('ES', date_formatted, strike_price, 'C', 'GLOBEX')
    ib.qualifyContracts(long_contract)

    long_option_order = MarketOrder('BUY', CONTRACTS_NUMBER, outsideRth=True)
    long_option_order.conditions.append(price_condition)
    ib.placeOrder(long_contract, long_option_order)


def main():
    """
    Checks for open orders, and if there are none, runs the process of setting a trade
    :return:
    """
    date_of_trade = get_next_available_date(datetime.today().date())
    while True:
        if ib.reqOpenOrders():
            log.info('There is an open order currently, can not place more')
            ib.sleep(
                int(OPEN_ORDERS_CHECK_PERIOD.total_seconds())
            )
        else:
            set_option_trade(date_of_trade)
            date_of_trade = get_next_available_date(date_of_trade + timedelta(days=1))


def show_report():
    """
    Shows a list of latest traded contracts. Showed info: contract id, trade date, strike price
    :return:
    """
    recent_trades = ib.trades()
    table = [
                [i.contract.conId, i.contract.lastTradeDateOrContractMonth, i.contract.strike] for i in recent_trades
            ]
    headers = ['Contract ID', 'Date', 'Price']
    print(tabulate(table, headers=headers))


if __name__ == '__main__':
    logging.basicConfig(level=logging.DEBUG,
                        format='%(asctime)s, %(message)s',
                        filename='debug.log'
                        )

    console = logging.StreamHandler()
    console.setLevel(logging.INFO)
    formatter = logging.Formatter('%(message)s')
    console.setFormatter(formatter)
    logging.getLogger('').addHandler(console)
    logging.getLogger('ib_insync').setLevel(logging.WARNING)

    log = logging.getLogger(__name__)

    main()

    parser = ArgumentParser()
    parser.add_argument('command')
    args = parser.parse_args()

    if args.command == 'run':
        main()

    elif args.command == 'report':
        show_report()
