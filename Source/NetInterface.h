#pragma once

#include "Util.h"

enum NetInterfaceErr
{
	NET_OK = 0,
	NET_ERR_RESOLVE,	// bad IP / no DNS entry
	NET_ERR_SOCKET,
	NET_ERR_CONNECT,
	NET_ERR_SEND,
	NET_ERR_RECV,
	NET_ERR_TIMEOUT,
	NET_NOT_OPEN,
	NET_ALREADY_OPEN,
};

class NetInterface
{
	SOCKET sock{ INVALID_SOCKET };
	bool open{ false };

public:
	static int InitNet();
	static void CloseNet();

	static int ResolveHost(const char* host, ushort port, struct sockaddr_in* remote);

	NetInterface() {}

	/* 
	 * NOTE: SO_RCVTIMEO is a DWORD of ms on windows but a timeval on POSIX
	 * and blocking connect() ignores it 
	 */
	int Open(struct sockaddr_in* remote, ushort timeout_ms);
	
	int Send(byte* buf, size_t len);

	/* until peer closes or timeout, TODO: exact read once size field is confirmed */
	int RecvAll(byte* buf, size_t cap, size_t* len);

	int Close();

	~NetInterface();
};
