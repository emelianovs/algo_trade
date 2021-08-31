Please read this before running the script.

First of all, you will need to install and set up TWS on your computer.
The next step will be to install modules needed for the script, to do it please run the following command
in the terminal/shell, while being in the same folder with the script(and requirements.txt file):

pip install -r requirements.txt

Once the previous steps are done, it's time to run the script.
The command to run the script from your terminal/cmd/shell
will be as following (you need to be in the folder with the script):

python3 algo_v1_0.py run

-- this will run the algorithm script itself.

There is also a different command:

python3 algo_v1_0.py report

-- which will show recent trades.

The script also runs logs - some important messages will appear in the terminal,
more detailed info will e written to the file 'debug.log' in the same directory as the script itself.

The variables at the beginning of the script, written in CAPITALS,
are for easier and faster adjustment of some aspects of the script.
Everything else can also be modified if need be.