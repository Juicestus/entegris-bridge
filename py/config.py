# point these at the box under test, the defaults match the mock so it runs with no edits
HOST = "127.0.0.1"
PORT = 4002

# goes in the header, how long the NCS waits on the serial side before answering with a timeout code
TIMEOUT_MS = 5000

# extra time our socket waits on top of TIMEOUT_MS, see NCSClient.transact for why
SOCKET_MARGIN_MS = 1000
