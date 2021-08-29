import logging
import math

from typing import Tuple
from ib_insync import *
from tenacity import *
from datetime import datetime, timedelta
from argparse import ArgumentParser
from tabulate import tabulate


OPEN_ORDERS_CHECK_PERIOD = timedelta(minutes=1)
ORDER_PLACE_MAX_DATE = timedelta(days=30)
TRADING_DAYS_OF_WEEK = [0, 2, 4]
STRIKE_PRICE_WAIT_PERIOD = 10
STRIKE_PRICE_MAX_WAIT_PERIOD = 100
GENERIC_WAIT_TIME = 2
CONTRACTS_NUMBER = 1


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

    ib.reqMarketDataType(3)  # Needed only for trial account - delete or comment out when using with real acc
    ib.client.setServerLogLevel(logLevel=1) # needs more detailed testing
    return ib


ib = connect()


def get_latest_contract() -> Contract:
    """
    Gets the information about the latest traded contract
    :return: Contract
    """
    latest_contract = ib.trades()
    if latest_contract:
        return latest_contract[-1].contract
    else:
        log.debug('No latest contract.')


@retry(wait=wait_fixed(2)) # critical issue here
def get_available_date() -> str:
    """
    Find the next available date for trade
    :return: str of specific data format
    """
    log.info('Looking for a suitable trade date...')
    latest_contract = get_latest_contract()
    if not latest_contract is None:
        latest_date_string = latest_contract.lastTradeDateOrContractMonth
        latest_date_date = datetime.strptime(latest_date_string, '%Y%m%d').date()
        log.info(f'Latest contract date is {latest_date_date}')
    else:
        latest_date_date = datetime.today()
        log.info(f'No latest contract found, starting with today date')

    for i in range(1, 7):
        candidate_date = latest_date_date + timedelta(days=i)
        if candidate_date > datetime.today() + ORDER_PLACE_MAX_DATE:
            raise NoSuitableDate('No suitable days left. Waiting till the next day.')
        else:
            if candidate_date.weekday() in TRADING_DAYS_OF_WEEK:
                candidate_date_formatted = str(candidate_date).replace('-', '')
                log.info(f'The closest possible date is {candidate_date}')
                return candidate_date_formatted

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
        ib.sleep(GENERIC_WAIT_TIME)
        reference_price = reference_futures_ticker.close

    for i in range(int(STRIKE_PRICE_MAX_WAIT_PERIOD / STRIKE_PRICE_WAIT_PERIOD)):
        reference_price_rounded = 5 * round(reference_price / 5)
        if reference_price_rounded + 10 > reference_price:  # don't forget to remove + 10 in production
            log.info(f'Good to go, reference futures price is {reference_price_rounded}')
            return reference_futures_contract, reference_price_rounded
        log.info('The current price does not allow to trade now, waiting.')
        ib.sleep(STRIKE_PRICE_WAIT_PERIOD)
    raise NoSuitablePrice('Can not find suitable price')


def create_and_trade_contract(date: str, strike_price: float) -> Trade:
    """

    :param date: Date, available for trading, from get_available_date()
    :param strike_price: Price of futures contract, used as a reference price and strike price for Future Option,
    from create_reference() function
    :return: a Trade object
    """
    option_contract = FuturesOption('ES', date, strike_price, 'C', 'GLOBEX')
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

    while True:
        status = option_trade.orderStatus.status
        if status == 'Filled':
            set_stop_loss(date, reference_contract, strike_price)
            break

        ib.sleep(GENERIC_WAIT_TIME)


def calculate_stop_loss_price(price: int) -> float:
    """
    Calculates stop-loss price by given formula
    :param price: rounded price from get_reference function
    :return: float of stop-loss price
    """
    stop_loss_price = price + 4.75
    return stop_loss_price


def set_stop_loss(date, reference_contract, strike_price):  # the last thing that needs to be tested
    """
    Sets an order on the opposite side to close the trade, once the reference futures price gets to a specific value
    :param date: date of the short contract from get_available_date()
    :param reference_contract: data of the reference futures contract from get_reference function
    :param strike_price: strike price of the actual Futures Option contract(same as reference futures contract price)
    :return:
    """
    stop_loss_price = calculate_stop_loss_price(strike_price)
    sl_price_condition = PriceCondition(
        price=stop_loss_price,
        conId=reference_contract.conId,
        exch=reference_contract.exchange
    )

    print(sl_price_condition)

    sl_contract = FuturesOption('ES', date, strike_price, 'C', 'GLOBEX')
    ib.qualifyContracts(sl_contract)

    option_contract_order = MarketOrder('BUY', CONTRACTS_NUMBER)
    option_contract_order.conditions.append(sl_price_condition)
    ib.placeOrder(sl_contract, option_contract_order)   # supposedly price condition doesn't work correctly -
                                                        # it places order anyway
                                                        # but needs to wait for a specific futures price


def main():
    """
    Checks for open orders, and if there are none, runs the process of setting a trade
    :return:
    """
    while True: # find a way to set limit
        check_orders = ib.reqOpenOrders()
        if check_orders:
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

    log = logging.getLogger(__name__)

    parser = ArgumentParser()
    parser.add_argument('command')
    args = parser.parse_args()

    if args.command == 'run':
        main()

    elif args.command == 'report':
        show_report()
