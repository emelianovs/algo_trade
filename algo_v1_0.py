import logging
import math

from typing import Tuple, Optional
from ib_insync import IB, Contract, ContFuture, MarketOrder, FuturesOption, Trade, PriceCondition
from tenacity import retry, wait_fixed
from datetime import datetime, timedelta, date
from argparse import ArgumentParser
from tabulate import tabulate


OPEN_ORDERS_CHECK_PERIOD = timedelta(minutes=1)
ORDER_PLACE_MAX_DATE = timedelta(days=30)
TRADING_DAYS_OF_WEEK = [0, 2, 4]
STRIKE_PRICE_WAIT_PERIOD = timedelta(seconds=10)
STRIKE_PRICE_MAX_WAIT_PERIOD = timedelta(seconds=100)
GENERIC_WAIT_TIME = timedelta(seconds=2)
CONTRACTS_NUMBER = 1
TRIAL_ACCOUNT = True
BANK_HOLIDAYS_DATES = [date(2021, 9, 6), date(2021, 10, 10), date(2021, 11, 11),
                       date(2021, 11, 24), date(2021, 12, 26)]
PERIOD_TO_WAIT_FOR_THE_NEXT_DAY = timedelta(hours=1)


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


def get_latest_contract() -> Optional[Contract]:
    """
    Gets the information about the latest traded contract
    :return: Contract
    """
    contracts = ib.trades()
    if contracts:
        return contracts[-1].contract
    else:
        log.debug('No latest contract.')


@retry(wait=wait_fixed(int(PERIOD_TO_WAIT_FOR_THE_NEXT_DAY.total_seconds())))
def get_available_date() -> date:
    """
    Find the next available date for trade
    :return: date in datetime format
    """
    log.info('Looking for a suitable trade date...')
    latest_contract = get_latest_contract()
    latest_date = datetime.today().date()
    if latest_contract:
        latest_date_string = latest_contract.lastTradeDateOrContractMonth
        latest_date_from_contract = datetime.strptime(latest_date_string, '%Y%m%d').date()
        if latest_date_from_contract > latest_date:
            latest_date = latest_date_from_contract
    log.info(f'Searching for suitable date, starting from {latest_date}')

    for i in range(1, 7):
        if latest_date == datetime.today().date():
            candidate_date = latest_date
        else:
            candidate_date = latest_date + timedelta(days=i)
            if candidate_date > datetime.today().date() + ORDER_PLACE_MAX_DATE:
                raise NoSuitableDate('No suitable days left. Waiting till the next day.')

        if candidate_date in BANK_HOLIDAYS_DATES:
            log.info(f'{candidate_date} is a bank holiday, moving to the next day. ')
            return candidate_date + timedelta(days=1)

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
    reference_price = reference_futures_ticker.close
    while math.isnan(reference_price):
        ib.sleep(GENERIC_WAIT_TIME.total_seconds())
        reference_price = reference_futures_ticker.close

    for i in range(int(STRIKE_PRICE_MAX_WAIT_PERIOD / STRIKE_PRICE_WAIT_PERIOD)):
        reference_price = reference_futures_ticker.close
        reference_price_rounded = 5 * round(reference_price / 5)
        if TRIAL_ACCOUNT:
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

    option_contract_order = MarketOrder('SELL', CONTRACTS_NUMBER)
    option_trade = ib.placeOrder(option_contract, option_contract_order)

    return option_trade


def set_option_trade():
    """
    Sets a trade via create_and_trade_contract function, waits for it to get filled and once it does, sets a stop-loss
    :return:
    """

    reference_contract, strike_price = create_reference()
    date = get_available_date()

    option_trade = create_and_trade_contract(date, strike_price)
    log.info(f'Order placed: {option_trade.order}')

    option_status = option_trade.orderStatus.status
    while option_status != 'Filled':
        ib.sleep(GENERIC_WAIT_TIME.total_seconds())
        option_status = option_trade.orderStatus.status
    set_stop_loss(date, reference_contract, strike_price)


def calculate_stop_loss_price(price: int) -> float:
    """
    Calculates stop-loss price by given formula
    :param price: rounded price from get_reference function
    :return: float of stop-loss price
    """
    stop_loss_price = price + 4.75
    return stop_loss_price


def set_stop_loss(date, reference_contract, strike_price):
    """
    Sets an order on the opposite side to close the trade, once the reference futures price gets to a specific value
    :param date: date of the short contract from get_available_date()
    :param reference_contract: data of the reference futures contract from get_reference function
    :param strike_price: strike price of the actual Futures Option contract(same as reference futures contract price)
    :return:
    """
    date_formatted = date.strftime('%Y%m%d')
    stop_loss_price = calculate_stop_loss_price(strike_price)
    sl_price_condition = PriceCondition(
        price=stop_loss_price,
        conId=reference_contract.conId,
        exch=reference_contract.exchange
    )

    sl_contract = FuturesOption('ES', date_formatted, stop_loss_price, 'C', 'GLOBEX')
    ib.qualifyContracts(sl_contract)

    option_contract_order = MarketOrder('BUY', CONTRACTS_NUMBER)
    option_contract_order.conditions.append(sl_price_condition)
    ib.placeOrder(sl_contract, option_contract_order)


def main():
    """
    Checks for open orders, and if there are none, runs the process of setting a trade
    :return:
    """
    while True:
        if ib.reqOpenOrders():
            log.info('There is an open order currently, can not place more')
            ib.sleep(
                int(OPEN_ORDERS_CHECK_PERIOD.total_seconds())
            )
        else:
            set_option_trade()


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
