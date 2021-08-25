import logging
from ib_insync import *
from datetime import datetime, timedelta

logging.basicConfig(format='%(message)s')
log = logging.getLogger('pre_prod_algo')
log.setLevel(logging.DEBUG)


def connect():
    """
    Connect to API, switch to delayed info (for trial account)
    :return:
    """

    ib = IB()
    ib.connect()
    if ib.isConnected():
        log.debug('Connection established successfully.')
    else:
        log.debug('Connection error, please try again.')

    ib.reqMarketDataType(3)  # Needed only for trial account - delete or comment out when using with real acc

    return ib


ib = connect()  # careful - global var


def get_date():
    """
    Set the date for the next option
    :return:
    """

    available_option_days = [0, 2, 4]
    option_date = datetime.today()  # the function always starts from today,
                                    # but it is needed to start from today once and then
                                    # store the last value in a var for the following orders
    one_day_increment = timedelta(days=1)

    while True:
        if option_date.weekday() in available_option_days:
            date_formatted = str(option_date.date()).replace('-', '')
            return date_formatted, option_date
        else:
            option_date = option_date + one_day_increment


set_date = get_date()[0]  # careful - global var
full_date = get_date()[1]  # careful - global var, still doesn't solve the issue with starting from today


def get_reference_price():
    """
    Get the future price for reference and check if it is suitable for the trade
    :return: int strike_price, ContFuture instance reference_future_contract
    """

    while True:
        reference_futures_contract = ContFuture('ES', 'GLOBEX')
        ib.qualifyContracts(reference_futures_contract)
        reference_futures_ticker = ib.reqMktData(reference_futures_contract)
        ib.sleep(2)
        reference_price = reference_futures_ticker.close
        reference_price_rounded = 5 * round(reference_price / 5)
        if reference_price_rounded > reference_price:  # be sure to remove +10 in production
            strike_price = reference_price_rounded
            log.debug(f'Good to go, price is {reference_price}')
            return strike_price, reference_futures_contract
        else:
            log.debug('Reference price does not allow to trade now, retrying')
            ib.sleep(5)


def set_option_trade():
    """
    Actually place a trade following the needed conditions
    :return:
    """

    strike_price = get_reference_price()[0]
    date = set_date
    option_contract = FuturesOption('ES', date, strike_price, 'C', 'GLOBEX')
    ib.qualifyContracts(option_contract)
    option_contract_order = MarketOrder('SELL', 1)
    option_trade = ib.placeOrder(option_contract, option_contract_order)
    log.debug('Order placed')
    ib.sleep(3)
    option_status = option_trade.orderStatus.status
    log.debug(option_status) #fills instantly - most probably shouldn't be
    while option_status != 'Filled':  # the docs say option_status will be updating automatically, do I need while loop
        log.debug(option_status)
        ib.sleep(5)
    else:
        pass
        #set_stop_loss()


def set_stop_loss():  # the last thing that needs to be tested
    """
    Set a stop-loss function for the existing order
    :return:
    """
    reference_futures_contract = get_reference_price()[1]
    strike_price = get_reference_price()[0]
    stop_loss_price = strike_price + 4.75
    sl_price_condition = PriceCondition(
        price=stop_loss_price,
        conId=reference_futures_contract.conId,
        exch=reference_futures_contract.exchange
    )

    print(sl_price_condition)

    date = set_date  # leads to global var, needs checkup
    sl_contract = FuturesOption('ES', date, strike_price, 'C', 'GLOBEX')
    ib.qualifyContracts(sl_contract)

    option_contract_order = MarketOrder('BUY', 1)  # set the same number of contracts as
                                                   # were in "SELL" side to close the trade correctly
    option_contract_order.conditions.append(sl_price_condition)
    ib.placeOrder(sl_contract, option_contract_order)  # price condition doesn't work correctly - it places order anyway
                                                       # but needs to wait for a specific futures price


def runner():
    check_orders = ib.reqOpenOrders()
    while True:
        if not check_orders:
            set_option_trade()
            break
        else:
            log.debug(f'There is an open order at the moment: {check_orders}')
            ib.sleep(30)


if __name__ == '__main__':
    runner()
    # after testing that stop loss works correctly, need to loop the whole thing
    # maybe just 'while' in this section?
    # need to create runner
