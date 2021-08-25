algo-trader v1.0

Please read this file before running the script.


First of all, you will need TWS installed on your computer.
You can get it here https://www.interactivebrokers.com/en/index.php?f=14099#tws-software
Then you can find instructions on how to set it up here https://algotrading101.com/learn/interactive-brokers-python-api-native-guide/#how-to-set-up-the-ib-native-python-api-on-windows

Once it is done, go to the folder where the script and the current file are found.
From there, you will need to install required packages - the command will be 'pip install -r requirements.txt'

Don't forget to actually run TWS before running the script.

When the abovementioned steps are done, it is time to get to the script itself.
The script consists of several functions as well as some global variables and extra settings.

First of all we set up logging for the future use.
Next we have the first function named 'connect' which establishes connection to the API. Then this connection is
saved in a global variable which is passed to other functions.
Next function is 'get_date', which sets the date for the first future option to be traded,
and also will iterate to set the dates for the next trades.
The function which comes next is 'get_reference_price', which will get the price of the futures contract, return it,
and as well return the information about this contract for future use.
Next function is 'set_option_trade', which actually places a trade of an option needed. It gets date from 'set_date' variable,
which is brought by 'get_date' function, and gets price from 'get_reference_price'. Other settings for the option can be changed
right in the script. Once everything is set, this function fires an order (side and number of contracts can also be changed
in the script). After the order is placed, this function monitors it till it is filled, and then activates the next
'stop-loss' function.
This next function checks the futures price and if it goes too high (following the given algorithm), the function
places an opposite order to close this trade.
The last function will be actually what runs the whole thing - it checks for the open contracts and if there are none,
it will run the script to place the next order. And it will do nothing if there is an open order already.
